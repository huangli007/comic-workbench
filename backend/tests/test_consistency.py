"""角色一致性（MVP-2）测试：设定图、参考图挂载、provider 自动路由。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Character, Panel, Project
from app.pipeline import worker
from app.pipeline.character import (
    build_edit_prompt,
    build_sheet_prompt,
    reference_images,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "consistency.db"
    import app.db as db_mod
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(worker, "engine", engine)
    with TestClient(app_import()) as c:
        yield c
    worker.stop_worker()
    shutil.rmtree(tmp_path / "projects", ignore_errors=True)


def app_import():
    from app.main import app
    return app


def test_sheet_prompt_contains_views_and_style():
    p = Project(name="测试", style="manga_bw")
    c = Character(project_id=p.id, name="林夏", appearance="black long hair, school uniform")
    prompt = build_sheet_prompt(p, c, "multi")
    assert "three views" in prompt
    assert "black long hair" in prompt
    assert "no text" in prompt
    # 设定图必须强调扁平背景，方便当参考图
    assert "plain flat light grey background" in prompt


def test_edit_prompt_keeps_identity_then_scene():
    p = Project(name="测试", style="manga_bw")
    c = Character(project_id=p.id, name="林夏", appearance="black long hair")
    out = build_edit_prompt("medium shot, walking in rain", [c], 1)
    assert out.index("林夏") < out.index("walking in rain")
    assert "same face" in out
    assert "Keep the character identity" in out
    # 文本与参考图冲突时必须听参考图（实测过：冲突时文本会覆盖参考图）
    assert "AUTHORITATIVE" in out
    assert "hair length" in out


def test_edit_prompt_guards_against_duplicating_single_reference():
    """实测坑：只有一张参考图但画面要求「两人」时，模型会复制参考角色。"""
    c = Character(project_id="p", name="林夏", appearance="bob hair")
    multi = build_edit_prompt("两人并肩走过空无一人的街道", [c], 1)
    assert "Never duplicate the reference character" in multi
    # 多角色时不加这条（各自有参考图）
    c2 = Character(project_id="p", name="陈默", appearance="短黑发")
    assert "Never duplicate" not in build_edit_prompt("两人并肩走", [c, c2], 2)
    # 单人场景也不加
    assert "Never duplicate" not in build_edit_prompt("close-up of her face", [c], 1)


def test_reference_images_picks_up_sheet_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    p = Project(name="测试")
    c = Character(project_id=p.id, name="林夏")
    d = tmp_path / p.id / "characters" / c.id
    d.mkdir(parents=True)
    Image.new("RGB", (64, 64), (10, 10, 10)).save(d / "sheet_111.png")
    Image.new("RGB", (64, 64), (20, 20, 20)).save(d / "sheet_222.png")
    refs = reference_images(p.id, c)
    assert len(refs) == min(2, config.MAX_REFERENCE_IMAGES)
    assert all(r.exists() and r.stat().st_size > 0 for r in refs)


def test_character_sheet_job_registers_reference(client):
    r = client.post("/api/projects", json={"name": "一致性", "style": "manga_bw"})
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/characters",
                    json={"name": "林夏", "appearance": "black long hair, school uniform"})
    cid = r.json()["id"]

    # 用 mock provider 跑设定图任务
    r = client.post(f"/api/characters/{cid}/sheet", json={"count": 1, "provider": "mock"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result_dict"]["reference_image"].endswith(".png")

    detail = client.get(f"/api/projects/{pid}").json()
    ch = detail["characters"][0]
    assert ch["reference_urls"], ch
    assert ch["reference_urls"][0].startswith("/files/")
    img = client.get(ch["reference_urls"][0])
    assert img.status_code == 200 and len(img.content) > 100


def test_panel_generation_attaches_references(client):
    """分镜生成时若角色有参考图，候选记录里必须带上 references。"""
    r = client.post("/api/projects", json={"name": "参考", "style": "manga_bw"})
    pid = r.json()["id"]
    cid = client.post(f"/api/projects/{pid}/characters",
                      json={"name": "林夏", "appearance": "black long hair"}).json()["id"]
    client.post(f"/api/characters/{cid}/sheet", json={"count": 1, "provider": "mock"})

    r = client.post(f"/api/projects/{pid}/storyboard",
                    json={"story": "雨夜，林夏站在校门口。她说：「你来了。」", "use_llm": False})
    _wait(client, r.json()["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    # 角色 id 必须挂到格子上（storyboard 阶段就该挂好）
    client.put(f"/api/panels/{panel['id']}", json={"characters": [cid]})

    r = client.post(f"/api/panels/{panel['id']}/draft",
                    json={"count": 1, "provider": "mock", "use_references": True})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]

    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    cand = panel["candidates"][0]
    from app.models import Candidate
    from sqlmodel import Session
    import app.db as db_mod
    with Session(db_mod.engine) as s:
        row = s.get(Candidate, cand["id"])
        params = json.loads(row.params)
    assert params["references"], params
    assert params["references"][0].endswith(".png")


def test_storyboard_regen_preserves_character_assets(client):
    """回归：重跑分镜不能把已生成参考图的角色冲掉（否则一致性资产全丢）。"""
    pid = client.post("/api/projects", json={"name": "保留", "style": "manga_bw"}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/characters",
                      json={"name": "林夏", "appearance": "black long hair"}).json()["id"]
    client.post(f"/api/characters/{cid}/sheet", json={"count": 1, "provider": "mock"})
    _wait(client, client.get("/api/jobs").json()[0]["id"])

    # 重跑分镜（同一个故事，会识别出同一个角色名）
    r = client.post(f"/api/projects/{pid}/storyboard",
                    json={"story": "雨夜，林夏站在校门口。她说：「你来了。」", "use_llm": False})
    _wait(client, r.json()["id"])

    detail = client.get(f"/api/projects/{pid}").json()
    names = [c["name"] for c in detail["characters"]]
    assert "林夏" in names
    ch = [c for c in detail["characters"] if c["name"] == "林夏"][0]
    assert ch["id"] == cid, "角色记录应被复用而不是重建"
    assert ch["reference_urls"], "参考图登记必须保留"


def test_storyboard_regen_drops_unused_blank_characters(client):
    pid = client.post("/api/projects", json={"name": "清理", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/characters", json={"name": "无关人物", "appearance": ""})
    r = client.post(f"/api/projects/{pid}/storyboard",
                    json={"story": "林夏站在校门口。她说：「你来了。」", "use_llm": False})
    _wait(client, r.json()["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    assert "无关人物" not in [c["name"] for c in detail["characters"]]


def _wait(client, job_id, timeout=30):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.3)
    raise TimeoutError("job 超时")
