"""中文文字框（字体/禁则）+ ComfyUI 接入 测试。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import SQLModel, create_engine

from app import config
from app.pipeline import bubbles, worker


# ── 中文排版 ────────────────────────────────────────

def test_wrap_keeps_closing_punctuation_off_line_start():
    """禁则：收尾标点（。，！？」）不允许出现在行首。"""
    font = bubbles._load_font(24, "heiti")
    text = "这是一个测试句子，标点不能出现在行首。真的不能！"
    for width in (90, 110, 130, 150, 170):
        lines = bubbles._wrap_cjk(text, font, width)
        for line in lines[1:]:
            assert line[0] not in bubbles.NO_LINE_START, (width, lines)


def test_wrap_keeps_opening_punctuation_off_line_end():
    """禁则：起始标点（「（《）不允许留在行尾。"""
    font = bubbles._load_font(24, "heiti")
    text = "他说「这里有一个很长的引用内容需要换行处理」然后离开了"
    for width in (100, 120, 140, 160):
        lines = bubbles._wrap_cjk(text, font, width)
        for line in lines[:-1]:
            assert line[-1] not in bubbles.NO_LINE_END, (width, lines)


def test_font_presets_resolve_to_cjk_fonts():
    """四种预设都能加载到真正的中文字体文件（而不是 Pillow 内置拉丁字体）。"""
    for preset in config.FONT_PRESETS:
        font = bubbles._load_font(24, preset)
        # 中文字形必须有实际宽度（内置拉丁位图字体会量到 0）
        assert font.getlength("中文测试") > 0, preset
        used = getattr(font, "path", "")
        assert used in config.FONT_PRESETS[preset], (preset, used)


def test_bubble_text_renders_chinese_pixels():
    """中文字框必须真的画出墨迹（不是空白/方块）。"""
    img = Image.new("RGB", (500, 400), (255, 255, 255))
    bubbles.draw_dialogue(
        img, {"speaker": "林夏", "text": "你终于来了。", "type": "speech"},
        (0, 0, 500, 400), font_preset="songti", size_scale=1.2,
    )
    # 统计非白像素：文字+气泡描边应该留下可观墨迹
    dark = sum(1 for p in img.getdata() if sum(p) < 600)
    assert dark > 500, dark


# ── API：项目设置 + 渲染字体 ─────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "cjk.db"
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


def test_fonts_endpoint(client):
    fonts = client.get("/api/fonts").json()
    keys = {f["key"] for f in fonts}
    assert {"heiti", "songti", "light"} <= keys
    assert all(f["label"] for f in fonts)


def test_project_bubble_settings_roundtrip_and_render(client):
    pid = client.post("/api/projects", json={"name": "中文框", "style": "manga_bw"}).json()["id"]
    r = client.put(f"/api/projects/{pid}", json={"bubble_style": "songti", "font_scale": 1.4})
    assert r.status_code == 200, r.text
    assert r.json()["bubble_style"] == "songti"
    assert abs(r.json()["font_scale"] - 1.4) < 1e-6
    # 越界字号被钳制
    r = client.put(f"/api/projects/{pid}", json={"font_scale": 9.0})
    assert r.json()["font_scale"] == 2.0

    client.post(f"/api/projects/{pid}/storyboard",
                json={"story": "雨夜，林夏站在校门口。她说：「你终于来了。」",
                      "pages": 1, "panels_per_page": 1, "use_llm": False})
    _wait(client, client.get("/api/jobs").json()[0]["id"])
    detail = client.get(f"/api/projects/{pid}").json()
    page = detail["pages"][0]
    r = client.post(f"/api/pages/{page['id']}/render", json={"font_preset": "light", "size_scale": 1.0})
    assert r.status_code == 200, r.text
    from pathlib import Path
    out = Image.open(Path(r.json()["page_png"]))
    assert out.size == (config.PAGE_W, config.PAGE_H)


def test_project_migration_adds_bubble_columns(tmp_path, monkeypatch):
    """老库（没有 bubble_style 列）启动后应自动补列。"""
    from sqlalchemy import text
    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT, style TEXT, created_at DATETIME)"))
        conn.commit()
    monkeypatch.setattr(config, "DB_PATH", db)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod._ensure_columns()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(project)"))}
    assert {"bubble_style", "font_scale"} <= cols


# ── ComfyUI 模板容错 ────────────────────────────────

def test_comfy_workflow_skips_non_node_keys(tmp_path, monkeypatch):
    """手工编辑过的模板里若有 _meta / 注释等非节点键，必须被跳过（否则 ComfyUI 报 missing_node_type）。"""
    wf = tmp_path / "panel.json"
    wf.write_text(json.dumps({
        "_meta": {"说明": "注释不应被当成节点"},
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "_role": "positive", "inputs": {"text": "", "clip": ["1", 1]}},
        "note": "这也是非节点",
    }))
    monkeypatch.setattr("app.providers.comfyui.WORKFLOW_PATH", wf)
    from app.providers.comfyui import load_workflow
    loaded = load_workflow()
    assert set(loaded) == {"1", "2"}
    assert loaded["2"]["_role"] == "positive"


def test_comfy_provider_unavailable_when_server_down(monkeypatch):
    from app.providers.comfyui import ComfyUIProvider
    monkeypatch.setattr(config, "COMFYUI_URL", "http://127.0.0.1:59999")
    assert ComfyUIProvider().available() is False


def _wait(client, job_id, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
