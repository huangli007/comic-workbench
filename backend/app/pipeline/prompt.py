"""Prompt 组装器 — 模板化拼接，不让用户每次手写完整 Prompt（蓝图 §13）。"""
from __future__ import annotations

import re

from .. import config
from ..models import Asset, Character, Panel, Project
from .asset import props_prompt_section, scene_prompt_section

# 引号包裹的对白（蓝图 §14：对白不进图像模型，由文字框系统后期绘制）
QUOTED = re.compile(r"[「『“\"][^」』”\"]{0,120}[」』”\"]")
# 引述动词残留：「她说：」「他喊道，」
SAY_VERB = re.compile(r"[，,、]?\s*[\u4e00-\u9fa5]{0,4}(?:轻声|大声|低声|冷冷地|笑着)?(?:说|喊|道|问|答|吼|叫)[：:，,]?")


def sanitize_action(text: str) -> str:
    """从画面动作描述里剥掉对白原文与引述动词。

    实测教训：动作里带中文对白原文（如「你终于来了。」）时，模型会把它理解为
    「这是一页要排对白的漫画」，从而产出**黑白漫画页并自己画上对话框**；
    剥掉之后画面才回归纯插画（彩色）。
    """
    if not text:
        return ""
    t = QUOTED.sub("", text)
    t = SAY_VERB.sub("，", t)
    t = re.sub(r"([。！？；])[，,、]+", r"\1", t)      # 「前。，」→「前。」
    t = re.sub(r"[，,、]{2,}", "，", t)
    return t.strip(" ，,、。；;：:")


def _camera_hint(camera: str) -> str:
    return config.CAMERA_HINTS.get(camera, camera)


def _lighting_hint(lighting: str) -> str:
    return config.LIGHTING_PRESETS.get(lighting, lighting)


def character_description(chars: list[Character]) -> str:
    if not chars:
        return ""
    parts = []
    for c in chars:
        if c.appearance:
            parts.append(f"{c.name}: {c.appearance}")
        else:
            parts.append(c.name)
    return "; ".join(parts)


def apply_style_directive(style_key: str, prompt: str) -> str:
    """把风格硬指令夹在提示词前后（彩色/黑白都强制声明）。

    扩散模型对**首尾**词权重最高；参考图模式下前面还会拼一致性守护语句，
    不这样做风格词会被淹没（实测彩色项目退化成黑白网点）。
    """
    d = config.style_directive(style_key)
    if not d:
        return prompt
    return f"{d}. {prompt}. COLOR REQUIREMENT: {d}"


def build_panel_prompt(
    project: Project,
    panel: Panel,
    characters: list[Character],
    override_prompt: str = "",
    scene: Asset | None = None,
    props: list[Asset] | None = None,
) -> tuple[str, str]:
    """返回 (prompt, negative_prompt)。override_prompt 非空则直接使用。

    资产库（场景/道具）作为独立段进 prompt —— 这是跨格一致性的关键：
    同一个场景资产被多格引用时，描述与参考图都保持一致。
    """
    if override_prompt.strip():
        return override_prompt.strip(), panel.negative_prompt or config.DEFAULT_NEGATIVE

    style = config.STYLE_PRESETS.get(project.style, project.style)
    scene_text = scene_prompt_section(scene) or panel.location
    sections: list[tuple[str, str]] = [
        ("STYLE", style),
        ("CHARACTERS", character_description(characters)),
        ("ACTION", sanitize_action(panel.action)),
        ("SCENE", scene_text),
        ("PROPS", props_prompt_section(props or [])),
        ("CAMERA", _camera_hint(panel.camera)),
        ("LIGHTING", _lighting_hint(panel.lighting)),
        ("EMOTION", panel.emotion),
    ]
    prompt = ", ".join(v for _, v in sections if v and v.strip())
    # 画面中不要出现文字/招牌/对白 —— 对白由气泡系统后期合成（蓝图 §14）。
    # 注意：FLUX.2 不支持负向提示词，这类排除要求必须写进正向描述。
    prompt += (
        ". IMPORTANT: clean artwork with absolutely no text, no lettering, no written "
        "characters, no signage, no speech bubbles, no captions anywhere in the image"
    )
    negative = panel.negative_prompt or config.DEFAULT_NEGATIVE
    return prompt, negative
