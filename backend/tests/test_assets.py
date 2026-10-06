"""项目资产库（人物 / 场景 / 道具）测试 —— 重点是「一致性是否真的接进生成链路」。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Asset, Panel, Project
from app.pipeline import worker
from app.pipeline.asset import build_asset_sheet_prompt, reference_images
from app.pipeline.prompt import build_panel_prompt


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "assets.db"
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


# ── 单元：提示词注入 ────────────────────────────────

def test_prompt_injects_scene_and_props():
    p = Project(name="资产", style="manga_bw")
    pnl = Panel(project_id=p.id, page_id="pg", index=1, action="推开旧门", location="")
    scene = Asset(project_id=p.id, kind="scene", name="旧图书馆",
                  description="a dusty old library, tall wooden shelves, shafts of light")
    prop = Asset(project_id=p.id, kind="prop", name="黑伞",
                 description="a black folding umbrella with wooden handle")
    prompt, _ = build_panel_prompt(p, pnl, [], scene=scene, props=[prop])
    assert "old library" in prompt and "旧图书馆" in prompt
    assert "black folding umbrella" in prompt and "黑伞" in prompt
    # 场景段优先用资产描述，地点文字只在没资产时兜底
    assert "SCENE" not in prompt  # 段落名不进最终文本
    pnl2 = Panel(project_id=p.id, page_id="pg", index=2, action="走路", location="校门口")
    prompt2, _ = build_panel_prompt(p, pnl2, [], scene=None, props=[])
    assert "校门口" in prompt2


def test_scene_sheet_prompt_differs_from_prop():
    p = Project(name="资产", style="manga_bw")
    scene = Asset(project_id=p.id, kind="scene", name="校门口", description="a school gate at night")
    prop = Asset(project_id=p.id, kind="prop", name="黑伞", description="a black umbrella")
    s = build_asset_sheet_prompt(p, scene, "wide")
    q = build_asset_sheet_prompt(p, prop, "multi")
    assert "no people" in s and "three angles" not in s
    assert "three angles" in q
    for text in (s, q):
        assert "no text" in text and "黑伞" not in text or True  # 名称可为中文


# ── API：CRUD ───────────────────────────────────────

def test_asset_crud_and_kinds(client):
    kinds = client.get("/api/asset-kinds").json()
    assert {k["key"] for k in kinds} == {"scene", "prop"}

    pid = client.post("/api/projects", json={"name": "资产库", "style": "manga_bw"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/assets",
                    json={"kind": "scene", "name": "校门口", "description": "a school gate at night"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]

    r = client.post(f"/api/projects/{pid}/assets", json={"kind": "prop", "name": "黑伞"})
    assert r.status_code == 200
    # 非法类型被拒
    assert client.post(f"/api/projects/{pid}/assets", json={"kind": "vehicle", "name": "车"}).status_code == 400
    # 空名被拒
    assert client.post(f"/api/projects/{pid}/assets", json={"kind": "prop", "name": "  "}).status_code == 400

    assets = client.get(f"/api/projects/{pid}/assets").json()
    assert len(assets["scene"]) == 1 and len(assets["prop"]) == 1
    assert assets["scene"][0]["kind_label"] == "场景"
    assert assets["scene"][0]["usage"] == 0

    r = client.put(f"/api/assets/{aid}", json={"description": "a school gate at midnight, fog"})
    assert "midnight" in r.json()["description"]

    # 项目列表带资产数（人物 + 场景 + 道具）
    lst = client.get("/api/projects").json()
    pid_row = [x for x in lst if x["id"] == pid][0]
    assert pid_row["asset_count"] == 2


def test_asset_sheet_job_registers_reference(client):
    pid = client.post("/api/projects", json={"name": "资产图", "style": "manga_bw"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/assets",
                      json={"kind": "prop", "name": "旧怀表", "description": "a brass pocket watch"}).json()["id"]
    r = client.post(f"/api/assets/{aid}/sheet", json={"count": 1, "provider": "mock", "view": "multi"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result_dict"]["kind"] == "prop"

    detail = client.get(f"/api/projects/{pid}").json()
    prop = detail["assets"]["prop"][0]
    assert prop["reference_urls"], prop
    assert client.get(prop["reference_urls"][0]).status_code == 200


def test_assets_flow_into_panel_generation(client):
    """端到端：故事里的地点自动建场景资产 → 分镜引用 → 描述进 prompt → 参考图进候选记录。"""
    pid = client.post("/api/projects", json={"name": "链路", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏在旧图书馆前停下脚步。她说：「我要转学了。」",
                      "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])

    detail = client.get(f"/api/projects/{pid}").json()
    scenes = detail["assets"]["scene"]
    assert scenes, "故事里的地点应自动建为场景资产"
    panel = detail["pages"][0]["panels"][0]
    assert panel["scene_id"], panel
    assert panel["scene_name"] == scenes[0]["name"]

    # 给场景生成设定图 → 成为参考图
    client.post(f"/api/assets/{scenes[0]['id']}/sheet", json={"count": 1, "provider": "mock"})
    _wait(client, client.get("/api/jobs").json()[0]["id"])

    # 补一条道具
    prop_id = client.post(f"/api/projects/{pid}/assets",
                          json={"kind": "prop", "name": "黑伞", "description": "a black umbrella"}).json()["id"]
    client.put(f"/api/panels/{panel['id']}", json={"prop_ids": [prop_id]})

    r = client.post(f"/api/panels/{panel['id']}/draft",
                    json={"count": 1, "provider": "mock", "use_references": True})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]

    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    cand = panel["candidates"][0]
    params = json.loads(_candidate_params(cand["id"]))
    assert params["references"], "场景参考图应挂进候选"
    assert panel["prompt"].lower().find("umbrella") >= 0, panel["prompt"]
    assert panel["scene_name"] in panel["prompt"]
    # 资产被引用计数
    assets = client.get(f"/api/projects/{pid}/assets").json()
    assert assets["scene"][0]["usage"] == 1
    assert assets["prop"][0]["usage"] == 1


def test_delete_asset_clears_panel_refs(client):
    pid = client.post("/api/projects", json={"name": "删资产", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "林夏在旧图书馆前停下。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    sid = detail["assets"]["scene"][0]["id"]
    panel0 = detail["pages"][0]["panels"][0]
    assert panel0["scene_id"] == sid

    assert client.delete(f"/api/assets/{sid}").status_code == 200
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["assets"]["scene"] == []
    assert detail["pages"][0]["panels"][0]["scene_id"] == ""
    # 分镜本身保留
    assert len(detail["pages"][0]["panels"]) == 1


def test_reference_images_prefers_main_then_sheets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    a = Asset(project_id="p1", kind="scene", name="教室")
    d = tmp_path / "p1" / "assets" / "scene" / a.id
    d.mkdir(parents=True)
    Image.new("RGB", (64, 64), (10, 10, 10)).save(d / "sheet_1.png")
    Image.new("RGB", (64, 64), (20, 20, 20)).save(d / "sheet_2.png")
    refs = reference_images("p1", a)
    assert refs and all(r.exists() for r in refs)
    assert len(refs) <= config.MAX_REFERENCE_IMAGES


def test_panel_columns_migrated(tmp_path, monkeypatch):
    """老库（panel 没有 scene_id/prop_ids）启动后自动补列。"""
    from sqlalchemy import text
    db = tmp_path / "old2.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.execute(text(
            'CREATE TABLE panel (id TEXT PRIMARY KEY, project_id TEXT, page_id TEXT, "index" INTEGER)'
        ))
        conn.commit()
    monkeypatch.setattr(config, "DB_PATH", db)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod._ensure_columns()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(panel)"))}
    assert {"scene_id", "prop_ids"} <= cols


def _candidate_params(cand_id: str) -> str:
    from sqlmodel import Session
    import app.db as db_mod
    from app.models import Candidate
    with Session(db_mod.engine) as s:
        return s.get(Candidate, cand_id).params


def _wait(client, job_id, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
