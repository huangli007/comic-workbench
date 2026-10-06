"""常用风格库（取材腾讯动漫热门题材族）测试。"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import config
from app.models import Panel, Project
from app.pipeline import worker
from app.pipeline.asset import build_asset_sheet_prompt
from app.pipeline.prompt import apply_style_directive, build_panel_prompt

# 参考腾讯动漫热门题材族 ↔ 风格 key
TENCENT_GENRE_MAP = {
    "玄幻修真": "xuanhuan_epic",          # 斗破苍穹 / 我的徒弟都是大反派 / 我的模拟长生路
    "仙侠唯美": "xianxia_ethereal",       # 狐妖小红娘 / 魔道祖师类
    "仙道诡秘": "dao_weird",              # 道诡异仙
    "古风历史": "ancient_history",        # 绍宋 / 铜雀锁金钗
    "国风水墨": "guofeng_ink",
    "都市校园": "urban_slice",            # 一人之下（日常）
    "都市灵异": "urban_supernatural",     # 中国惊奇先生 / 情绪病
    "少女恋爱": "shoujo_romance",         # 恰似寒光遇骄阳
    "Q版搞笑": "chibi_comedy",            # 我家大师兄脑子有坑 / 非人哉
    "少年热血": "shonen_action",          # 传武 / 拳王归来
    "机甲科幻": "mecha_scifi",            # 星甲魂将传
    "末世求生": "apocalypse",             # 我叫白小飞 / 地球尽头
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "styles_lib.db"
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


def test_style_library_is_complete_and_grouped():
    keys = [s["key"] for s in config.STYLE_LIBRARY]
    assert len(keys) == len(set(keys)), "风格 key 重复"
    assert len(keys) >= 15, f"常用风格太少：{len(keys)}"
    for s in config.STYLE_LIBRARY:
        assert s["label"] and s["group"] and s["desc"], s
        assert s["prompt"], s
        assert s["directive"], f"{s['key']} 缺风格硬指令（会导致彩色退化/黑白漂移）"
        # 无文字约束必须写进正向提示词（FLUX.2 不支持负向词）
        low = s["prompt"].lower()
        assert "no text" in low or "no lettering" in low, s["key"]
    # 分组有序且都非空
    assert len(config.STYLE_GROUPS) >= 4
    for g in config.STYLE_GROUPS:
        assert [s for s in config.STYLE_LIBRARY if s["group"] == g], g


def test_tencent_genre_styles_present():
    """腾讯动漫热门题材族都要有对应风格。"""
    keys = {s["key"] for s in config.STYLE_LIBRARY}
    missing = [g for g, k in TENCENT_GENRE_MAP.items() if k not in keys]
    assert not missing, f"缺少题材风格：{missing}"


def test_color_and_bw_directives_are_correct():
    for s in config.STYLE_LIBRARY:
        d = s["directive"].lower()
        if s["color"]:
            assert "full color" in d, s["key"]
            assert "no black-and-white" in d or "no monochrome" in d, s["key"]
        else:
            assert "black and white" in d or "monochrome" in d, s["key"]
            assert "no color" in d, s["key"]
    # 派生字典与库一致
    assert set(config.STYLE_PRESETS) == {s["key"] for s in config.STYLE_LIBRARY}
    assert set(config.STYLE_DIRECTIVES) == set(config.STYLE_PRESETS)
    assert set(config.STYLE_LABELS) == set(config.STYLE_PRESETS)


def test_styles_api_returns_groups_and_desc(client):
    r = client.get("/api/styles")
    assert r.status_code == 200
    data = r.json()
    assert data["default"] == config.DEFAULT_STYLE
    assert data["default"] in config.COLOR_STYLES
    assert len(data["groups"]) == len(config.STYLE_GROUPS)
    assert len(data["styles"]) == len(config.STYLE_LIBRARY)
    for s in data["styles"]:
        assert s["label"] and s["desc"] and isinstance(s["color"], bool)
    # 每个分组都能取到条目（前端分组渲染的前提）
    for g in data["groups"]:
        assert [s for s in data["styles"] if s["group"] == g]


def test_each_style_produces_sane_prompt():
    """每种风格都能生成"首尾带对应指令 + 正文含风格词"的提示词，且设定图同步跟风格。"""
    for s in config.STYLE_LIBRARY:
        p = Project(name="t", style=s["key"])
        panel = Panel(project_id="p", page_id="pg", index=1, action="少女站在雨中")
        prompt, _ = build_panel_prompt(p, panel, [])
        wrapped = apply_style_directive(s["key"], prompt)
        assert wrapped.startswith(s["directive"]), s["key"]
        assert s["directive"] in wrapped[-len(s["directive"]) - 60:], s["key"]
        assert s["prompt"].split(",")[0] in wrapped, s["key"]
        # 资产设定图也带同一风格
        from app.models import Asset
        a = Asset(project_id="p", kind="scene", name="街口", description="a street corner")
        sheet = apply_style_directive(s["key"], build_asset_sheet_prompt(p, a, "wide"))
        assert sheet.startswith(s["directive"]), s["key"]


def test_new_project_uses_color_default(client):
    pid = client.post("/api/projects", json={"name": "默认风格"}).json()["id"]
    assert client.get(f"/api/projects/{pid}").json()["style"] == config.DEFAULT_STYLE

    # 可以切到新加的题材风格
    for key in ("xuanhuan_epic", "xianxia_ethereal", "urban_supernatural", "sumi_bw"):
        r = client.put(f"/api/projects/{pid}", json={"style": key})
        assert r.status_code == 200 and r.json()["style"] == key

    # 非法风格不写入（保持原值），但接口不炸
    client.put(f"/api/projects/{pid}", json={"style": "sumi_bw"})
    assert client.get(f"/api/projects/{pid}").json()["style"] == "sumi_bw"
