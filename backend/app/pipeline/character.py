"""角色一致性（蓝图 §6 第一优先级）。

两件事：
1. Character Bible 设定图生成 —— 由外貌描述出一张可直接当参考图的角色设定图；
2. 生成分镜时把设定图作为参考条件（mlx-edit / ComfyUI reference）挂进 GenRequest。

原则：**不要第一天就训练 LoRA**。先用 Reference + Prompt 跑通一致性。
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..models import Character, Project


def character_dir(project_id: str, character_id: str) -> Path:
    d = config.project_dir(project_id) / "characters" / character_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def reference_images(project_id: str, character: Character, style: str = "") -> list[Path]:
    """该角色当前可用的参考图（磁盘上真实存在的）。

    style 非空时**优先挑当前画风的设定图**（文件名形如 sheet_<style>_<seed>.png），
    避免切到彩色后仍把旧黑白设定图挂成参考图。
    """
    d = character_dir(project_id, character.id)
    # 指定画风时**只挂当前画风**的设定图；该画风还没有图就不挂（宁可只靠文字描述），
    # 否则彩色项目里会混进旧黑白参考图，画面风格被拉回黑白。
    if style:
        candidates = sorted(d.glob(f"sheet_{style}_*.png"))
    else:
        candidates = sorted(d.glob("sheet_*.png"))
    refs: list[Path] = []
    if character.reference_image:
        p = config.PROJECTS_DIR / character.reference_image
        if p.exists() and p.stat().st_size > 0 and p in set(candidates):
            refs.append(p)
    for p in candidates:
        if p not in refs and p.stat().st_size > 0:
            refs.append(p)
        if len(refs) >= config.MAX_REFERENCE_IMAGES:
            break
    return refs[: config.MAX_REFERENCE_IMAGES]


def build_sheet_prompt(project: Project, character: Character, view: str = "multi") -> str:
    """角色设定图提示词 —— 目标产物是「可当参考图」的干净设定图。"""
    style = config.STYLE_PRESETS.get(project.style, project.style)
    body = character.appearance or f"{character.name}, a manga character"
    views = {
        "multi": "character design sheet showing the same character in three views: full body front view, three-quarter view and side view, identical face and outfit in every view",
        "front": "character design sheet, full body front view, standing neutral pose, arms at sides",
        "bust": "character design sheet, bust portrait, front view, neutral expression",
    }.get(view, "character design sheet, full body front view")
    return (
        f"{views}, of {body}. plain flat light grey background, no scenery, no props, "
        f"even lighting, clean lineart. {style}. "
        "IMPORTANT: no text, no lettering, no written characters, no watermark, "
        "no speech bubbles anywhere in the image"
    )


MULTI_PERSON_HINTS = ("两人", "二人", "他们", "她们", "大家", "众人", "两人并肩", "群")


def build_edit_prompt(
    base_prompt: str, characters: list[Character], n_refs: int
) -> str:
    """参考图模式下的提示词：明确要求沿用参考图中的角色形象。

    注意：文本与参考图冲突时模型倾向听文本，所以必须显式声明「参考图为准」。
    """
    if not characters:
        return base_prompt
    names = ", ".join(c.name for c in characters)
    guard = (
        f"Keep the character identity of {names} exactly the same as in the reference "
        f"image{'s' if n_refs > 1 else ''}: same face, same hairstyle and hair length, "
        "same hair color, same outfit, same body proportions. "
        "THE REFERENCE IMAGE IS AUTHORITATIVE for appearance; if any text description "
        "conflicts with it, always follow the reference image. "
        "Only change the scene, camera angle, pose and expression as described below. "
    )
    # 实测坑：画面里要求「两人」但只挂了一张参考图时，模型会把参考角色复制成两个人
    if len(characters) == 1 and any(h in base_prompt for h in MULTI_PERSON_HINTS):
        guard += (
            "The scene contains more than one person: the reference image shows only "
            f"{names}, so every other person must be a clearly DIFFERENT individual "
            "with a different face, hairstyle and outfit. Never duplicate the "
            "reference character. "
        )
    return guard + base_prompt
