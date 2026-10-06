"""候选质量评分与自动选最佳（AI Panel Ranking）测试。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Candidate, Panel
from app.pipeline import worker
from app.pipeline.quality import _extract_json, combine, objective_metrics, score_objective


def _sharp_manga(path, size=(320, 480)):
    """高对比线稿风：许多黑线条 + 白色底（模拟漫画分镜）。"""
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    for i in range(0, size[0], 8):
        d.line([(i, 0), (i, size[1])], fill=(10, 10, 10), width=2)
    for i in range(0, size[1], 12):
        d.line([(0, i), (size[0], i)], fill=(30, 30, 30), width=2)
    d.ellipse([80, 120, 240, 340], outline=(0, 0, 0), width=4)
    img.save(path)
    return path


def _blurry(path, size=(320, 480)):
    img = Image.new("RGB", size, (200, 200, 200))
    d = ImageDraw.Draw(img)
    d.ellipse([80, 120, 240, 340], fill=(180, 180, 180))
    img.save(path)
    return path


def _blank(path, size=(320, 480)):
    Image.new("RGB", size, (250, 250, 250)).save(path)
    return path


def _gray_but_color_expected(path, size=(320, 480)):
    img = Image.new("RGB", size, (128, 128, 128))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 300, 460], outline=(60, 60, 60), width=3)
    img.save(path)
    return path


def _colorful(path, size=(320, 480)):
    img = Image.new("RGB", size, (40, 90, 180))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 300, 460], fill=(220, 80, 60), outline=(20, 20, 20), width=3)
    d.ellipse([80, 120, 240, 340], fill=(240, 220, 90), outline=(0, 0, 0), width=4)
    img.save(path)
    return path


# ── 客观指标 ────────────────────────────────────────

def test_objective_metrics_sanity(tmp_path):
    m = objective_metrics(_sharp_manga(tmp_path / "a.png"))
    assert m["sharpness"] > 0 and m["contrast"] > 0
    assert m["size"] == [320, 480]
    assert m["unique_colors"] > 2


def test_sharp_scores_higher_than_blurry(tmp_path):
    sharp = score_objective(_sharp_manga(tmp_path / "s.png"))
    blur = score_objective(_blurry(tmp_path / "b.png"))
    assert sharp["score"] > blur["score"], (sharp["score"], blur["score"])
    assert any("糊" in i for i in blur["issues"]) or blur["score"] < 70


def test_blank_image_is_flagged(tmp_path):
    r = score_objective(_blank(tmp_path / "x.png"))
    assert r["score"] < 60, r
    assert any("信息量" in i for i in r["issues"]), r


def test_color_expectation_respects_style(tmp_path):
    gray = _gray_but_color_expected(tmp_path / "g.png")
    color = _colorful(tmp_path / "c.png")
    # 彩色项目：灰图应被扣分并标记
    gray_color_project = score_objective(gray, expect_color=True)
    color_project = score_objective(color, expect_color=True)
    assert color_project["score"] > gray_color_project["score"]
    assert any("无色" in i for i in gray_color_project["issues"])
    # 黑白项目：灰阶图不该因此扣分（同一张图，黑白项目得分更高）
    gray_bw_project = score_objective(gray, expect_color=False)
    assert gray_bw_project["score"] > gray_color_project["score"]


def test_extreme_exposure_detected(tmp_path):
    dark = Image.new("RGB", (200, 200), (8, 8, 8))
    p = tmp_path / "dark.png"
    dark.save(p)
    r = score_objective(p)
    assert any("过暗" in i for i in r["issues"]), r


# ── 组合与解析 ──────────────────────────────────────

def test_combine_weights_objective_and_vlm(tmp_path):
    obj = score_objective(_colorful(tmp_path / "c.png"))
    only_obj = combine(obj, None)
    assert only_obj["score"] == obj["score"]

    with_vlm = combine(obj, {"score": 10, "issues": ["与描述不符"], "comment": "还行"})
    assert with_vlm["score"] > only_obj["score"]
    assert "与描述不符" in with_vlm["issues"]
    assert with_vlm["comment"] == "还行"


def test_vlm_json_extraction_handles_think_and_noise():
    text = '好的，我的评估如下：\n<think>先看看构图…</think>\n{"score": 7, "issues": ["手部崩坏"], "comment": "构图不错"}'
    data = _extract_json(text)
    assert data and data["score"] == 7 and data["issues"] == ["手部崩坏"]
    assert _extract_json("完全没有 JSON") is None


# ── API：评分 / 排序 / 自动选最佳 ────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "ranking.db"
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


def _panel_with_candidates(client, tmp_path):
    """造一个分镜格 + 3 张质量差异明显的候选（直接写文件与 DB 行）。"""
    from sqlmodel import Session
    import app.db as db_mod
    from app.models import Page, Project

    pid = client.post("/api/projects", json={"name": "评分", "style": config.DEFAULT_STYLE}).json()["id"]
    with Session(db_mod.engine) as s:
        page = Page(project_id=pid, index=1)
        s.add(page)
        s.commit()
        panel = Panel(project_id=pid, page_id=page.id, index=1, action="少女站在雨中")
        s.add(panel)
        s.commit()
        d = config.PROJECTS_DIR / pid / "panels" / panel.id / "draft"
        d.mkdir(parents=True, exist_ok=True)
        made = []
        for name, fn in (("good.png", _colorful), ("blur.png", _blurry), ("blank.png", _blank)):
            p = fn(d / name)
            c = Candidate(panel_id=panel.id, path=str(p.relative_to(config.PROJECTS_DIR)),
                          provider="test", stage="draft", seed=1)
            s.add(c)
            s.commit()
            made.append(c.id)
        return pid, panel.id, made


def test_score_then_ranking_and_autoselect(client, tmp_path):
    pid, panel_id, cand_ids = _panel_with_candidates(client, tmp_path)

    r = client.post(f"/api/panels/{panel_id}/score", json={"use_vlm": False, "only_unscored": True})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result_dict"]["scored"] == 3

    ranking = client.get(f"/api/panels/{panel_id}/ranking").json()
    assert len(ranking) == 3
    scores = [c["score"] for c in ranking]
    assert scores == sorted(scores, reverse=True), scores
    assert all(s > 0 for s in scores)
    # 彩色好图应排第一（比糊图/空图分高）
    assert ranking[0]["path"].endswith("good.png"), [c["path"] for c in ranking]

    # 未评分前不能自动选
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["pages"][0]["panels"][0]["best_candidate_id"] == ranking[0]["id"]

    r = client.post(f"/api/panels/{panel_id}/auto-select-best")
    assert r.status_code == 200, r.text
    assert r.json()["selected"] == ranking[0]["id"]
    detail = client.get(f"/api/projects/{pid}").json()
    p = detail["pages"][0]["panels"][0]
    assert p["selected_candidate_id"] == ranking[0]["id"]
    assert p["status"] == "approved"


def test_score_only_unscored_is_idempotent(client, tmp_path):
    pid, panel_id, _ = _panel_with_candidates(client, tmp_path)
    job = _wait(client, client.post(f"/api/panels/{panel_id}/score",
                                    json={"use_vlm": False, "only_unscored": True}).json()["id"])
    assert job["result_dict"]["scored"] == 3
    job2 = _wait(client, client.post(f"/api/panels/{panel_id}/score",
                                     json={"use_vlm": False, "only_unscored": True}).json()["id"])
    assert job2["result_dict"]["scored"] == 0        # 已评过的不重复评
    job3 = _wait(client, client.post(f"/api/panels/{panel_id}/score",
                                     json={"use_vlm": False, "only_unscored": False}).json()["id"])
    assert job3["result_dict"]["scored"] == 3        # 强制重评


def test_auto_select_requires_scores(client, tmp_path):
    pid, panel_id, _ = _panel_with_candidates(client, tmp_path)
    r = client.post(f"/api/panels/{panel_id}/auto-select-best")
    assert r.status_code == 400 and "评分" in r.json()["detail"]


def test_project_wide_scoring_and_autoselect(client, tmp_path):
    pid, panel_id, _ = _panel_with_candidates(client, tmp_path)
    job = _wait(client, client.post(f"/api/projects/{pid}/score-candidates",
                                    json={"use_vlm": False, "only_unscored": True}).json()["id"])
    assert job["status"] == "done" and job["result_dict"]["scored"] == 3

    r = client.post(f"/api/projects/{pid}/auto-select-best", json={})
    assert r.json()["changed"] == 1
    # 再跑一次：已是最佳，不应重复变更
    r = client.post(f"/api/projects/{pid}/auto-select-best", json={})
    assert r.json()["changed"] == 0


def test_vlm_request_without_model_is_rejected(client, tmp_path, monkeypatch):
    """VLM 不可用时给出明确错误，而不是静默降级（避免用户以为做了 VLM 点评）。"""
    pid, panel_id, _ = _panel_with_candidates(client, tmp_path)
    monkeypatch.setattr(config, "MLX_VLM_BIN", __import__("pathlib").Path("/nonexistent/mlx_vlm.generate"))
    job = _wait(client, client.post(f"/api/panels/{panel_id}/score",
                                    json={"use_vlm": True}).json()["id"])
    assert job["status"] == "failed"
    assert "VLM 不可用" in job["error"]


def test_candidate_score_columns_migrated(tmp_path, monkeypatch):
    from sqlalchemy import text
    db = tmp_path / "old3.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE candidate (id TEXT PRIMARY KEY, panel_id TEXT, path TEXT)"))
        conn.commit()
    monkeypatch.setattr(config, "DB_PATH", db)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod._ensure_columns()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(candidate)"))}
    assert {"score", "score_detail", "scored_at"} <= cols


def _wait(client, job_id, timeout=120):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
