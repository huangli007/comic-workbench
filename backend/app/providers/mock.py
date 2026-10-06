"""Mock Provider — 测试用，生成带 prompt 摘要的占位图。"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from .base import GenRequest, ImageProvider


class MockProvider(ImageProvider):
    name = "mock"

    def available(self) -> bool:
        return True

    def generate(self, req: GenRequest) -> Path:
        req.output_path.parent.mkdir(parents=True, exist_ok=True)
        rnd = random.Random(req.seed)
        img = Image.new(
            "RGB",
            (req.width, req.height),
            (rnd.randint(180, 240), rnd.randint(180, 240), rnd.randint(180, 240)),
        )
        d = ImageDraw.Draw(img)
        for _ in range(6):
            x0 = rnd.randint(0, req.width - 100)
            y0 = rnd.randint(0, req.height - 100)
            d.ellipse(
                [x0, y0, x0 + rnd.randint(60, 160), y0 + rnd.randint(60, 160)],
                outline=(90, 90, 110), width=3,
            )
        d.rectangle([0, 0, req.width - 1, req.height - 1], outline=(60, 60, 60), width=4)
        d.text((16, 20), f"MOCK seed={req.seed}", fill=(40, 40, 40))
        img.save(req.output_path)
        return req.output_path
