"""分镜格 / 候选 / 页面渲染 / 导出 / 任务 API。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..models import Candidate, Character, Job, Page, Panel, Project
from ..pipeline import worker
from ..pipeline.layout import render_page

router = APIRouter(prefix="/api", tags=["panels"])


class PanelUpdate(BaseModel):
    action: str | None = None
    camera: str | None = None
    location: str | None = None
    emotion: str | None = None
    lighting: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    dialogue: list[dict] | None = None
    characters: list[str] | None = None
    scene_id: str | None = None            # 资产库：场景
    prop_ids: list[str] | None = None      # 资产库：道具（多选）
    seed: int | None = None


class DraftRequest(BaseModel):
    count: int = 3
    provider: str = ""          # 留空=自动（有角色参考图则走 mlx-edit）
    model: str = ""             # ComfyUI 的 checkpoint 名；MLX 留空用本机默认模型
    prompt: str = ""
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    use_references: bool = True  # 角色一致性开关


class SheetRequest(BaseModel):
    count: int = 1
    view: str = "multi"          # multi|front|bust
    seed: int | None = None
    steps: int | None = None
    provider: str = "mlx"


class InpaintRequest(BaseModel):
    """选区的相对坐标（0~1）：{x, y, w, h}。"""
    rect: dict
    prompt: str = ""
    candidate_id: str = ""       # 留空=当前选中候选
    provider: str = ""
    steps: int | None = None
    seed: int | None = None
    margin_ratio: float = 0.25   # 上下文边距（相对选区尺寸）
    feather: int = 14            # 融合羽化像素


class ExternalEditRequest(BaseModel):
    candidate_id: str = ""
    open_editor: bool = True     # 自动打开 Krita（未安装则 Finder 定位）


class ExportRequest(BaseModel):
    fmt: str = "pdf"  # png|pdf|cbz
    lang: str = ""    # "" = 中文原文；en / ja / zh-Hant


@router.put("/panels/{panel_id}")
def update_panel(panel_id: str, body: PanelUpdate, session: Session = Depends(get_session)):
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    if body.action is not None:
        panel.action = body.action
    if body.camera is not None:
        panel.camera = body.camera
    if body.location is not None:
        panel.location = body.location
    if body.emotion is not None:
        panel.emotion = body.emotion
    if body.lighting is not None:
        panel.lighting = body.lighting
    if body.prompt is not None:
        panel.prompt = body.prompt
    if body.negative_prompt is not None:
        panel.negative_prompt = body.negative_prompt
    if body.dialogue is not None:
        panel.dialogue = json.dumps(body.dialogue, ensure_ascii=False)
    if body.characters is not None:
        panel.characters = json.dumps(body.characters)
    if body.scene_id is not None:
        panel.scene_id = body.scene_id
    if body.prop_ids is not None:
        panel.prop_ids = json.dumps(body.prop_ids)
    if body.seed is not None:
        panel.seed = body.seed
    session.add(panel)
    session.commit()
    return panel


@router.post("/panels/{panel_id}/draft")
def generate_draft(panel_id: str, body: DraftRequest, session: Session = Depends(get_session)):
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    job = worker.enqueue("panel_draft", {
        "panel_id": panel_id,
        "count": max(1, min(body.count, 6)),
        "provider": body.provider,
        "model": body.model,
        "prompt": body.prompt,
        "width": body.width,
        "height": body.height,
        "steps": body.steps,
        "use_references": body.use_references,
    }, project_id=panel.project_id)
    return job


@router.post("/panels/{panel_id}/final")
def generate_final(panel_id: str, body: DraftRequest, session: Session = Depends(get_session)):
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    job = worker.enqueue("panel_final", {
        "panel_id": panel_id,
        "count": max(1, min(body.count, 3)),
        "provider": body.provider,
        "model": body.model,
        "prompt": body.prompt,
        "use_references": body.use_references,
    }, project_id=panel.project_id)
    return job


@router.post("/characters/{cid}/sheet")
def generate_character_sheet(cid: str, body: SheetRequest, session: Session = Depends(get_session)):
    """生成角色设定图（Character Bible），并自动登记为分镜生成的参考图。"""
    ch = session.get(Character, cid)
    if not ch:
        raise HTTPException(404, "角色不存在")
    job = worker.enqueue("character_sheet", {
        "character_id": cid,
        "count": max(1, min(body.count, 3)),
        "view": body.view,
        "seed": body.seed,
        "provider": body.provider,
    }, project_id=ch.project_id)
    return job


@router.post("/panels/{panel_id}/inpaint")
def inpaint(panel_id: str, body: InpaintRequest, session: Session = Depends(get_session)):
    """局部重绘选区（生成新候选，原图不受影响）。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    rect = body.rect or {}
    for k in ("x", "y", "w", "h"):
        if k not in rect:
            raise HTTPException(400, f"选区缺少字段: {k}")
    job = worker.enqueue("panel_inpaint", {
        "panel_id": panel_id,
        "rect": rect,
        "prompt": body.prompt,
        "candidate_id": body.candidate_id,
        "provider": body.provider,
        "steps": body.steps,
        "seed": body.seed,
        "margin_ratio": body.margin_ratio,
        "feather": body.feather,
    }, project_id=panel.project_id)
    return job


@router.post("/panels/{panel_id}/external-export")
def external_export(panel_id: str, body: ExternalEditRequest, session: Session = Depends(get_session)):
    """导出工作图到外部编辑器（Krita 等），供人工精修。"""
    from ..pipeline.external import export_for_edit
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    try:
        return export_for_edit(session, panel, body.candidate_id, body.open_editor)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/panels/{panel_id}/external-import")
def external_import(panel_id: str, session: Session = Depends(get_session)):
    """把外部编辑器改好的工作图回填为新的手动候选。"""
    from ..pipeline.external import import_from_edit
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    try:
        return import_from_edit(session, panel)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


class BatchDraftRequest(BaseModel):
    count: int = 1
    page_id: str = ""            # 留空=整个项目
    use_references: bool = True
    only_missing: bool = True    # 只处理还没有候选图的格子（避免重复烧算力）
    provider: str = ""
    model: str = ""


class RegenerateRequest(BaseModel):
    count: int = 1
    use_references: bool = True
    provider: str = ""
    model: str = ""


def _enqueue_drafts(session: Session, panels: list[Panel], body, project_id: str) -> dict:
    """批量入队分镜草稿任务（worker 串行执行，进度在任务队列可见）。"""
    job_ids = []
    for p in panels:
        job = worker.enqueue("panel_draft", {
            "panel_id": p.id,
            "count": max(1, min(body.count, 4)),
            "provider": body.provider,
            "model": getattr(body, "model", ""),
            "use_references": body.use_references,
        }, project_id=project_id)
        job_ids.append(job.id)
    return {"enqueued": len(job_ids), "job_ids": job_ids}


@router.post("/projects/{pid}/batch-draft")
def batch_draft(pid: str, body: BatchDraftRequest, session: Session = Depends(get_session)):
    """批量生成候选图：整页或整个项目，可只处理尚未出图的格子。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    q = select(Panel).where(Panel.project_id == pid)
    if body.page_id:
        q = select(Panel).where(Panel.project_id == pid, Panel.page_id == body.page_id)  # type: ignore
    panels = sorted(session.exec(q).all(), key=lambda p: p.index)
    if body.only_missing:
        keep = []
        for p in panels:
            n = len(session.exec(select(Candidate).where(Candidate.panel_id == p.id)).all())
            if n == 0:
                keep.append(p)
        panels = keep
    if not panels:
        return {"enqueued": 0, "job_ids": [], "message": "没有需要生成的格子"}
    return _enqueue_drafts(session, panels, body, pid)


@router.get("/panels/{panel_id}/neighbors")
def panel_neighbors(panel_id: str, session: Session = Depends(get_session)):
    """同页前后相邻格（用于连贯性检查：共享场景/光照是否一致）。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    mates = sorted(
        session.exec(select(Panel).where(Panel.page_id == panel.page_id)).all(),
        key=lambda p: p.index,
    )
    idx = [i for i, p in enumerate(mates) if p.id == panel_id][0]
    return {
        "prev": mates[idx - 1].model_dump() if idx > 0 else None,
        "next": mates[idx + 1].model_dump() if idx + 1 < len(mates) else None,
    }


@router.get("/projects/{pid}/exports")
def list_exports(pid: str, session: Session = Depends(get_session)):
    """已导出的文件清单（供前端下载）。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    base = config.project_dir(pid) / "exports"
    files = []
    if base.exists():
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.stat().st_size > 0:
                rel = f.relative_to(config.PROJECTS_DIR)
                files.append({
                    "fmt": f.parent.name,
                    "name": f.name,
                    "url": f"/files/{rel}",
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


class ScoreRequest(BaseModel):
    use_vlm: bool = False        # 是否让本地 VLM 看图点评（慢，每张数十秒）
    only_unscored: bool = True   # 只评还没打分的候选


@router.post("/panels/{panel_id}/score")
def score_panel(panel_id: str, body: ScoreRequest, session: Session = Depends(get_session)):
    """给该分镜格的所有候选打分（客观指标 + 可选 VLM 点评）。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    job = worker.enqueue("score_candidates", {
        "panel_id": panel_id,
        "use_vlm": body.use_vlm,
        "only_unscored": body.only_unscored,
    }, project_id=panel.project_id)
    return job


@router.post("/projects/{pid}/score-candidates")
def score_project(pid: str, body: ScoreRequest, session: Session = Depends(get_session)):
    """批量给整个项目的候选打分（用于批量出图后统一挑图）。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    return worker.enqueue("score_candidates", {
        "project_id": pid,
        "use_vlm": body.use_vlm,
        "only_unscored": body.only_unscored,
    }, project_id=pid)


@router.get("/panels/{panel_id}/ranking")
def panel_ranking(panel_id: str, session: Session = Depends(get_session)):
    """按分数排序的候选列表（未评分的排在后面）。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    cands = session.exec(select(Candidate).where(Candidate.panel_id == panel_id)).all()
    cands = sorted(cands, key=lambda c: (-(c.score or 0), c.created_at))
    return [
        {**c.model_dump(), "url": f"/files/{c.path}", "is_selected": c.id == panel.selected_candidate_id}
        for c in cands
    ]


@router.post("/panels/{panel_id}/auto-select-best")
def auto_select_best(panel_id: str, session: Session = Depends(get_session)):
    """自动选中得分最高的候选（没有评分时返回明确提示）。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    cands = session.exec(select(Candidate).where(Candidate.panel_id == panel_id)).all()
    scored = [c for c in cands if c.score]
    if not scored:
        raise HTTPException(400, "该分镜格还没有已评分的候选，请先「评分排序」")
    best = max(scored, key=lambda c: c.score)
    panel.selected_candidate_id = best.id
    panel.status = "approved"
    session.add(panel)
    session.commit()
    return {"selected": best.id, "score": best.score, "path": best.path,
            "url": f"/files/{best.path}"}


@router.post("/projects/{pid}/translate")
def translate_project_api(pid: str, body: TranslateRequest, session: Session = Depends(get_session)):
    """整项目对白翻译（本机 LLM），译文存 dialogue_translations。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if not body.langs:
        raise HTTPException(400, "请至少选择一个目标语言")
    results = []
    for lang in body.langs:
        job = worker.enqueue("translate_dialogues", {"project_id": pid, "lang": lang}, project_id=pid)
        results.append(job)
    return {"jobs": results}


@router.post("/projects/{pid}/auto-select-best")
def auto_select_best_project(pid: str, body: ScoreRequest | None = None,
                             session: Session = Depends(get_session)):
    """整项目自动选最佳（只影响已有评分的格子；未评分的原样保留）。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    changed, skipped = [], []
    for p in session.exec(select(Panel).where(Panel.project_id == pid)).all():
        cands = [c for c in session.exec(
            select(Candidate).where(Candidate.panel_id == p.id)
        ).all() if c.score]
        if not cands:
            skipped.append(p.id)
            continue
        best = max(cands, key=lambda c: c.score)
        if p.selected_candidate_id != best.id:
            p.selected_candidate_id = best.id
            p.status = "approved"
            session.add(p)
            changed.append({"panel_id": p.id, "candidate_id": best.id, "score": best.score})
    session.commit()
    return {"changed": len(changed), "skipped_unscored": len(skipped), "details": changed}


def _reindex_page(session: Session, page_id: str) -> None:
    panels = sorted(
        session.exec(select(Panel).where(Panel.page_id == page_id)).all(),
        key=lambda p: p.index,
    )
    for i, p in enumerate(panels, start=1):
        if p.index != i:
            p.index = i
            session.add(p)
    session.commit()


@router.delete("/panels/{panel_id}")
def delete_panel(panel_id: str, session: Session = Depends(get_session)):
    """删除分镜格（连同其候选记录；图片文件保留），同页其余格自动重排序。"""
    panel = session.get(Panel, panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    page_id = panel.page_id
    for c in session.exec(select(Candidate).where(Candidate.panel_id == panel_id)).all():
        session.delete(c)
    session.delete(panel)
    session.commit()
    _reindex_page(session, page_id)
    return {"ok": True}


@router.post("/panels/{panel_id}/duplicate")
def duplicate_panel(panel_id: str, session: Session = Depends(get_session)):
    """复制分镜格（含设定与选中的候选图），插入到当前格之后。"""
    src = session.get(Panel, panel_id)
    if not src:
        raise HTTPException(404, "分镜格不存在")
    import shutil as _shutil
    import time as _time

    # 先把 src 之后的格子整体后移（腾出位置），再插入副本 —— 避免同序号冲突
    mates = sorted(
        session.exec(select(Panel).where(Panel.page_id == src.page_id)).all(),
        key=lambda p: p.index,
    )
    for p in reversed([p for p in mates if p.index > src.index]):
        p.index += 1
        session.add(p)
    session.commit()

    dup = Panel(
        project_id=src.project_id, page_id=src.page_id, index=src.index + 1,
        characters=src.characters, scene_id=src.scene_id, prop_ids=src.prop_ids,
        location=src.location, camera=src.camera, action=src.action,
        emotion=src.emotion, lighting=src.lighting, dialogue=src.dialogue,
        prompt=src.prompt, negative_prompt=src.negative_prompt,
        seed=src.seed, status="draft",
    )
    session.add(dup)
    session.commit()

    # 复制选中的候选图文件
    if src.selected_candidate_id:
        cand = session.get(Candidate, src.selected_candidate_id)
        if cand and cand.path:
            sp = config.PROJECTS_DIR / cand.path
            if sp.exists():
                ddir = config.project_dir(src.project_id) / "panels" / dup.id / "draft"
                ddir.mkdir(parents=True, exist_ok=True)
                dp = ddir / f"draft_dup_{int(_time.time())}.png"
                _shutil.copyfile(sp, dp)
                nc = Candidate(
                    panel_id=dup.id, path=str(dp.relative_to(config.PROJECTS_DIR)),
                    prompt=cand.prompt, negative_prompt=cand.negative_prompt,
                    seed=cand.seed, provider=cand.provider, model=cand.model,
                    params=cand.params, stage=cand.stage,
                )
                session.add(nc)
                dup.selected_candidate_id = nc.id
                session.add(dup)
    session.commit()
    _reindex_page(session, src.page_id)
    return {"panel": dup.model_dump(), "ok": True}


@router.post("/pages/{page_id}/panels")
def create_panel(page_id: str, body: PanelUpdate | None = None,
                 session: Session = Depends(get_session)):
    """在页面末尾新增一个空分镜格。"""
    page = session.get(Page, page_id)
    if not page:
        raise HTTPException(404, "页面不存在")
    n = len(session.exec(select(Panel).where(Panel.page_id == page_id)).all())
    panel = Panel(
        project_id=page.project_id, page_id=page_id, index=n + 1,
        camera="medium_shot", emotion="calm", lighting="day",
    )
    if body and body.action:
        panel.action = body.action
    if body and body.dialogue is not None:
        panel.dialogue = json.dumps(body.dialogue, ensure_ascii=False)
    session.add(panel)
    session.commit()
    session.refresh(panel)
    return panel


@router.post("/candidates/{cand_id}/select")
def select_candidate(cand_id: str, session: Session = Depends(get_session)):
    cand = session.get(Candidate, cand_id)
    if not cand:
        raise HTTPException(404, "候选不存在")
    panel = session.get(Panel, cand.panel_id)
    if not panel:
        raise HTTPException(404, "分镜格不存在")
    panel.selected_candidate_id = cand_id
    panel.status = "approved"
    session.add(panel)
    session.commit()
    return panel


class RenderRequest(BaseModel):
    font_preset: str = ""     # 留空=用项目设置
    size_scale: float | None = None
    lang: str = ""            # 对白语言（"" = 中文原文）


class TranslateRequest(BaseModel):
    langs: list[str]          # 例如 ["en"] / ["zh-Hant","ja"]


@router.post("/pages/{page_id}/render")
def render_page_api(page_id: str, body: RenderRequest | None = None, session: Session = Depends(get_session)):
    page = session.get(Page, page_id)
    if not page:
        raise HTTPException(404, "页面不存在")
    body = body or RenderRequest()
    path = render_page(session, page, font_preset=body.font_preset,
                       size_scale=body.size_scale or 1.0, lang=body.lang)
    return {"page_png": str(path), "url": f"/files/{page.project_id}/pages/page_{page.index:03d}.png"}


@router.post("/projects/{pid}/export")
def export_project_api(pid: str, body: ExportRequest, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    job = worker.enqueue("export", {"project_id": pid, "fmt": body.fmt, "lang": body.lang or ""},
                         project_id=pid)
    return job


@router.get("/jobs")
def list_jobs(project_id: str = "", limit: int = 50, session: Session = Depends(get_session)):
    q = select(Job).order_by(Job.created_at.desc()).limit(limit)  # type: ignore
    if project_id:
        q = select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(limit)  # type: ignore
    jobs = session.exec(q).all()
    out = []
    for j in jobs:
        out.append({**j.model_dump(), "payload_dict": json.loads(j.payload or "{}"),
                    "result_dict": json.loads(j.result or "{}"),
                    "progress_dict": json.loads(j.progress or "{}")})
    return out


@router.get("/providers")
def providers_status():
    from ..providers import all_providers
    return [p.info() for p in all_providers()]


@router.get("/styles")
def styles():
    """常用风格库（取材腾讯动漫热门题材族），按分组返回，供前端下拉使用。"""
    return {
        "groups": config.STYLE_GROUPS,
        "default": config.DEFAULT_STYLE,
        "styles": config.styles_payload(),
    }
