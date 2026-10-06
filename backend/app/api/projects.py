"""项目 / 角色 API。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..models import Asset, Candidate, Character, Job, Page, Panel, Project
from ..pipeline import worker

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    style: str = config.DEFAULT_STYLE   # 默认彩色


class ProjectUpdate(BaseModel):
    name: str | None = None
    style: str | None = None
    bubble_style: str | None = None      # 气泡字体预设：heiti/songti/light/unicode
    font_scale: float | None = None      # 气泡字号缩放 0.5~2.0


class StoryboardRequest(BaseModel):
    story: str
    pages: int = 4
    panels_per_page: int = 4
    use_llm: bool = True


class CharacterCreate(BaseModel):
    name: str
    appearance: str = ""


def _project_detail(session: Session, project: Project) -> dict:
    pages = sorted(
        session.exec(select(Page).where(Page.project_id == project.id)).all(),
        key=lambda p: p.index,
    )
    panels = session.exec(
        select(Panel).where(Panel.project_id == project.id)
    ).all()
    # 资产库：名称映射，供分镜卡片显示中文资产名
    assets = session.exec(
        select(Asset).where(Asset.project_id == project.id)
    ).all()
    asset_names = {a.id: a.name for a in assets}
    asset_kinds = {a.id: a.kind for a in assets}
    panels_by_page: dict[str, list] = {}
    for p in sorted(panels, key=lambda x: x.index):
        cands = session.exec(
            select(Candidate).where(Candidate.panel_id == p.id)
        ).all()
        panels_by_page.setdefault(p.page_id, []).append({
            **p.model_dump(),
            "characters_list": p.char_ids(),
            "dialogue_list": p.dialogues(),
            "prop_ids_list": p.prop_id_list(),
            "scene_name": asset_names.get(p.scene_id, ""),
            "prop_names": [asset_names.get(a, "") for a in p.prop_id_list()],
            "candidates": sorted(
                [{**c.model_dump(), "url": f"/files/{c.path}"} for c in cands],
                key=lambda c: (-(c["score"] or 0), c["created_at"]),
            ),
            "best_candidate_id": (
                max(cands, key=lambda c: c.score).id
                if any(c.score for c in cands) else ""
            ),
        })
    chars = session.exec(
        select(Character).where(Character.project_id == project.id)
    ).all()
    # 资产被分镜引用的次数（前端资产库卡片展示）
    usage: dict[str, int] = {}
    for p in panels:
        for cid in p.char_ids():
            usage[cid] = usage.get(cid, 0) + 1
        if p.scene_id:
            usage[p.scene_id] = usage.get(p.scene_id, 0) + 1
        for aid in p.prop_id_list():
            usage[aid] = usage.get(aid, 0) + 1
    char_list = []
    for c in chars:
        d = c.model_dump()
        refs = [r for r in c.ref_images()]
        if c.reference_image and c.reference_image not in refs:
            refs.insert(0, c.reference_image)
        d["kind"] = "character"
        d["usage"] = usage.get(c.id, 0)
        d["reference_urls"] = [f"/files/{r}" for r in refs]
        char_list.append(d)
    return {
        **project.model_dump(),
        "pages": [
            {
                **pg.model_dump(),
                "panels": panels_by_page.get(pg.id, []),
                "page_png": f"/files/{project.id}/pages/page_{pg.index:03d}.png",
            }
            for pg in pages
        ],
        "characters": char_list,
        "assets": {
            "scene": [
                {**a.model_dump(), "kind": "scene", "kind_label": "场景",
                 "text": a.description or a.appearance or a.name,
                 "usage": usage.get(a.id, 0),
                 "reference_urls": [f"/files/{r}" for r in a.ref_images()]}
                for a in assets if a.kind == "scene"
            ],
            "prop": [
                {**a.model_dump(), "kind": "prop", "kind_label": "道具",
                 "text": a.description or a.appearance or a.name,
                 "usage": usage.get(a.id, 0),
                 "reference_urls": [f"/files/{r}" for r in a.ref_images()]}
                for a in assets if a.kind == "prop"
            ],
        },
    }


@router.post("/projects")
def create_project(body: ProjectCreate, session: Session = Depends(get_session)):
    project = Project(name=body.name, style=body.style)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/projects")
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project).order_by(Project.created_at)).all()  # type: ignore
    result = []
    for p in projects:
        n_panels = len(session.exec(select(Panel).where(Panel.project_id == p.id)).all())
        n_done = len(
            session.exec(
                select(Panel).where(Panel.project_id == p.id, Panel.status == "exported")  # type: ignore
            ).all()
        )
        n_assets = (
            len(session.exec(select(Character).where(Character.project_id == p.id)).all())
            + len(session.exec(select(Asset).where(Asset.project_id == p.id)).all())
        )
        result.append({
            **p.model_dump(), "panel_count": n_panels,
            "exported_count": n_done, "asset_count": n_assets,
        })
    return result


@router.get("/projects/{pid}")
def get_project(pid: str, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    return _project_detail(session, project)


@router.put("/projects/{pid}")
def update_project(pid: str, body: ProjectUpdate, session: Session = Depends(get_session)):
    """更新项目设置（名称/风格/气泡字体/字号）。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if body.name is not None:
        project.name = body.name
    if body.style is not None:
        project.style = body.style
    if body.bubble_style is not None:
        project.bubble_style = body.bubble_style
    if body.font_scale is not None:
        project.font_scale = max(0.5, min(2.0, body.font_scale))
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/fonts")
def font_presets():
    """可用的中文字体预设（前端下拉用）。"""
    return [
        {"key": k, "label": config.FONT_PRESET_LABELS.get(k, k), "default": k == config.DEFAULT_FONT_PRESET}
        for k in config.FONT_PRESETS
    ]


class UpgradeStyleRequest(BaseModel):
    style: str                       # 目标画风
    steps: int | None = None         # 留空用风格推荐步数
    seed: int | None = None
    provider: str = "mlx"


@router.post("/projects/{pid}/upgrade-style")
def upgrade_style(pid: str, body: UpgradeStyleRequest, session: Session = Depends(get_session)):
    """一键升级画风：重生成全部人物/场景/道具设定图 + 重跑全部分镜 + 渲染。

    耗时Warning：真实出图时每张 1~2 分钟，总时长 = (资产数 + 有图分镜数) × 单张耗时。
    """
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if body.style not in config.STYLE_PRESETS:
        raise HTTPException(400, f"未知画风: {body.style}")
    return worker.enqueue("upgrade_style", {
        "project_id": pid,
        "style": body.style,
        "steps": body.steps,
        "seed": body.seed,
        "provider": body.provider,
    }, project_id=pid)


@router.get("/stats")
def global_stats(session: Session = Depends(get_session)):
    """首页统计卡：项目 / 分镜 / 资产 / 已导出文件 / 队列中任务。"""
    projects = session.exec(select(Project)).all()
    n_panels = len(session.exec(select(Panel)).all())
    n_chars = len(session.exec(select(Character)).all())
    n_assets = len(session.exec(select(Asset)).all())
    n_exports = 0
    for p in projects:
        base = config.project_dir(p.id) / "exports"
        if base.exists():
            n_exports += sum(1 for f in base.rglob("*") if f.is_file() and f.stat().st_size > 0)
    from ..models import Job
    pending = len(session.exec(select(Job).where(Job.status == "pending")).all())  # type: ignore
    running = len(session.exec(select(Job).where(Job.status == "running")).all())  # type: ignore
    return {
        "project_count": len(projects),
        "panel_count": n_panels,
        "character_count": n_chars,
        "asset_count": n_assets,
        "export_count": n_exports,
        "jobs_pending": pending,
        "jobs_running": running,
    }


@router.delete("/projects/{pid}")
def delete_project(pid: str, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    for model, col in ((Panel, Panel.project_id), (Page, Page.project_id),
                       (Character, Character.project_id), (Job, Job.project_id)):
        for row in session.exec(select(model).where(col == pid)).all():
            session.delete(row)
    session.delete(project)
    session.commit()
    return {"ok": True}


@router.post("/projects/{pid}/storyboard")
def create_storyboard(pid: str, body: StoryboardRequest, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if not body.story.strip():
        raise HTTPException(400, "故事内容为空")
    job = worker.enqueue("storyboard", {
        "project_id": pid,
        "story": body.story,
        "pages": body.pages,
        "panels_per_page": body.panels_per_page,
        "use_llm": body.use_llm,
    }, project_id=pid)
    return job


@router.get("/projects/{pid}/characters")
def list_characters(pid: str, session: Session = Depends(get_session)):
    chars = session.exec(select(Character).where(Character.project_id == pid)).all()
    return [c.model_dump() for c in chars]


@router.post("/projects/{pid}/characters")
def create_character(pid: str, body: CharacterCreate, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    ch = Character(project_id=pid, name=body.name, appearance=body.appearance)
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.put("/characters/{cid}")
def update_character(cid: str, body: CharacterCreate, session: Session = Depends(get_session)):
    ch = session.get(Character, cid)
    if not ch:
        raise HTTPException(404, "角色不存在")
    ch.name = body.name
    ch.appearance = body.appearance
    session.add(ch)
    session.commit()
    return ch
