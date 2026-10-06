"""Provider 注册与路由。"""
from __future__ import annotations

from .base import GenRequest, ImageProvider, ImageProviderError
from .comfyui import ComfyUIProvider
from .mlx_provider import MlxEditProvider, MlxProvider
from .mock import MockProvider

_PROVIDERS: dict[str, ImageProvider] = {}


def register(provider: ImageProvider) -> None:
    _PROVIDERS[provider.name] = provider


def _ensure_registered() -> None:
    if not _PROVIDERS:
        register(MlxProvider())
        register(MlxEditProvider())
        register(ComfyUIProvider())
        register(MockProvider())


def get_provider(name: str) -> ImageProvider:
    _ensure_registered()
    if name not in _PROVIDERS:
        raise ImageProviderError(f"未知 provider: {name}，可用: {list(_PROVIDERS)}")
    return _PROVIDERS[name]


def all_providers() -> list[ImageProvider]:
    _ensure_registered()
    return list(_PROVIDERS.values())
