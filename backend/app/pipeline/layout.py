"""Layout Engine — 自动页面排版（蓝图 §16：布局不交给 AI）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from sqlmodel import Session, select

from .. import config
import json

from ..models import Candidate, Page, Panel, Project
from . import bubbles

# 模板：面板数 → 槽位 (x0, y0, x1, y1) 相对坐标（0~1，不含页边距）
TEMPLATES: dict[str, list[tuple[float, float, float, float]]] = {
    "1x1":  [(0.0, 0.0, 1.0, 1.0)],
    "1x2":  [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 1.0)],
    "2x2":  [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 1.0, 0.5),
             (0.0, 0.5, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0)],
    "big2": [(0.0, 0.0, 1.0, 0.62), (0.0, 0.62, 0.5, 1.0), (0.5, 0.62, 1.0, 1.0)],
    "full": [(0.0, 0.0, 1.0, 1.0)],
}

AUTO_BY_COUNT = {1: "1x1", 2: "1x2", 3: "big2", 4: "2x2"}


def pick_template(page: Page, panels: list[Panel]) -> str:
    if page.template and page.template in TEMPLATES:
        # 手动模板但面板超出槽位 → 退化 auto
        if len(panels) <= len(TEMPLATES[page.template]) or page.template in ("1x1", "full"):
            return page.template
    return AUTO_BY_COUNT.get(len(panels), "2x2")


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """等比缩放后居中裁剪到目标尺寸（cover）。"""
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    x0 = (nw - w) // 2
    y0 = (nh - h) // 2
    return img.crop((x0, y0, x0 + w, y0 + h))


def _slot_rect(slot: tuple[float, float, float, float], full: bool = False) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = slot
    if full:  # 满版出血
        return (int(x0 * config.PAGE_W), int(y0 * config.PAGE_H),
                int(x1 * config.PAGE_W), int(y1 * config.PAGE_H))
    m, g = config.PAGE_MARGIN, config.GUTTER
    inner_w = config.PAGE_W - 2 * m
    inner_h = config.PAGE_H - 2 * m
    return (
        int(m + x0 * inner_w + (g / 2 if x0 > 0 else 0)),
        int(m + y0 * inner_h + (g / 2 if y0 > 0 else 0)),
        int(m + x1 * inner_w - (g / 2 if x1 < 1 else 0)),
        int(m + y1 * inner_h - (g / 2 if y1 < 1 else 0)),
    )


def _placeholder(w: int, h: int, text: str, font_preset: str = "") -> Image.Image:
    img = Image.new("RGB", (w, h), (228, 226, 220))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(150, 148, 140), width=2)
    font = bubbles._load_font(max(16, min(24, w // 24)), font_preset)
    lines = bubbles._wrap_cjk(text or "（未生成）", font, w - 40)
    ty = h // 2 - len(lines) * 18
    for line in lines:
        tw = font.getlength(line)
        d.text(((w - tw) / 2, ty), line, font=font, fill=(130, 128, 120))
        ty += 26
    return img


def render_page(
    session: Session,
    page: Page,
    font_preset: str = "",
    size_scale: float = 1.0,
    lang: str = "",
) -> Path:
    """渲染一页漫画：排版 + 图像 + 中文气泡。返回 PNG 路径。

    font_preset: 气泡字体预设（heiti/songti/light/unicode），留空用项目设置或默认黑体
    size_scale:  气泡字号缩放（0.5~2.0）
    """
    project = session.get(Project, page.project_id)
    if not font_preset:
        font_preset = (getattr(project, "bubble_style", "") or config.DEFAULT_FONT_PRESET)
    if size_scale is None or size_scale <= 0:
        size_scale = float(getattr(project, "font_scale", 1.0) or 1.0)
    panels = sorted(
        session.exec(select(Panel).where(Panel.page_id == page.id)).all(),
        key=lambda p: p.index,
    )
    template = pick_template(page, panels)
    slots = TEMPLATES[template]
    full_bleed = template == "full"

    canvas = Image.new("RGB", (config.PAGE_W, config.PAGE_H), (252, 250, 246) if not full_bleed else (255, 255, 255))
    d = ImageDraw.Draw(canvas)

    for i, panel in enumerate(panels):
        slot = slots[i % len(slots)]
        rect = _slot_rect(slot, full=full_bleed)
        rx0, ry0, rx1, ry1 = rect
        w, h = rx1 - rx0, ry1 - ry0

        img: Image.Image | None = None
        # 优先选中候选 → 最新 final → 最新 draft
        cand: Candidate | None = None
        if panel.selected_candidate_id:
            cand = session.get(Candidate, panel.selected_candidate_id)
        if cand is None:
            cands = session.exec(
                select(Candidate).where(Candidate.panel_id == panel.id)
                .order_by(Candidate.created_at)  # type: ignore
            ).all()
            finals = [c for c in cands if c.stage == "final"]
            cand = (finals or cands or [None])[-1] if cands else None
        if cand and cand.path:
            p = config.PROJECTS_DIR / cand.path
            if p.exists() and p.stat().st_size > 0:
                img = _cover_crop(Image.open(p).convert("RGB"), w, h)

        if img is None:
            img = _placeholder(w, h, panel.action, font_preset)

        canvas.paste(img, (rx0, ry0))
        # 面板边框
        d.rectangle([rx0, ry0, rx1 - 1, ry1 - 1], outline=(15, 15, 15), width=config.BORDER_W)

        # 对白气泡（按语言取译文，缺失回退中文原文）
        dialogues = panel.dialogues()
        if lang:
            try:
                tr = json.loads(panel.dialogue_translations or "{}").get(lang) or []
                if len(tr) == len(dialogues) and tr:
                    dialogues = tr
            except Exception:
                pass
        for di, item in enumerate(dialogues):
            bubbles.draw_dialogue(canvas, item, rect, default_index=di,
                                 font_preset=font_preset, size_scale=size_scale)

    out_dir = config.project_dir(page.project_id) / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 多语言页面带语言后缀，避免互相覆盖（中文原文无后缀）
    suffix = f"_{lang}" if lang else ""
    out = out_dir / f"page_{page.index:03d}{suffix}.png"
    canvas.save(out)
    return out
