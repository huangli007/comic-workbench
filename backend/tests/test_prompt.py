"""Prompt 构建器测试。"""
from __future__ import annotations

from app import config
from app.models import Character, Panel, Project
from app.pipeline.prompt import build_panel_prompt, character_description


def test_prompt_contains_all_sections():
    project = Project(name="t", style="manga_bw")
    panel = Panel(
        action="少年在雨中奔跑",
        location="城市街道",
        camera="low_angle",
        lighting="night",
        emotion="fearful",
    )
    chars = [Character(name="林夏", appearance="black long hair, school uniform")]
    prompt, negative = build_panel_prompt(project, panel, chars)
    assert "black and white Japanese manga" in prompt
    assert "black long hair" in prompt
    assert "雨中奔跑" in prompt
    assert "low angle" in prompt
    assert "moonlight" in prompt
    assert "no speech bubbles" in prompt
    assert negative == config.DEFAULT_NEGATIVE


def test_override_prompt():
    project = Project(name="t")
    panel = Panel()
    prompt, _ = build_panel_prompt(project, panel, [], override_prompt="my custom prompt")
    assert prompt == "my custom prompt"


def test_empty_character_description():
    assert character_description([]) == ""
    assert "林夏" in character_description([Character(name="林夏", appearance="tall")])
