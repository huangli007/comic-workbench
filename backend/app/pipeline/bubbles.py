"""气泡系统 — Pillow 绘制中文对白气泡（蓝图 §14/§15：对白不进图像模型）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import config

_font_cache: dict[tuple[int, str], ImageFont.FreeTypeFont] = {}

# 中文禁则：这些标点不允许出现在行首（尾随禁则）
NO_LINE_START = set("、。，．！？；：）》」』】〉…—～!?,.;:)]}")
# 这些标点不允许出现在行尾（头随禁则）
NO_LINE_END = set("（《「『【〈([{")


def _load_font(size: int, preset: str = "") -> ImageFont.FreeTypeFont:
    preset = preset or config.DEFAULT_FONT_PRESET
    key = (size, preset)
    if key in _font_cache:
        return _font_cache[key]
    for path in config.FONT_PRESETS.get(preset, config.FONT_PRESETS[config.DEFAULT_FONT_PRESET]):
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _wrap_cjk(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_w: int,
    no_line_start: set[str] | None = None,
    no_line_end: set[str] | None = None,
) -> list[str]:
    """CJK 逐字换行 + 禁则处理：
    - 行首不出现 。，！？」』 等收尾标点（挤在上一行末尾）
    - 行尾不出现 「《（ 等起始标点（推到下一行）
    - ASCII 单词不拆
    """
    nls = NO_LINE_START if no_line_start is None else no_line_start
    nle = NO_LINE_END if no_line_end is None else no_line_end

    lines: list[str] = []
    line = ""
    ascii_buf = ""

    def flush_carry(tail: str = "") -> None:
        """把当前行（含 ASCII 缓冲与可能的尾随标点）落盘。"""
        nonlocal line, ascii_buf
        lines.append(line + ascii_buf + tail)
        line = ""
        ascii_buf = ""

    for ch in text:
        if ch == "\n":
            flush_carry()
            continue
        if ord(ch) < 128 and ch not in "，。！？；：、":
            ascii_buf += ch
            continue

        candidate = line + ascii_buf + ch
        if font.getlength(candidate) > max_w and (line or ascii_buf):
            # 禁则①：该字符不允许出现在行首 → 让它挤在上一行末尾
            if ch in nls and (line or ascii_buf):
                flush_carry(ch)
                continue
            # 禁则②：行尾是起始标点 → 把它一起挪到下一行
            if (line + ascii_buf)[-1:] in nle:
                moved = (line + ascii_buf)[-1]
                head = (line + ascii_buf)[:-1]
                lines.append(head)
                line = moved + ch
                ascii_buf = ""
                continue
            flush_carry()
            line = ch
            continue
        line += ascii_buf + ch
        ascii_buf = ""

    if line or ascii_buf:
        flush_carry()
    return lines or [""]


def measure_bubble(
    text: str,
    font_size: int = 26,
    max_text_w: int = 260,
    pad: int = 14,
    line_gap: int = 6,
    font_preset: str = "",
) -> tuple[int, int, list[str]]:
    font = _load_font(font_size, font_preset)
    lines = _wrap_cjk(text, font, max_text_w)
    line_h = font_size + line_gap
    w = max((font.getlength(l) for l in lines), default=0) + pad * 2
    h = line_h * len(lines) + pad * 2
    return int(w), int(h), lines


def _draw_speech(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, outline, width=3):
    x0, y0, x1, y1 = box
    d.ellipse(box, fill=fill, outline=outline, width=width)
    # 尾巴（指向说话者，默认左下）
    cx = x0 + int((x1 - x0) * 0.3)
    d.polygon(
        [(cx, y1 - 4), (cx + 26, y1 - 2), (cx - 14, y1 + 30)],
        fill=fill, outline=outline,
    )
    d.polygon([(cx + 2, y1 - 6), (cx + 22, y1 - 4), (cx - 8, y1 + 24)], fill=fill)


def _draw_thought(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, outline, width=3):
    x0, y0, x1, y1 = box
    d.ellipse(box, fill=fill, outline=outline, width=width)
    cx = x0 + int((x1 - x0) * 0.4)
    cy = y1
    for r in (9, 6, 4):
        cy += r + 8
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=2)


def _draw_shout(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, outline, width=3):
    import math
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    n = 20
    for i in range(n * 2):
        ang = math.pi * i / n
        frac = 1.0 if i % 2 == 0 else 0.88
        pts.append((cx + rx * frac * math.cos(ang), cy + ry * frac * math.sin(ang)))
    d.polygon(pts, fill=fill, outline=outline, width=width)
    # 尾巴
    d.polygon(
        [(cx - rx * 0.5, cy + ry * 0.9), (cx - rx * 0.3, cy + ry * 0.95), (cx - rx * 0.9, cy + ry + 34)],
        fill=fill, outline=outline,
    )


def _draw_whisper(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, outline, width=3):
    x0, y0, x1, y1 = box
    d.ellipse(box, fill=fill, outline=outline, width=width)
    # 虚线内圈
    import math
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2 - 7, (y1 - y0) / 2 - 7
    n_dashes = 18
    for i in range(n_dashes):
        a0 = 2 * math.pi * i / n_dashes
        a1 = a0 + 2 * math.pi / n_dashes * 0.5
        d.arc(
            [cx - rx, cy - ry, cx + rx, cy + ry],
            start=math.degrees(a0), end=math.degrees(a1),
            fill=outline, width=2,
        )


def _draw_caption(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, outline, width=3):
    d.rounded_rectangle(box, radius=8, fill=fill, outline=outline, width=width)


_BUBBLE_SHAPES = {
    "speech": _draw_speech,
    "thought": _draw_thought,
    "shout": _draw_shout,
    "whisper": _draw_whisper,
    "narration": _draw_caption,
    "caption": _draw_caption,
}


def draw_dialogue(
    base: Image.Image,
    item: dict,
    panel_box: tuple[int, int, int, int],
    default_index: int = 0,
    font_preset: str = "",
    size_scale: float = 1.0,
) -> None:
    """在 panel 上绘制一条对白。item: {speaker,text,type,x,y}，x/y 为 0~1 相对坐标。

    文字固定使用中文字体（config.FONT_PRESETS），标点做禁则处理，确保中文不出现方块/断行错乱。
    """
    text = str(item.get("text", "")).strip()
    if not text:
        return
    dtype = item.get("type", "speech")
    px0, py0, px1, py1 = panel_box
    pw, ph = px1 - px0, py1 - py0

    font_size = int(max(20, pw * 0.032) * max(0.5, min(2.0, size_scale)))
    max_text_w = int(pw * 0.42)
    # 喊叫气泡是锯齿形，内接面积小于外框，测量时留更多余量，否则文字会溢出
    measure_pad = 30 if dtype == "shout" else 14
    bw, bh, lines = measure_bubble(
        text, font_size=font_size, max_text_w=max_text_w,
        pad=measure_pad, font_preset=font_preset,
    )

    # 位置：默认在上方按序错开
    x = item.get("x")
    y = item.get("y")
    if not isinstance(x, (int, float)) or not (0 <= x <= 1):
        x = 0.08 + (default_index % 2) * 0.5
    if not isinstance(y, (int, float)) or not (0 <= y <= 1):
        y = 0.10 + (default_index // 2) * 0.34

    # 给「说话者标注」预留上方空间，避免贴边被裁
    label_room = 24 if item.get("speaker") and dtype in ("speech", "thought", "shout", "whisper") else 6
    bx = int(px0 + x * pw)
    by = int(py0 + y * ph)
    bx = max(px0 + 6, min(bx, px1 - bw - 6))
    by = max(py0 + label_room, min(by, py1 - bh - 6))
    box = (bx, by, bx + bw, by + bh)

    d = ImageDraw.Draw(base)
    fill = (255, 255, 255, 235)
    outline = (20, 20, 20)

    if dtype == "sfx":
        # 拟声词：无气泡，描边大字
        font = _load_font(int(font_size * 1.7), font_preset)
        d.text((bx, by), text, font=font, fill=(240, 240, 240),
               stroke_width=4, stroke_fill=(15, 15, 15))
        return

    shape = _BUBBLE_SHAPES.get(dtype, _draw_speech)
    # 半透明底
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    shape(od, box, fill, outline)
    base_rgba = base.convert("RGBA")
    base_rgba.alpha_composite(overlay)
    base.paste(base_rgba.convert("RGB"))

    # 文字
    d = ImageDraw.Draw(base)
    font = _load_font(font_size, font_preset)
    line_h = font_size + 6
    ty = box[1] + 12
    for line in lines:
        tw = font.getlength(line)
        d.text((box[0] + (box[2] - box[0] - tw) / 2, ty), line, font=font, fill=(15, 15, 15))
        ty += line_h

    # 说话者标注（小字，气泡左上；位置钳制在面板内）
    speaker = item.get("speaker", "")
    if speaker and dtype in ("speech", "thought", "shout", "whisper"):
        sfont = _load_font(max(14, font_size - 8), font_preset)
        ly = max(py0 + 2, box[1] - sfont.size - 4)
        d.text((box[0] + 4, ly), f"·{speaker}", font=sfont, fill=(90, 90, 90))
