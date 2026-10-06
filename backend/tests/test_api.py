"""API + Worker 全链路测试（mock provider，不依赖真实模型）。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app import config
from app.main import app
from app.pipeline import worker


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "api_test.db"
    from sqlmodel import create_engine
    import app.db as db_mod
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    # worker 用 engine 全局引用
    monkeypatch.setattr(worker, "engine", engine)

    with TestClient(app) as c:  # lifespan: init_db + start_worker（真实 worker 线程）
        yield c
    worker.stop_worker()
    shutil.rmtree(tmp_path / "projects", ignore_errors=True)


STORY = (
    "雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」"
    "陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」"
    "两人并肩走过空无一人的街道。林夏看着地面，沉默良久。"
    "在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」"
    "陈默震惊地看着她，雨水顺着他的头发流下。"
    "林夏转身离去，只留下陈默一个人站在雨中。"
)


def _wait_job(client, job_id, timeout=30):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs").json()
        for j in r:
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.3)
    raise TimeoutError("job 超时")


def test_full_pipeline_with_mock(client):
    # 1. 建项目
    r = client.post("/api/projects", json={"name": "雨夜", "style": "manga_bw"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # 2. 故事 → 分镜（启发式，跳过 LLM）
    r = client.post(f"/api/projects/{pid}/storyboard",
                    json={"story": STORY, "pages": 2, "panels_per_page": 3, "use_llm": False})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = _wait_job(client, job_id)
    assert job["status"] == "done", job["error"]
    assert job["result_dict"]["panels"] >= 4

    # 3. 详情
    detail = client.get(f"/api/projects/{pid}").json()
    assert len(detail["pages"]) >= 1
    panel0 = detail["pages"][0]["panels"][0]

    # 4. 生成候选（mock provider）
    r = client.post(f"/api/panels/{panel0['id']}/draft",
                    json={"count": 2, "provider": "mock"})
    assert r.status_code == 200, r.text
    job = _wait_job(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert len(job["result_dict"]["candidates"]) == 2

    # 5. 选择候选
    detail = client.get(f"/api/projects/{pid}").json()
    panel0 = detail["pages"][0]["panels"][0]
    cand_id = panel0["candidates"][0]["id"]
    r = client.post(f"/api/candidates/{cand_id}/select")
    assert r.status_code == 200

    # 6. 渲染页面
    r = client.post(f"/api/pages/{detail['pages'][0]['id']}/render")
    assert r.status_code == 200, r.text
    png_url = r.json()["url"]
    img_resp = client.get(png_url)
    assert img_resp.status_code == 200 and len(img_resp.content) > 10000

    # 7. 导出三种格式
    for fmt in ("png", "pdf", "cbz"):
        r = client.post(f"/api/projects/{pid}/export", json={"fmt": fmt})
        job = _wait_job(client, r.json()["id"])
        assert job["status"] == "done", f"{fmt}: {job['error']}"
        assert job["result_dict"]["files"]


def test_storyboard_llm_fallback_json():
    from app.pipeline.storyboard import _extract_json, heuristic_storyboard
    data = _extract_json('<think>推理</think>\n{"characters": [], "panels": [{"action": "a"}]}')
    assert data and data["panels"][0]["action"] == "a"
    sb = heuristic_storyboard(STORY)
    assert len(sb["panels"]) >= 3
    assert any(p["dialogue"] for p in sb["panels"])


def test_quote_aware_split_and_speaker_resolution():
    """引号内不断句；代词说话人回指最近的具名角色。"""
    from app.pipeline.storyboard import collect_characters, split_sentences
    sents = split_sentences("雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默跑来。")
    assert sents[0] == "雨夜，林夏撑着黑伞站在校门口"
    # 引号内的句号不切句：「你终于来了。」必须完整保留在同一句里
    assert "「你终于来了。」" in sents[1]
    assert not any(s.startswith("」") for s in sents)
    assert collect_characters(STORY)[:2] == ["林夏", "陈默"]
    from app.pipeline.storyboard import heuristic_storyboard
    sb = heuristic_storyboard(STORY)
    speakers = {d["speaker"] for p in sb["panels"] for d in p["dialogue"]}
    assert "林夏" in speakers and "陈默" in speakers, speakers
