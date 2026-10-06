"""画风（彩色/黑白）默认值与切换传播测试。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Asset, Character, Panel, Project
from app.pipeline import worker
from app.pipeline.asset import build_asset_sheet_prompt
from app.pipeline.character import build_sheet_prompt
from app.pipeline.prompt import build_panel_prompt


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "styles.db"
    import app.db as db_mod
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(worker, "engine", engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    worker.stop_worker()
    shutil.rmtree(tmp_path / "projects", ignore_errors=True)


def test_default_style_is_color(client):
    """新建项目默认应为彩色画风（用户要求：图片要彩色）。"""
    pid = client.post("/api/projects", json={"name": "默认彩色"}).json()["id"]
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["style"] == config.DEFAULT_STYLE
    assert detail["style"] in config.COLOR_STYLES
    assert "color" in config.STYLE_PRESETS[detail["style"]].lower()


def test_all_color_presets_are_colorful_and_text_free():
    for key in config.COLOR_STYLES:
        text = config.STYLE_PRESETS[key].lower()
        assert "color" in text, key
        assert "text" in text or "lettering" in text, key  # 必须带无文字约束
    # 黑白预设不得混入色彩词
    assert "no color" in config.STYLE_PRESETS["manga_bw"]


def test_style_switch_propagates_to_panel_prompt(client):
    """切换画风后，分镜提示词必须立刻用新画风（= 重新生成即变彩色）。"""
    pid = client.post("/api/projects", json={"name": "切画风", "style": "manga_bw"}).json()["id"]
    project = Project(id=pid, name="切画风", style="manga_bw")
    panel = Panel(project_id=pid, page_id="pg", index=1, action="少女在雨中奔跑")

    bw, _ = build_panel_prompt(project, panel, [])
    assert "black and white" in bw.lower()
    assert "no color" in bw.lower()

    r = client.put(f"/api/projects/{pid}", json={"style": "manhua_color"})
    assert r.status_code == 200 and r.json()["style"] == "manhua_color"
    project.style = "manhua_color"
    colored, _ = build_panel_prompt(project, panel, [])
    assert "full color" in colored.lower()
    assert "black and white" not in colored.lower()


def test_style_switch_propagates_to_sheets(client):
    """人物/场景/道具设定图也必须跟着变彩色，否则资产仍是黑白。"""
    p_bw = Project(name="x", style="manga_bw")
    p_color = Project(name="x", style=config.DEFAULT_STYLE)
    ch = Character(project_id="p", name="林夏", appearance="black bob hair")
    scene = Asset(project_id="p", kind="scene", name="校门口", description="a school gate")

    assert "no color" in build_sheet_prompt(p_bw, ch).lower()
    assert "full color" in build_sheet_prompt(p_color, ch).lower()
    assert "no color" in build_asset_sheet_prompt(p_bw, scene, "wide").lower()
    assert "full color" in build_asset_sheet_prompt(p_color, scene, "wide").lower()


def test_color_render_end_to_end(client):
    """彩色项目跑一遍：拆解 → 出图（mock）→ 排版 → 导出，确认链路无黑白残留。"""
    pid = client.post("/api/projects", json={"name": "彩色链路"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏在旧图书馆前停下脚步。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    r = client.post(f"/api/panels/{panel['id']}/draft", json={"count": 1, "provider": "mock"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]

    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    assert "full color" in panel["prompt"].lower()
    assert "black and white" not in panel["prompt"].lower()
    client.post(f"/api/candidates/{panel['candidates'][0]['id']}/select")
    r = client.post(f"/api/pages/{detail['pages'][0]['id']}/render")
    assert r.status_code == 200


def test_style_directive_wraps_prompt_ends():
    """风格硬指令必须夹在提示词首尾（实测：不这样做彩色会退化成黑白网点）。"""
    from app.pipeline.prompt import apply_style_directive

    body = "a girl walking in the rain, medium shot"
    out = apply_style_directive("manhua_color", body)
    assert out.startswith(config.STYLE_DIRECTIVES["manhua_color"])
    assert config.STYLE_DIRECTIVES["manhua_color"] in out[-len(config.STYLE_DIRECTIVES["manhua_color"]) - 40:]
    assert "no black-and-white" in out

    bw = apply_style_directive("manga_bw", body)
    assert bw.startswith(config.STYLE_DIRECTIVES["manga_bw"])
    assert "no color" in bw

    # 未知画风不炸
    assert apply_style_directive("unknown_style", body) == body


def test_generated_prompt_keeps_color_directive_at_ends(client):
    """端到端：生成后的 panel.prompt 首尾都要有彩色指令（守护语句不能把它挤掉）。"""
    pid = client.post("/api/projects", json={"name": "首尾风格"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏在旧图书馆前停下脚步。", "pages": 1, "panels_per_page": 1,
                      "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    scene = detail["assets"]["scene"][0]
    # 给场景造一张"当前画风"的参考图，强制走参考图条件路径（守护语句+风格指令同时存在）
    client.post(f"/api/assets/{scene['id']}/sheet", json={"count": 1, "provider": "mock"})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    client.put(f"/api/panels/{panel['id']}", json={"scene_id": scene["id"]})

    r = client.post(f"/api/panels/{panel['id']}/draft",
                    json={"count": 1, "provider": "mock", "use_references": True})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    detail = client.get(f"/api/projects/{pid}").json()
    prompt = detail["pages"][0]["panels"][0]["prompt"]
    directive = config.STYLE_DIRECTIVES[config.DEFAULT_STYLE]
    assert prompt.startswith(directive), prompt[:120]
    assert directive in prompt[-len(directive) - 60:], prompt[-200:]
    assert "AUTHORITATIVE" in prompt  # 守护语句仍在


def test_repeated_generation_does_not_overwrite_files(client):
    """回归：同一格重复生成（seed 相同）不能覆盖旧候选文件 —— 否则历史候选全丢。"""
    pid = client.post("/api/projects", json={"name": "不覆盖"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏在旧图书馆前停下。", "pages": 1, "panels_per_page": 1,
                      "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]

    paths = []
    for _ in range(2):
        r = client.post(f"/api/panels/{panel['id']}/draft",
                        json={"count": 2, "provider": "mock", "steps": 2})
        job = _wait(client, r.json()["id"])
        assert job["status"] == "done", job["error"]
        detail = client.get(f"/api/projects/{pid}").json()
        panel = detail["pages"][0]["panels"][0]
        paths = [c["path"] for c in panel["candidates"]]

    assert len(paths) == 4, paths
    assert len(set(paths)) == 4, f"候选文件路径重复（会被覆盖）：{paths}"
    for p in paths:
        f = config.PROJECTS_DIR / p
        assert f.exists() and f.stat().st_size > 0, f


def test_action_dialogue_is_stripped_from_prompt():
    """对白不进图像模型：动作里的引号对白必须剥掉（实测会诱导模型画黑白漫画页+自绘对话框）。"""
    from app.pipeline.prompt import sanitize_action

    out = sanitize_action("雨夜，林夏撑着黑伞站在旧图书馆前。她轻声说：「你终于来了。」她收起黑伞。")
    assert "你终于来了" not in out
    assert "「" not in out and "」" not in out
    assert "轻声说" not in out
    assert "撑着黑伞站在旧图书馆前" in out and "她收起黑伞" in out
    assert "。，" not in out  # 标点已清理

    # 无对白的动作原样保留
    assert sanitize_action("少女在雨中奔跑") == "少女在雨中奔跑"
    assert sanitize_action("") == ""

    # 端到端：生成后的提示词里不应出现对白原文
    from app.models import Panel, Project
    from app.pipeline.prompt import build_panel_prompt
    p = Project(name="x", style=config.DEFAULT_STYLE)
    pnl = Panel(project_id="p", page_id="pg", index=1,
                action="她说：「我要转学了。」", )
    prompt, _ = build_panel_prompt(p, pnl, [])
    assert "我要转学了" not in prompt


def test_reference_images_prefers_current_style(tmp_path, monkeypatch):
    """切彩色后重新生成设定图：参考图必须**只用当前画风**，不能混入旧黑白图。"""
    from PIL import Image
    from app.pipeline.character import reference_images

    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    ch = Character(project_id="p1", name="林夏")
    d = tmp_path / "p1" / "characters" / ch.id
    d.mkdir(parents=True)
    for name in ("sheet_manga_bw_1.png", f"sheet_{config.DEFAULT_STYLE}_2.png",
                 f"sheet_{config.DEFAULT_STYLE}_3.png"):
        Image.new("RGB", (32, 32), (30, 30, 30)).save(d / name)
    # reference_image 指向旧黑白图时也不该被带进来
    ch.reference_image = str((d / "sheet_manga_bw_1.png").relative_to(tmp_path))

    refs = reference_images("p1", ch, config.DEFAULT_STYLE)
    assert refs, refs
    assert all(config.DEFAULT_STYLE in r.name for r in refs), refs
    assert "manga_bw" not in " ".join(r.name for r in refs)

    # 该画风完全没有设定图时不挂参考图（宁可只靠文字描述，也不混入其它画风）
    ch2 = Character(project_id="p2", name="林夏")
    d2 = tmp_path / "p2" / "characters" / ch2.id
    d2.mkdir(parents=True)
    Image.new("RGB", (32, 32), (30, 30, 30)).save(d2 / "sheet_manga_bw_9.png")
    assert reference_images("p2", ch2, config.DEFAULT_STYLE) == []
    # 不传画风时（旧调用）仍能拿到任意设定图
    assert reference_images("p2", ch2)


def _wait(client, job_id, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
