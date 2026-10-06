"""局部重绘 + 外部编辑器往返 测试。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Candidate, Panel, Project
from app.pipeline import worker
from app.pipeline.inpaint import blend_patch, inpaint_region


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "inpaint.db"
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


def test_blend_patch_feathers_edges():
    base = Image.new("RGB", (200, 200), (0, 0, 0))
    patch = Image.new("RGB", (60, 60), (255, 255, 255))
    blend_patch(base, patch, (70, 70, 60, 60), feather=10)
    # 中心已替换
    assert base.getpixel((100, 100)) == (255, 255, 255)
    # 角落（羽化外）仍为黑
    assert base.getpixel((70, 70)) == (0, 0, 0)
    # 边缘为过渡灰（0 < v < 255）
    edge = base.getpixel((85, 100))[0]
    assert 0 < edge < 255, edge


def test_inpaint_region_with_mock_provider(client):
    """选区重绘：产物尺寸与原图一致，且产生 stage=inpaint 的新候选。"""
    pid = client.post("/api/projects", json={"name": "重绘", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏站在校门口。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]

    r = client.post(f"/api/panels/{panel['id']}/draft", json={"count": 1, "provider": "mock"})
    _wait(client, r.json()["id"])

    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    cand_id = panel["candidates"][0]["id"]
    client.post(f"/api/candidates/{cand_id}/select")

    r = client.post(f"/api/panels/{panel['id']}/inpaint", json={
        "rect": {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.3},
        "prompt": "一把红色的伞",
        "provider": "mock",
        "steps": 2,
    })
    job = _wait(client, r.json()["id"], timeout=60)
    assert job["status"] == "done", job["error"]

    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    stages = [c["stage"] for c in panel["candidates"]]
    assert "inpaint" in stages, stages

    inp = [c for c in panel["candidates"] if c["stage"] == "inpaint"][0]
    params = json.loads(_candidate_params(inp["id"]))
    assert params["rect"]["w"] > 0
    assert params["source_candidate"] == cand_id
    assert params["size"] == [640, 960]  # mock 默认草稿尺寸

    # 尺寸必须与原图一致（否则排版会错位）
    draft_path = [c for c in panel["candidates"] if c["stage"] == "draft"][0]["path"]
    src = Image.open(config.PROJECTS_DIR / draft_path)
    out = Image.open(config.PROJECTS_DIR / inp["path"])
    assert out.size == src.size, (out.size, src.size)

    # 未指定选区的错误参数应被拒
    r = client.post(f"/api/panels/{panel['id']}/inpaint", json={"rect": {"x": 0.1}})
    assert r.status_code == 400


def test_inpaint_requires_source_image(client):
    pid = client.post("/api/projects", json={"name": "空", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "场景一。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    r = client.post(f"/api/panels/{panel['id']}/inpaint",
                    json={"rect": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, "provider": "mock"})
    job = _wait(client, r.json()["id"], timeout=30)
    assert job["status"] == "failed"
    assert "还没有可用图像" in job["error"]


def test_external_edit_roundtrip(client):
    """导出工作图 → 模拟在编辑器里改动 → 回填为手动候选。"""
    pid = client.post("/api/projects", json={"name": "外编", "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏站在校门口。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    r = client.post(f"/api/panels/{panel['id']}/draft", json={"count": 1, "provider": "mock"})
    _wait(client, r.json()["id"])

    # 导出（不真的打开外部程序）
    r = client.post(f"/api/panels/{panel['id']}/external-export",
                    json={"candidate_id": "", "open_editor": False})
    assert r.status_code == 200, r.text
    from pathlib import Path
    work = Path(r.json()["work_path"])
    assert work.exists() and work.stat().st_size > 0

    # 未改动就回填 → 拒绝
    r = client.post(f"/api/panels/{panel['id']}/external-import")
    assert r.status_code == 400, r.text

    # 模拟人工修改：画一道白线
    img = Image.open(work).convert("RGB")
    for i in range(30):
        img.putpixel((20 + i, 30), (255, 255, 255))
    img.save(work)

    r = client.post(f"/api/panels/{panel['id']}/external-import")
    assert r.status_code == 200, r.text
    assert r.json()["candidate"]["stage"] == "manual"
    detail = client.get(f"/api/projects/{pid}").json()
    stages = [c["stage"] for c in detail["pages"][0]["panels"][0]["candidates"]]
    assert "manual" in stages, stages


def _candidate_params(cand_id: str) -> str:
    from sqlmodel import Session
    import app.db as db_mod
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
