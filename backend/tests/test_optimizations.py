"""优化项测试：风格推荐参数 / 批量出图 / 资产重跑 / 导出清单。"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Candidate, Panel
from app.pipeline import worker


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "opt.db"
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


# ── 风格推荐参数 ────────────────────────────────────

def test_every_style_has_recommended_params():
    for s in config.STYLE_LIBRARY:
        assert s.get("steps"), s["key"]
        assert 3 <= s["steps"] <= 12, s["key"]
        # cfg_hint 是 ComfyUI/SD 语义的参考 cfg（不是 FLUX 的 guidance）
        assert 4.0 <= (s.get("cfg_hint") or 0) <= 10.0, s["key"]
    # 细节密集型应比平涂型步数更多
    rec = {s["key"]: s["steps"] for s in config.STYLE_LIBRARY}
    assert rec["mecha_scifi"] > rec["chibi_comedy"]
    assert rec["xuanhuan_epic"] > rec["guofeng_ink"]


def test_style_rec_fallback_and_api_exposes_it(client):
    assert config.style_rec("不存在的风格")["steps"] == config.DRAFT_STEPS
    assert config.style_rec("mecha_scifi")["steps"] == 9
    assert config.style_rec("guofeng_ink")["cfg"] == 5.5
    styles = client.get("/api/styles").json()["styles"]
    assert all("steps" in s and s["steps"] for s in styles)
    assert all("cfg_hint" in s and s["cfg_hint"] for s in styles)


def test_worker_uses_style_steps(client):
    """未显式指定步数时，候选参数应等于该风格的推荐步数。"""
    pid = client.post("/api/projects", json={"name": "步数", "style": "chibi_comedy"}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "小女孩在街上蹦跳。", "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    r = client.post(f"/api/panels/{panel['id']}/draft", json={"count": 1, "provider": "mock"})
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job["error"]
    detail = client.get(f"/api/projects/{pid}").json()
    cand = detail["pages"][0]["panels"][0]["candidates"][0]
    import json
    params = json.loads(_params(cand["id"]))
    assert params["steps"] == config.style_rec("chibi_comedy")["steps"], params


def test_mlx_provider_never_passes_invalid_guidance():
    """flux2-klein 只接受 --guidance 1.0，其它值会让 CLI exit=2（曾因此批量任务全挂）。"""
    from app.providers.base import GenRequest
    from app.providers.mlx_provider import _common_args

    def cmd_for(g):
        req = GenRequest(prompt="p", output_path=__import__("pathlib").Path("/tmp/x.png"), guidance=g)
        return _common_args(req, "m")

    assert "--guidance" not in cmd_for(0.0)
    assert "--guidance" not in cmd_for(1.2)          # 非法值直接不传
    assert "--guidance" in cmd_for(1.0)              # 合法值可显式传


# ── 批量出图 ────────────────────────────────────────

def test_batch_draft_skips_panels_with_candidates(client):
    pid = _project_with_panels(client, pages=2, per_page=2, story='雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」两人并肩走过空无一人的街道。林夏看着地面，沉默良久。在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')
    detail = client.get(f"/api/projects/{pid}").json()
    panels = [p for pg in detail["pages"] for p in pg["panels"]]
    assert len(panels) >= 2

    # 先给第一格出一张
    r = client.post(f"/api/panels/{panels[0]['id']}/draft", json={"count": 1, "provider": "mock"})
    _wait(client, r.json()["id"])

    r = client.post(f"/api/projects/{pid}/batch-draft",
                    json={"count": 1, "only_missing": True, "provider": ""})
    body = r.json()
    assert body["enqueued"] == len(panels) - 1, body
    for jid in body["job_ids"]:
        assert _wait(client, jid)["status"] == "done"

    # 再跑一次（都已有图）→ 不应重复入队
    r = client.post(f"/api/projects/{pid}/batch-draft", json={"count": 1, "only_missing": True})
    assert r.json()["enqueued"] == 0

    # 不限 only_missing 则全部重跑
    r = client.post(f"/api/projects/{pid}/batch-draft", json={"count": 1, "only_missing": False})
    assert r.json()["enqueued"] == len(panels)


def test_batch_draft_can_limit_to_one_page(client):
    pid = _project_with_panels(client, pages=2, per_page=2, story='雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」两人并肩走过空无一人的街道。林夏看着地面，沉默良久。在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')
    detail = client.get(f"/api/projects/{pid}").json()
    page1 = detail["pages"][0]
    r = client.post(f"/api/projects/{pid}/batch-draft",
                    json={"count": 1, "page_id": page1["id"], "only_missing": True})
    assert r.json()["enqueued"] == len(page1["panels"])


# ── 资产改动 → 重跑引用分镜 ─────────────────────────

def test_regenerate_panels_referencing_asset(client):
    # 页数要够，否则故事的「在…」句子被截断，抽不到场景资产
    pid = _project_with_panels(client, pages=3, per_page=2, story='雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」两人并肩走过空无一人的街道。林夏看着地面，沉默良久。在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["assets"]["scene"], "故事中的地点应抽成场景资产"
    scene = detail["assets"]["scene"][0]
    panels = [p for pg in detail["pages"] for p in pg["panels"]]
    # 让前两格引用该场景（故事拆解可能已自动挂上，这里显式指定保证可预期）
    refs = panels[:2]
    for p in refs:
        client.put(f"/api/panels/{p['id']}", json={"scene_id": scene["id"]})

    r = client.post(f"/api/assets/{scene['id']}/regenerate", json={"count": 1})
    body = r.json()
    # 预期 = 所有真正引用该场景的格子（故事拆解可能已自动挂上部分格子）
    detail2 = client.get(f"/api/projects/{pid}").json()
    expected = [
        p for pg in detail2["pages"] for p in pg["panels"] if p["scene_id"] == scene["id"]
    ]
    assert expected, "应至少有一格引用该场景"
    assert body["enqueued"] == len(expected), body
    for jid in body["job_ids"]:
        assert _wait(client, jid)["status"] == "done"

    # 未引用任何分镜的资产 → 明确提示而不是报错
    other = client.post(f"/api/projects/{pid}/assets", json={"kind": "prop", "name": "孤品"}).json()
    r = client.post(f"/api/assets/{other['id']}/regenerate", json={"count": 1})
    assert r.json()["enqueued"] == 0 and r.json()["message"]


def test_regenerate_panels_referencing_character(client):
    pid = _project_with_panels(client, pages=1, per_page=2, story='雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」两人并肩走过空无一人的街道。林夏看着地面，沉默良久。在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')
    detail = client.get(f"/api/projects/{pid}").json()
    chars = detail["characters"]
    assert chars, detail
    ch = chars[0]
    panels = detail["pages"][0]["panels"]
    client.put(f"/api/panels/{panels[0]['id']}", json={"characters": [ch["id"]]})
    r = client.post(f"/api/characters/{ch['id']}/regenerate", json={"count": 1})
    assert r.json()["enqueued"] == 1


# ── 导出清单 ────────────────────────────────────────

def test_export_listing_and_download(client):
    pid = _project_with_panels(client, pages=1, per_page=1, story='雨夜，林夏撑着黑伞站在校门口。她轻声说：「你终于来了。」陈默从街角跑来，浑身湿透。他喊：「对不起，我迟到了！」两人并肩走过空无一人的街道。林夏看着地面，沉默良久。在学校后面的旧图书馆前，林夏停下脚步。她说：「我要转学了。」')
    detail = client.get(f"/api/projects/{pid}").json()
    panel = detail["pages"][0]["panels"][0]
    r = client.post(f"/api/panels/{panel['id']}/draft", json={"count": 1, "provider": "mock"})
    _wait(client, r.json()["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    client.post(f"/api/candidates/{detail['pages'][0]['panels'][0]['candidates'][0]['id']}/select")
    client.post(f"/api/pages/{detail['pages'][0]['id']}/render")
    for fmt in ("pdf", "cbz", "png"):
        job = _wait(client, client.post(f"/api/projects/{pid}/export", json={"fmt": fmt}).json()["id"])
        assert job["status"] == "done", job["error"]

    files = client.get(f"/api/projects/{pid}/exports").json()
    fmts = {f["fmt"] for f in files}
    assert {"pdf", "cbz", "png"} <= fmts, files
    for f in files:
        assert f["size"] > 0 and f["url"].startswith("/files/")
        assert client.get(f["url"]).status_code == 200      # 下载链接可用
    # 最新导出的排在前面
    assert files[0]["mtime"] >= files[-1]["mtime"]


def _params(cand_id: str) -> str:
    from sqlmodel import Session
    import app.db as db_mod
    with Session(db_mod.engine) as s:
        return s.get(Candidate, cand_id).params


def _project_with_panels(client, pages: int, per_page: int, story: str) -> str:
    pid = client.post("/api/projects", json={"name": "批量", "style": config.DEFAULT_STYLE}).json()["id"]
    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": story, "pages": pages, "panels_per_page": per_page, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    return pid


def _wait(client, job_id, timeout=90):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
