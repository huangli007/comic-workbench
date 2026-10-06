"""局部重绘（Panel Inpaint）—— 无需 mask 模型的实用方案。

本机现状：mflux 的 `mflux-generate-fill` 需要 FLUX.1 Fill 权重（未下载），
而 FLUX.2 Klein 的 edit 通道只吃图片不吃 mask。所以采用「裁剪—条件重绘—羽化回贴」：

    选区(rect) + 上下文边距(margin)
      ↓ 裁剪成 crop
    flux2-edit 以 crop 为参考图重绘（保持画风/角色连续）
      ↓ 缩放回 crop 尺寸
    取回内区，羽化后贴回原图
      ↓
    新候选（stage=inpaint，可与原图对比后再决定是否采用）

以后若下载了 Fill 权重，只需在 config 里给出路径，`use_fill_model` 会切到真 mask 重绘。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from sqlmodel import Session, select

from .. import config
from ..models import Candidate, Panel, Project
from ..providers import GenRequest, get_provider
from .character import reference_images


def _match_vae_size(v: int) -> int:
    """对齐到 32 的倍数（扩散模型 VAE 要求）。"""
    return max(64, (int(v) // 32) * 32)


def blend_patch(
    base: Image.Image,
    patch: Image.Image,
    rect: tuple[int, int, int, int],
    feather: int = 14,
) -> None:
    """把 patch 羽化后贴回 base 的 rect（就地修改）。"""
    x, y, w, h = rect
    patch = patch.convert("RGB").resize((w, h), Image.LANCZOS)

    f = max(1, min(feather, w // 3, h // 3))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rectangle([f, f, w - f, h - f], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(f / 2))
    base.paste(patch, (x, y), mask)


def _source_image(session: Session, panel: Panel, candidate_id: str = "") -> tuple[Image.Image, Candidate | None]:
    """取当前选中的候选（或指定候选 / 最新候选）作为重绘底图。"""
    cand: Candidate | None = None
    if candidate_id:
        cand = session.get(Candidate, candidate_id)
    if cand is None and panel.selected_candidate_id:
        cand = session.get(Candidate, panel.selected_candidate_id)
    if cand is None:
        cands = session.exec(
            select(Candidate).where(Candidate.panel_id == panel.id).order_by(Candidate.created_at)  # type: ignore
        ).all()
        cand = cands[-1] if cands else None
    if cand is None or not cand.path:
        raise RuntimeError("该分镜格还没有可用图像，请先生成候选")
    p = config.PROJECTS_DIR / cand.path
    if not p.exists() or p.stat().st_size == 0:
        raise RuntimeError(f"候选图不存在: {p}")
    return Image.open(p).convert("RGB"), cand


def inpaint_region(
    session: Session,
    panel: Panel,
    rect_norm: dict,
    prompt: str,
    provider_name: str = "",
    steps: int | None = None,
    seed: int | None = None,
    margin_ratio: float = 0.25,
    feather: int = 14,
    candidate_id: str = "",
) -> dict:
    """rect_norm: {x, y, w, h} 0~1 相对坐标。返回结果信息 dict。"""
    project = session.get(Project, panel.project_id)
    base, src_cand = _source_image(session, panel, candidate_id)
    W, H = base.size

    # 归一化 → 像素（并做边界钳制）
    x = int(round(rect_norm.get("x", 0) * W))
    y = int(round(rect_norm.get("y", 0) * H))
    w = int(round(rect_norm.get("w", 0.3) * W))
    h = int(round(rect_norm.get("h", 0.3) * H))
    x = max(0, min(x, W - 8))
    y = max(0, min(y, H - 8))
    w = max(32, min(w, W - x))
    h = max(32, min(h, H - y))

    # 带上下文的裁剪框
    m = int(max(w, h) * margin_ratio)
    cx0, cy0 = max(0, x - m), max(0, y - m)
    cx1, cy1 = min(W, x + w + m), min(H, y + h + m)
    crop = base.crop((cx0, cy0, cx1, cy1))
    cw, ch = crop.size
    gen_w, gen_h = _match_vae_size(cw), _match_vae_size(ch)

    work_dir = config.project_dir(project.id) / "panels" / panel.id / "inpaint"
    work_dir.mkdir(parents=True, exist_ok=True)
    crop_path = work_dir / f"crop_{x}_{y}_{w}_{h}.png"
    crop.resize((gen_w, gen_h), Image.LANCZOS).save(crop_path)

    # 组装提示词：用户描述 + 画风/角色连续性约束
    chars = []
    for cid in panel.char_ids():
        from ..models import Character
        ch_ = session.get(Character, cid)
        if ch_:
            chars.append(ch_)
    refs = [crop_path]
    for ch_ in chars:
        if len(refs) >= config.MAX_REFERENCE_IMAGES:
            break
        for r in reference_images(project.id, ch_, project.style):
            if r not in refs:
                refs.append(r)
    refs = refs[: config.MAX_REFERENCE_IMAGES]

    style = config.STYLE_PRESETS.get(project.style, project.style)
    full_prompt = (
        "Local patch repaint of a manga panel. "
        f"Content of this region: {prompt.strip() or panel.action}. "
        "Match the exact art style, line weight, screentone shading and lighting of the "
        f"surrounding image so the patch blends seamlessly. {style}. "
        "IMPORTANT: no text, no lettering, no speech bubbles anywhere in the image"
    )
    if chars:
        names = ", ".join(c.name for c in chars)
        full_prompt = (
            f"Keep {names} identical to the reference images (same face, hairstyle, "
            "hair length and outfit). THE REFERENCE IMAGES ARE AUTHORITATIVE for "
            "appearance. "
        ) + full_prompt

    from .prompt import apply_style_directive
    full_prompt = apply_style_directive(project.style, full_prompt)
    provider = get_provider(provider_name or ("mlx-edit" if refs else "mlx"))
    _tok = f"{int(__import__('time').time() * 1000) % 1000000:06d}"
    out_path = work_dir / f"inpaint_{(seed or panel.seed)}_{x}_{y}_{_tok}.png"
    req = GenRequest(
        prompt=full_prompt,
        output_path=out_path,
        width=gen_w,
        height=gen_h,
        steps=steps or config.DRAFT_STEPS,
        seed=seed or panel.seed,
        reference_images=refs,
    )
    provider.generate(req)

    # 生成结果 → 缩回 crop 尺寸 → 取内区 → 羽化贴回
    patched = Image.open(out_path).convert("RGB").resize((cw, ch), Image.LANCZOS)
    inner = patched.crop((x - cx0, y - cy0, x - cx0 + w, y - cy0 + h))
    result = base.copy()
    blend_patch(result, inner, (x, y, w, h), feather=feather)

    out_dir = config.project_dir(project.id) / "panels" / panel.id / "inpaint"
    final_path = out_dir / f"inpaint_full_{(seed or panel.seed)}_{x}_{y}_{_tok}.png"
    result.save(final_path)

    cand = Candidate(
        panel_id=panel.id,
        path=str(final_path.relative_to(config.PROJECTS_DIR)),
        prompt=full_prompt,
        seed=seed or panel.seed,
        provider=provider.name,
        model=config.MLX_MODEL if provider.name.startswith("mlx") else "",
        stage="inpaint",
        params=_json({
            "rect": {"x": x / W, "y": y / H, "w": w / W, "h": h / H},
            "rect_px": [x, y, w, h],
            "source_candidate": src_cand.id if src_cand else "",
            "margin_ratio": margin_ratio,
            "feather": feather,
            "size": [W, H],
            "references": [str(r.relative_to(config.PROJECTS_DIR)) for r in refs],
        }),
    )
    session.add(cand)
    panel.status = "review"
    session.add(panel)
    session.commit()
    session.refresh(cand)
    return {
        "candidate": cand.model_dump(),
        "url": f"/files/{cand.path}",
        "rect_px": [x, y, w, h],
    }


def _json(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)
