"""项目资产库：场景 / 道具（人物由 character.py 负责，语义与能力对齐）。

一致性思路（蓝图 §6 的推广）：
    资产 = 名称 + 文字设定 + 参考图
    分镜引用资产 → 设定描述进 prompt + 参考图作为条件图
这样「同一个场景/同一件道具」跨格保持稳定，而不只是人物一致。
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..models import Asset, Project


def asset_dir(project_id: str, kind: str, asset_id: str) -> Path:
    d = config.project_dir(project_id) / "assets" / kind / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def reference_images(project_id: str, asset: Asset, style: str = "") -> list[Path]:
    """该资产当前可用的参考图（磁盘上真实存在的）。

    style 非空时优先挑当前画风的设定图，避免切彩色后仍挂旧黑白设定图。
    """
    d = asset_dir(project_id, asset.kind, asset.id)
    # 指定画风时只挂当前画风的设定图（同人物，避免风格串味）
    if style:
        candidates = sorted(d.glob(f"sheet_{style}_*.png"))
    else:
        candidates = sorted(d.glob("sheet_*.png"))
    refs: list[Path] = []
    if asset.reference_image:
        p = config.PROJECTS_DIR / asset.reference_image
        if p.exists() and p.stat().st_size > 0 and p in set(candidates):
            refs.append(p)
    for p in candidates:
        if p not in refs and p.stat().st_size > 0:
            refs.append(p)
        if len(refs) >= config.MAX_REFERENCE_IMAGES:
            break
    return refs[: config.MAX_REFERENCE_IMAGES]


def asset_text(asset: Asset) -> str:
    """资产进 prompt 的文本：优先英文设定描述，退化到名称。"""
    return (asset.description or asset.appearance or "").strip() or asset.name


# ── 资产设定图提示词 ─────────────────────────────────

SCENE_VIEWS = {
    "wide": (
        "establishing background art of a location, wide shot showing the whole place, "
        "no people present, detailed environment design"
    ),
    "interior": (
        "interior background art of a room, no people present, detailed environment design"
    ),
    "exterior": (
        "exterior background art of a building and its surroundings, no people present, "
        "detailed environment design"
    ),
}

PROP_VIEWS = {
    "multi": (
        "prop design sheet showing the same object from three angles: front view, "
        "side view and three-quarter view, identical design in every view"
    ),
    "front": "prop design sheet, single object front view, centered",
    "detail": "prop design sheet, close-up detail study of the object",
}


def build_asset_sheet_prompt(
    project: Project, asset: Asset, view: str = ""
) -> str:
    """资产设定图提示词 —— 目标产物是干净、可当参考图的环境/道具设定图。"""
    style = config.STYLE_PRESETS.get(project.style, project.style)
    body = asset_text(asset)
    if asset.kind == "scene":
        base = SCENE_VIEWS.get(view or "wide", SCENE_VIEWS["wide"])
        extra = "plain clean background art, no characters, no people, no text"
    else:
        base = PROP_VIEWS.get(view or "multi", PROP_VIEWS["multi"])
        extra = "plain flat light grey background, no scenery, no hands, no people"
    return (
        f"{base}, of {body}. {extra}, even lighting, clean lineart. {style}. "
        "IMPORTANT: no text, no lettering, no written characters, no watermark, "
        "no speech bubbles anywhere in the image"
    )


def scene_prompt_section(scene: Asset | None) -> str:
    if scene is None:
        return ""
    return f"{scene.name}: {asset_text(scene)}"


def props_prompt_section(props: list[Asset]) -> str:
    if not props:
        return ""
    return "; ".join(f"{p.name}: {asset_text(p)}" for p in props)


def guard_text(kind: str, assets: list[Asset], n_refs: int) -> str:
    """参考图条件生成时，要求沿用资产外观的守护语句。"""
    if not assets:
        return ""
    label = "location" if kind == "scene" else "props"
    names = ", ".join(a.name for a in assets)
    return (
        f"Keep the {label} consistent with the reference image"
        f"{'s' if n_refs > 1 else ''} ({names}): same layout, same materials, "
        "same colors and same design details. THE REFERENCE IMAGES ARE AUTHORITATIVE. "
    )
