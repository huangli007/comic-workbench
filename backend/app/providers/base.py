"""Provider 抽象基类 — 生产系统与图像引擎解耦（蓝图 §21 原则1）。"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GenRequest:
    prompt: str
    output_path: Path
    negative_prompt: str = ""
    width: int = 640
    height: int = 960
    steps: int = 6
    seed: int = 0
    guidance: float = 1.0
    reference_images: list[Path] = field(default_factory=list)  # 角色参考（一致性）
    model: str = ""
    # 角色 LoRA（权重级一致性，比参考图更硬）
    lora_paths: list[Path] = field(default_factory=list)
    lora_scales: list[float] = field(default_factory=list)


class ImageProviderError(RuntimeError):
    pass


class ImageProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def available(self) -> bool:
        """该后端当前是否可用。"""

    @abc.abstractmethod
    def generate(self, req: GenRequest) -> Path:
        """同步生成一张图，返回产物路径。失败抛 ImageProviderError。"""

    def info(self) -> dict:
        return {"name": self.name, "available": self.available()}
