"""气泡 / 排版 / 导出 测试（用合成图，不依赖生成引擎）。"""
from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from app import config
from app.models import Candidate, Page, Panel, Project
from app.pipeline import bubbles, export as export_mod, layout


@pytest.fixture()
def session(tmp_path, monkeypatch):
    # 独立 DB
    db = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    # projects 目录指到 tmp
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    with Session(engine) as s:
        yield s
    shutil.rmtree(tmp_path / "projects", ignore_errors=True)


def _make_project_with_page(s: Session, n_panels=3):
    project = Project(name="测试漫画", style="manga_bw")
    s.add(project)
    s.commit()
    page = Page(project_id=project.id, index=1, template="auto")
    s.add(page)
    s.commit()
    panels = []
    for i in range(n_panels):
        p = Panel(
            project_id=project.id, page_id=page.id, index=i + 1,
            action=f"第{i+1}格动作",
            dialogue=json.dumps([
                {"speaker": "林夏", "text": "这是第 %d 格的对白台词。" % (i + 1), "type": "speech"},
                {"speaker": "", "text": "轰隆隆", "type": "sfx"},
            ], ensure_ascii=False),
        )
        s.add(p)
        panels.append(p)
    s.commit()
    return project, page, panels


def test_cjk_wrap():
    font = bubbles._load_font(24)
    lines = bubbles._wrap_cjk("这是一个很长的中文台词需要自动换行处理", font, 100)
    assert len(lines) > 1
    for line in lines:
        assert font.getlength(line) <= 100


def test_measure_bubble():
    w, h, lines = bubbles.measure_bubble("你好，世界", font_size=24, max_text_w=200)
    assert w > 0 and h > 0


def test_render_page_with_placeholder_and_bubbles(session):
    project, page, panels = _make_project_with_page(session, n_panels=3)
    path = layout.render_page(session, page)
    assert path.exists()
    img = Image.open(path)
    assert img.size == (config.PAGE_W, config.PAGE_H)
    # 3 panel → big2 模板
    assert layout.pick_template(page, panels) == "big2"


def test_render_page_with_candidate_image(session):
    project, page, panels = _make_project_with_page(session, n_panels=1)
    # 造一张候选图
    fake = config.PROJECTS_DIR / project.id / "panels" / panels[0].id / "draft" / "draft_1.png"
    fake.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 480), (200, 100, 100)).save(fake)
    cand = Candidate(
        panel_id=panels[0].id,
        path=str(fake.relative_to(config.PROJECTS_DIR)),
        provider="mock", stage="draft", seed=1,
    )
    session.add(cand)
    session.commit()
    panels[0].selected_candidate_id = cand.id
    panels[0].status = "approved"
    session.add(panels[0])
    session.commit()

    path = layout.render_page(session, page)
    assert path.exists()


def test_export_pdf_cbz_png(session):
    project, page, panels = _make_project_with_page(session, n_panels=4)
    for fmt in ("png", "pdf", "cbz"):
        paths = export_mod.export_project(session, project, fmt)
        assert paths and all(p.exists() and p.stat().st_size > 0 for p in paths), fmt
