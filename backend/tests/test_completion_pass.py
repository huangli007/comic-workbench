"""收官批次测试：一键升级画风 / 分镜编辑 / 多语言对白与导出 / 首页统计。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Candidate, Page, Panel, Project
from app.pipeline import translate as translate_mod
from app.pipeline import worker


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "final.db"
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


LONG_STORY = ('雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」'
              '陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」'
              '两人并肩走过空无一人的街道。林夏看着地面，沉默良久。'
              '在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')


def _make_project(client, name="收官", pages=2):
    pid = client.post("/api/projects", json={"name": name, "style": "manga_bw"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": LONG_STORY, "pages": pages, "panels_per_page": 2, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    return pid, detail


# ── 一键升级画风 ────────────────────────────────────

def test_upgrade_style_end_to_end(client, tmp_path):
    pid, detail = _make_project(client)
    panels = [p for pg in detail["pages"] for p in pg["panels"]]
    assert len(panels) >= 2

    # 先给所有格出 mock 图（否则升级任务会跳过没图的格子）
    r = client.post(f"/api/projects/{pid}/batch-draft",
                    json={"count": 1, "only_missing": True, "provider": "mock"})
    for jid in r.json()["job_ids"]:
        _wait(client, jid)

    # 加一个人物以便验证设定图也换风格
    client.post(f"/api/projects/{pid}/characters",
                json={"name": "林夏", "appearance": "black bob hair, school uniform"})

    r = client.post(f"/api/projects/{pid}/upgrade-style",
                    json={"style": "manhua_color", "provider": "mock", "steps": 3})
    job = _wait(client, r.json()["id"], timeout=120)
    assert job["status"] == "done", job["error"]
    res = job["result_dict"]
    assert res["style"] == "manhua_color"
    assert res["panels_rerun"] == len(panels)
    assert res["pages_rendered"] >= 1

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["style"] == "manhua_color"
    # 新候选的 prompt 应该是彩色
    for pg in detail["pages"]:
        for p in pg["panels"]:
            assert "full color" in p["prompt"].lower(), p["prompt"][:80]

    # 升级后旧文件不被覆盖（新候选是新文件）
    paths = [c["path"] for pg in detail["pages"] for p in pg["panels"] for c in pg_panels_candidates(p)]
    assert len(paths) == len(set(paths))


def pg_panels_candidates(p):
    return p.get("candidates", [])


# ── 分镜编辑 ────────────────────────────────────────

def test_panel_delete_duplicate_and_create(client):
    pid, detail = _make_project(client)
    pages = detail["pages"]
    panels_p1 = pages[0]["panels"]
    assert len(panels_p1) >= 1
    first = panels_p1[0]

    # 复制第一格 → 数量 +1，新格继承设定与候选图
    r = client.post(f"/api/panels/{first['id']}/duplicate")
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/projects/{pid}").json()
    p1 = detail["pages"][0]["panels"]
    assert len(p1) == len(panels_p1) + 1
    dup = [p for p in p1 if p["id"] != first["id"] and p["action"] == first["action"]]
    assert dup, "应有复制的格子"
    # 重排序：index 连续
    assert [p["index"] for p in sorted(p1, key=lambda x: x["index"])] == list(range(1, len(p1) + 1))

    # 删除一格 → 数量 -1，重排序
    victim = p1[0]["id"]
    assert client.delete(f"/api/panels/{victim}").status_code == 200
    detail = client.get(f"/api/projects/{pid}").json()
    p1 = detail["pages"][0]["panels"]
    assert len(p1) == len(panels_p1)
    assert [p["index"] for p in sorted(p1, key=lambda x: x["index"])] == list(range(1, len(p1) + 1))
    assert all(p["id"] != victim for p in p1)

    # 页面末尾新增空格
    page_id = detail["pages"][0]["id"]
    r = client.post(f"/api/pages/{page_id}/panels", json={"action": "新加的一格"})
    assert r.status_code == 200
    detail = client.get(f"/api/projects/{pid}").json()
    p1 = detail["pages"][0]["panels"]
    assert p1[-1]["action"] == "新加的一格"
    assert p1[-1]["index"] == len(p1)


# ── 多语言对白与导出 ────────────────────────────────

def test_translation_flow_with_fake_llm(client, tmp_path, monkeypatch):
    """monkeypatch 掉 mlx_lm 子进程，验证翻译→落库→按语言渲染→导出命名。"""
    pid, detail = _make_project(client, name="多语言")
    panel_ids = [p["id"] for pg in detail["pages"] for p in pg["panels"]]

    # 伪造 LLM 输出：把每条对白翻译成 "EN:<原文>"
    def fake_run(*args, **kwargs):
        prompt = ""
        for a in args[0]:
            if "对白：" in str(a):
                prompt = str(a)
        import re
        pairs = re.findall(r"(d\d+)\. (.+)", prompt)
        trs = {k: f"EN:{v}" for k, v in pairs}
        out = json.dumps({"translations": trs}, ensure_ascii=False)

        class R:
            returncode = 0
            stderr = ""
            stdout = out
        return R()

    monkeypatch.setattr(translate_mod.subprocess, "run", fake_run)

    r = client.post(f"/api/projects/{pid}/translate", json={"langs": ["en"]})
    assert r.status_code == 200, r.text
    for j in r.json()["jobs"]:
        job = _wait(client, j["id"])
        assert job["status"] == "done", job["error"]
    res = [j for j in client.get("/api/jobs").json()
           if j["type"] == "translate_dialogues"][-1]["result_dict"]
    assert res["translated"] > 0

    detail = client.get(f"/api/projects/{pid}").json()
    for pg in detail["pages"]:
        for p in pg["panels"]:
            tr = json.loads(p["dialogue_translations"])
            if p["dialogue_list"]:
                assert "en" in tr, p["id"]
                for orig, t in zip(p["dialogue_list"], tr["en"]):
                    assert t["text"].startswith("EN:"), t

    # 按英文渲染 → 页面文件带 _en 后缀；导出目录分语言
    page = detail["pages"][0]
    r = client.post(f"/api/pages/{page['id']}/render", json={"lang": "en"})
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid}/export", json={"fmt": "pdf", "lang": "en"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert "_en" in job["result_dict"]["files"][0]

    # 导出清单里能看到多语言文件
    files = client.get(f"/api/projects/{pid}/exports").json()
    assert any(f["fmt"] == "pdf_en" for f in files), files


def test_translate_rejects_unknown_lang(client):
    pid, _ = _make_project(client, name="语言校验")
    r = client.post(f"/api/projects/{pid}/translate", json={"langs": ["klingon"]})
    job = _wait(client, r.json()["jobs"][0]["id"])
    assert job["status"] == "failed"
    assert "不支持的语言" in job["error"]


# ── 首页统计 ────────────────────────────────────────

def test_home_stats_present(client):
    pid = client.post("/api/projects", json={"name": "统计"}).json()["id"]
    stats = client.get("/api/stats").json()
    assert stats["project_count"] >= 1
    assert "panel_count" in stats and "asset_count" in stats
    projs = client.get("/api/projects").json()
    row = [p for p in projs if p["id"] == pid][0]
    assert row["panel_count"] == 0 and row["asset_count"] == 0


def _wait(client, job_id, timeout=120):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
