#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LoRA 效果对比：同一 prompt + 同 seed，分别用「无 LoRA / 不同 scale 的 LoRA」生成，拼成对比图。

训练完 LoRA 后跑这个，一眼看出权重级一致性的实际效果。

用法：
  python scripts/lora_compare.py --project proj_xxx --character char_yyy \
      --prompt "linxia standing in the rain at night" [--scales 0,0.7,1.0]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Character, Project  # noqa: E402
from app.pipeline.prompt import apply_style_directive  # noqa: E402
from app.providers.base import GenRequest  # noqa: E402
from app.providers.mlx_provider import MlxProvider  # noqa: E402

W, H, STEPS = 640, 960, 8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--character", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--scales", default="0,0.7,1.0", help="0 = 不挂 LoRA")
    ap.add_argument("--seed", type=int, default=8888)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with Session(engine) as s:
        project = s.get(Project, args.project)
        ch = s.get(Character, args.character)
        if not project or not ch:
            print("项目或人物不存在"); return 1
        if not ch.lora_path:
            print(f"人物「{ch.name}」还没有训练好的 LoRA"); return 1
        lora = Path(ch.lora_path)
        if not lora.is_absolute():
            lora = config.PROJECTS_DIR / lora
        if not lora.exists():
            print(f"LoRA 文件不存在: {lora}"); return 1

        prompt = apply_style_directive(project.style, args.prompt)
        provider = MlxProvider()
        out_dir = config.project_dir(project.id) / "lora" / ch.id / "compare"
        out_dir.mkdir(parents=True, exist_ok=True)

        tiles = []
        for sc in [float(x) for x in args.scales.split(",")]:
            tag = "no-lora" if sc == 0 else f"lora-{sc:g}"
            out = out_dir / f"{tag}_{args.seed}.png"
            t0 = time.time()
            provider.generate(GenRequest(
                prompt=prompt, output_path=out, width=W, height=H, steps=STEPS,
                seed=args.seed,
                lora_paths=[] if sc == 0 else [lora],
                lora_scales=[] if sc == 0 else [sc],
            ))
            print(f"  {tag:10s} {time.time() - t0:.0f}s  → {out.name}")
            tiles.append((tag, out))

        font_path = next((p for p in config.FONT_CANDIDATES if Path(p).exists()), None)
        font = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()
        pad, bar = 12, 40
        canvas = Image.new("RGB", (len(tiles) * W + (len(tiles) + 1) * pad, H + bar + 2 * pad),
                           (250, 249, 246))
        d = ImageDraw.Draw(canvas)
        for i, (tag, path) in enumerate(tiles):
            x = pad + i * (W + pad)
            canvas.paste(Image.open(path).convert("RGB").resize((W, H)), (x, pad))
            d.text((x + 4, pad + H + 6), f"林夏 {tag}", font=font, fill=(30, 30, 30))
        dst = Path(args.out) if args.out else (Path(__file__).resolve().parent.parent / "docs" / "lora_compare.png")
        canvas.save(dst)
        print(f"\n对比图 → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
