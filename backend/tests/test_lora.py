"""角色 LoRA（MVP-3）链路测试：数据集 / 配置 / 生成挂载 / 迁移。

训练本身需要完整精度权重（本机只有 8bit），所以真正跑训练的部分用 monkeypatch 验证命令装配，
其余链路（数据集、配置、生成挂载、字段迁移）全部真实执行。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Character, Panel, Project
from app.pipeline import lora as lora_mod
from app.pipeline import worker


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "lora.db"
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


def _project_with_character(client, style=config.DEFAULT_STYLE, name="LoRA"):
    pid = client.post("/api/projects", json={"name": name, "style": style}).json()["id"]
    ch = client.post(f"/api/projects/{pid}/characters",
                     json={"name": "林夏", "appearance": "black bob hair, school uniform"}).json()
    return pid, ch["id"]


def _fake_image(path: Path, color=(120, 150, 200)):
    Image.new("RGB", (64, 96), color).save(path)
    return path


# ── 数据集准备 ──────────────────────────────────────

def test_prepare_dataset_builds_flat_pairs(client, tmp_path):
    pid, cid = _project_with_character(client)
    from sqlmodel import Session
    import app.db as db_mod
    with Session(db_mod.engine) as s:
        project = s.get(Project, pid)
        ch = s.get(Character, cid)
        # 造两张当前画风的设定图
        d = config.project_dir(pid) / "characters" / cid
        d.mkdir(parents=True, exist_ok=True)
        _fake_image(d / f"sheet_{project.style}_1.png")
        _fake_image(d / f"sheet_{project.style}_2.png")
        data_dir, n = lora_mod.prepare_dataset(s, project, ch)
        assert n == 2, n
        imgs = sorted(data_dir.glob("*.png"))
        assert len(imgs) == 2
        for im in imgs:
            txt = im.with_suffix(".txt")
            assert txt.exists(), "每张图都要有同名提示词"
            assert "林夏" in txt.read_text(encoding="utf-8")


def test_prepare_dataset_collects_same_name_across_projects(client, tmp_path):
    """样本不足时，从其它项目收集同名角色的图（同一角色的跨项目素材）。"""
    from sqlmodel import Session
    import app.db as db_mod
    from app.models import Panel, Character

    # 项目 A：同名角色但只有 1 张设定图（样本不足）
    pid_a, cid_a = _project_with_character(client, name="跨项目A")
    # 项目 B：同名角色，另有 2 张设定图
    pid_b = client.post("/api/projects", json={"name": "跨项目B", "style": config.DEFAULT_STYLE}).json()["id"]
    ch_b = client.post(f"/api/projects/{pid_b}/characters",
                       json={"name": "林夏", "appearance": "black bob hair"}).json()
    cid_b = ch_b["id"]

    with Session(db_mod.engine) as s:
        d_b = config.project_dir(pid_b) / "characters" / cid_b
        d_b.mkdir(parents=True, exist_ok=True)
        _fake_image(d_b / f"sheet_{config.DEFAULT_STYLE}_1.png", (10, 20, 30))
        _fake_image(d_b / f"sheet_{config.DEFAULT_STYLE}_2.png", (30, 40, 50))

        d_a = config.project_dir(pid_a) / "characters" / cid_a
        d_a.mkdir(parents=True, exist_ok=True)
        _fake_image(d_a / f"sheet_{config.DEFAULT_STYLE}_9.png", (200, 100, 100))

        project_a = s.get(Project, pid_a)
        ch_a = s.get(Character, cid_a)
        data_dir, n = lora_mod.prepare_dataset(s, project_a, ch_a)
        assert n >= 3, f"应收集到本项目 1 张 + 跨项目 2 张，实际 {n}"
        names = sorted(f.name for f in data_dir.glob("*.png"))
        assert len(names) == n
        for f in data_dir.glob("*.png"):
            assert f.with_suffix(".txt").exists()


def test_prepare_dataset_zero_images_raises(client):
    pid, cid = _project_with_character(client)
    from sqlmodel import Session
    import app.db as db_mod
    with Session(db_mod.engine) as s:
        project = s.get(Project, pid)
        ch = s.get(Character, cid)
        _, n = lora_mod.prepare_dataset(s, project, ch)
        assert n == 0
        with pytest.raises(RuntimeError):
            lora_mod.train_character_lora(s, pid, cid, epochs=1)


# ── 训练配置 ────────────────────────────────────────

def test_build_train_config_shape(tmp_path):
    out = tmp_path / "training"
    out.mkdir()
    cfg_path = lora_mod.build_train_config(tmp_path / "data", out, epochs=9, rank=8, lr=5e-5)
    cfg = json.loads(cfg_path.read_text())
    assert cfg["model"] == lora_mod.LORA_BASE_MODEL          # 必须用内置名
    assert cfg["training_loop"]["num_epochs"] == 9
    assert cfg["monitoring"]["generate_image_frequency"] > 0  # 否则 mflux 直接报错
    # data / output_path 必须是绝对路径（mflux 按 config 目录解析相对路径，会拼重复）
    assert Path(cfg["data"]).is_absolute(), cfg["data"]
    assert Path(cfg["checkpoint"]["output_path"]).is_absolute(), cfg["checkpoint"]["output_path"]
    targets = cfg["lora_layers"]["targets"]
    assert targets and all(t["rank"] == 8 for t in targets)
    assert any("single_transformer_blocks" in t["module_path"] for t in targets)


def test_train_job_runs_command_and_records_adapter(client, monkeypatch, tmp_path):
    """用假 mflux-train 验证：命令装配 + 产物登记到 character.lora_path。"""
    pid, cid = _project_with_character(client)
    from sqlmodel import Session
    import app.db as db_mod
    with Session(db_mod.engine) as s:
        ch = s.get(Character, cid)
        d = config.project_dir(pid) / "characters" / cid
        d.mkdir(parents=True, exist_ok=True)
        _fake_image(d / f"sheet_{config.DEFAULT_STYLE}_1.png")

    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        cfg = json.loads(Path(cmd[cmd.index("--config") + 1]).read_text())
        out_dir = Path(cfg["checkpoint"]["output_path"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0000009_adapter.safetensors").write_bytes(b"fake")

        class R:
            returncode = 0
            stdout = "done"
            stderr = ""
        return R()

    monkeypatch.setattr(lora_mod.subprocess, "run", fake_run)
    r = client.post(f"/api/characters/{cid}/train-lora", json={"epochs": 9, "rank": 8})
    assert r.status_code == 200, r.text
    job = _wait(client, r.json()["id"], timeout=120)
    assert job["status"] == "done", job["error"]
    assert job["result_dict"]["lora_path"].endswith("adapter.safetensors")
    assert "--config" in calls["cmd"]

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["characters"][0]["lora_path"], "LoRA 路径应登记到人物上"


# ── 生成时挂载 LoRA ─────────────────────────────────

def test_generation_passes_lora_args(client, monkeypatch):
    from app.providers.base import GenRequest
    from app.providers.mlx_provider import _common_args

    p1 = config.PROJECTS_DIR / "a.safetensors"
    p1.write_bytes(b"x")
    req = GenRequest(prompt="p", output_path=Path("/tmp/out.png"),
                     lora_paths=[p1], lora_scales=[0.8])
    cmd = _common_args(req, "m")
    # 新格式：--lora <path> <scale>（可重复）
    assert "--lora" in cmd and str(p1) in cmd and "0.8" in cmd
    assert "--lora-paths" not in cmd, "不该再用废弃参数"
    i = cmd.index("--lora")
    assert cmd[i + 1] == str(p1) and cmd[i + 2] == "0.8"

    # 多 LoRA：各自一个 --lora，不会错位
    p2 = config.PROJECTS_DIR / "b.safetensors"
    p2.write_bytes(b"y")
    cmd3 = _common_args(GenRequest(prompt="p", output_path=Path("/tmp/o.png"),
                                   lora_paths=[p1, p2], lora_scales=[0.7]), "m")
    assert cmd3.count("--lora") == 2
    j = [k for k, v in enumerate(cmd3) if v == "--lora"]
    assert cmd3[j[0] + 1] == str(p1) and cmd3[j[0] + 2] == "0.7"
    assert cmd3[j[1] + 1] == str(p2) and cmd3[j[1] + 2] == "1.0"   # 缺省补 1.0

    # 没训练过 LoRA 时不带这些参数
    cmd2 = _common_args(GenRequest(prompt="p", output_path=Path("/tmp/out.png")), "m")
    assert "--lora" not in cmd2


def test_character_lora_columns_migrated(tmp_path, monkeypatch):
    from sqlalchemy import text
    db = tmp_path / "old4.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE character (id TEXT PRIMARY KEY, name TEXT)"))
        conn.commit()
    monkeypatch.setattr(config, "DB_PATH", db)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod._ensure_columns()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(character)"))}
    assert {"lora_path", "lora_scale"} <= cols


def test_find_adapter_extracts_from_checkpoint_zip(tmp_path):
    """mflux 把 adapter 打包在 checkpoint.zip 里，必须能自动提取出来。"""
    import zipfile

    out = tmp_path / "training"
    (out / "checkpoints").mkdir(parents=True)
    zp = out / "checkpoints" / "0000024_checkpoint.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("0000024_adapter.safetensors", b"fake-adapter")
        zf.writestr("0000024_optimizer.safetensors", b"fake-opt")

    found = lora_mod.find_adapter(out)
    assert found is not None and found.name == "adapter.safetensors"
    assert found.read_bytes() == b"fake-adapter"

    # 已经有散落的 adapter 时优先用它
    loose = out / "x_adapter.safetensors"
    loose.write_bytes(b"loose")
    assert lora_mod.find_adapter(out).read_bytes() == b"loose"


def _wait(client, job_id, timeout=120):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        for j in client.get("/api/jobs").json():
            if j["id"] == job_id and j["status"] in ("done", "failed"):
                return j
        time.sleep(0.2)
    raise TimeoutError("job 超时")
