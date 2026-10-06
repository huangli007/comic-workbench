#!/usr/bin/env python3
"""生成常用风格样张对照图（同一场景 × 多种风格），产出 docs/style_gallery.png。

直接调用本机 MLX 引擎与提示词组装逻辑，不需要工作台在运行。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import config  # noqa: E402
from app.models import Panel, Project  # noqa: E402
from app.pipeline.prompt import apply_style_directive, build_panel_prompt  # noqa: E402
from app.providers.base import GenRequest  # noqa: E402
from app.providers.mlx_provider import MlxProvider  # noqa: E402

# 同一场景描述，只换风格 —— 便于横向对比
SCENE = "a young hero standing on a rooftop at dusk, wind blowing the coat, city below"
STYLES = [
    "manhua_color", "xuanhuan_epic", "xianxia_ethereal",
    "urban_supernatural", "shoujo_romance", "mecha_scifi",
    "guofeng_ink", "chibi_comedy", "manga_bw",
]
W, H, STEPS = 512, 768, 6
OUT = Path(__file__).resolve().parent.parent / "docs" / "style_samples"
OUT.mkdir(parents=True, exist_ok=True)

provider = MlxProvider()
labels = {s["key"]: s["label"] for s in config.STYLE_LIBRARY}

files = []
for key in STYLES:
    project = Project(name="gallery", style=key)
    panel = Panel(project_id="g", page_id="pg", index=1, action=SCENE)
    prompt, _ = build_panel_prompt(project, panel, [])
    prompt = apply_style_directive(key, prompt)
    out = OUT / f"{key}.png"
    if out.exists() and out.stat().st_size > 0:
        print(f"skip {key}（已存在）")
    else:
        t0 = time.time()
        provider.generate(GenRequest(prompt=prompt, output_path=out, width=W, height=H, steps=STEPS,
                                     seed=20260925))
        print(f"{key:20s} {labels[key]:10s} {time.time() - t0:.0f}s")
    files.append((key, out))

# 拼成 3×3 对照图（带中文标签）
font_path = next((p for p in config.FONT_CANDIDATES if Path(p).exists()), None)
font = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
cols, pad, bar = 3, 14, 46
rows = (len(files) + cols - 1) // cols
W2, H2 = cols * W + (cols + 1) * pad, rows * (H + bar) + (rows + 1) * pad
canvas = Image.new("RGB", (W2, H2), (250, 249, 246))
d = ImageDraw.Draw(canvas)
for i, (key, path) in enumerate(files):
    r, c = divmod(i, cols)
    x = pad + c * (W + pad)
    y = pad + r * (H + bar + pad)
    canvas.paste(Image.open(path).convert("RGB").resize((W, H)), (x, y))
    d.rectangle([x - 1, y - 1, x + W, y + H], outline=(200, 200, 200))
    tag = f"{labels[key]}{'' if key in config.COLOR_STYLES else '（黑白）'}"
    d.text((x + 4, y + H + 6), tag, font=font, fill=(40, 40, 40))
canvas.save(Path(__file__).resolve().parent.parent / "docs" / "style_gallery.png")
print("→ docs/style_gallery.png", canvas.size)
