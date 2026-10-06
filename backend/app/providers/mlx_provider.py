"""MLX Provider — 本机 mflux (FLUX.2 Klein 4B 8bit) 子进程生成。

两个后端：
- MlxProvider      「mlx」      文生图（mflux-generate-flux2）
- MlxEditProvider  「mlx-edit」 参考图条件生成（mflux-generate-flux2-edit，多图输入）
                   —— 角色一致性的核心：把角色设定图作为条件，保证跨格同一张脸
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .. import config
from .base import GenRequest, ImageProvider, ImageProviderError


def _base_env() -> dict:
    env = dict(os.environ)
    env["HF_ENDPOINT"] = config.HF_ENDPOINT
    env["HF_HUB_OFFLINE"] = "1"  # 强制离线：只走本地缓存，不重新下载
    return env


def _common_args(req: GenRequest, model: str) -> list[str]:
    cmd = [
        "--model", model,
        "--prompt", req.prompt,
        "--width", str(req.width),
        "--height", str(req.height),
        "--steps", str(req.steps),
        "--seed", str(req.seed),
        "--output", str(req.output_path),
    ]
    if config.MLX_LOW_RAM:
        cmd.append("--low-ram")
    # FLUX.2 不支持负向提示词；模型不支持时只能把排除要求写进正向描述
    if req.negative_prompt and config.MLX_SUPPORTS_NEGATIVE:
        cmd += ["--negative-prompt", req.negative_prompt]
    # flux2-klein 是蒸馏模型：--guidance 只接受 1.0，传别的值 CLI 直接 exit=2
    if req.guidance and abs(float(req.guidance) - 1.0) < 1e-6:
        cmd += ["--guidance", "1.0"]
    # 角色 LoRA（权重级一致性）。用可重复的 `--lora PATH [SCALE]`：
    # mflux 0.19+ 的新写法，比废弃的 --lora-paths/--lora-scales 语义清晰且不会配对错位
    # （后者需要两个等长列表，缺一个就整体错位）。
    if req.lora_paths:
        scales = list(req.lora_scales)
        for i, lp in enumerate(req.lora_paths):
            scale = float(scales[i]) if i < len(scales) else 1.0
            cmd += ["--lora", str(lp), str(scale)]
    return cmd


def _run(cmd: list[str], req: GenRequest) -> Path:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=_base_env())
    if proc.returncode != 0 or not req.output_path.exists() or req.output_path.stat().st_size == 0:
        raise ImageProviderError(
            f"mflux 生成失败 (exit={proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return req.output_path


class MlxProvider(ImageProvider):
    name = "mlx"

    def available(self) -> bool:
        return config.MFLUX_BIN.exists()

    def generate(self, req: GenRequest) -> Path:
        if not self.available():
            raise ImageProviderError(f"mflux 不可用: {config.MFLUX_BIN} 不存在")
        req.output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(config.MFLUX_BIN), *_common_args(req, req.model or config.MLX_MODEL)]
        return _run(cmd, req)


class MlxEditProvider(ImageProvider):
    """参考图条件生成：无参考图时自动退化为文生图。"""

    name = "mlx-edit"

    def available(self) -> bool:
        return config.MFLUX_EDIT_BIN.exists()

    def generate(self, req: GenRequest) -> Path:
        if not self.available():
            raise ImageProviderError(f"mflux edit 不可用: {config.MFLUX_EDIT_BIN} 不存在")
        refs = [p for p in req.reference_images if Path(p).exists()]
        if not refs:
            return MlxProvider().generate(req)  # 无参考图 → 普通文生图
        req.output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(config.MFLUX_EDIT_BIN),
            "--image-paths", *[str(p) for p in refs[: config.MAX_REFERENCE_IMAGES]],
            *_common_args(req, req.model or config.MLX_MODEL),
        ]
        return _run(cmd, req)
