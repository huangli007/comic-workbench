"""单机任务队列 Worker — SQLite 轮询 + 串行执行（M4 16GB 单任务原则）。"""
from __future__ import annotations

import json
import random
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..db import engine
from ..models import Asset, Candidate, Character, Job, Page, Panel, Project
from ..providers import GenRequest, get_provider
from . import export as export_mod
from . import layout as layout_mod
from .asset import asset_dir, build_asset_sheet_prompt, guard_text
from .asset import reference_images as asset_reference_images
from .character import (
    build_edit_prompt,
    build_sheet_prompt,
    character_dir,
    reference_images,
)
from .inpaint import inpaint_region
from .quality import combine, score_objective, vlm_available, vlm_review
from .prompt import apply_style_directive, build_panel_prompt
from .lora import lora_for_characters, train_character_lora
from .storyboard import make_storyboard
from .translate import translate_project

_stop = threading.Event()
_thread: threading.Thread | None = None


def _run_token() -> str:
    """本次生成的短标识：同一格子重复生成时不会覆盖旧候选（seed 相同也不会）。"""
    return f"{int(time.time() * 1000) % 1000000:06d}"


def _set_progress(session: Session, job: Job, current: int, total: int, label: str = "") -> None:
    job.progress = json.dumps({"current": current, "total": total, "label": label}, ensure_ascii=False)
    session.add(job)
    session.commit()


# ── Job 处理器 ───────────────────────────────────────

def _handle_storyboard(session: Session, job: Job, payload: dict) -> dict:
    project = session.get(Project, payload["project_id"])
    if not project:
        raise RuntimeError("项目不存在")

    # 清空旧分镜（重新生成）——同时清理候选行，避免悬挂数据
    old_panels = session.exec(select(Panel).where(Panel.project_id == project.id)).all()
    for p in old_panels:
        for cand in session.exec(select(Candidate).where(Candidate.panel_id == p.id)).all():
            session.delete(cand)  # 图片文件保留在磁盘，不删（避免误删人工成果）
        session.delete(p)
    for p in session.exec(select(Page).where(Page.project_id == project.id)).all():
        session.delete(p)
    session.commit()

    sb, used_llm = make_storyboard(
        payload["story"], use_llm=payload.get("use_llm", True),
        max_panels=payload.get("pages", 4) * payload.get("panels_per_page", 4),
    )

    # 角色按名字**合并**：已有角色（含设定图/参考图）必须保留，只新增没见过的。
    # 这是角色一致性的前提：重跑分镜不能把已生成的角色资产冲掉。
    existing = session.exec(
        select(Character).where(Character.project_id == project.id)
    ).all()
    by_name: dict[str, Character] = {c.name: c for c in existing}
    incoming = [c.get("name", "").strip() for c in sb.get("characters", []) if c.get("name")]
    for c in sb.get("characters", []):
        name = c.get("name", "").strip()
        if not name:
            continue
        ch = by_name.get(name)
        if ch is None:
            ch = Character(project_id=project.id, name=name, appearance=c.get("appearance", ""))
            session.add(ch)
            by_name[name] = ch
        elif not ch.appearance and c.get("appearance"):
            ch.appearance = c["appearance"]
            session.add(ch)
    session.commit()

    # 清理：新分镜里没出现、且没有任何投入（无参考图/无外貌设定）的旧角色
    for name, ch in list(by_name.items()):
        if name in incoming:
            continue
        if ch.reference_image or (ch.appearance or "").strip():
            continue
        session.delete(ch)
        by_name.pop(name, None)
    session.commit()

    # 分页落库
    panels = sb.get("panels", [])
    per_page = payload.get("panels_per_page", 4)
    pages_needed = max(1, (len(panels) + per_page - 1) // per_page)
    pages: list[Page] = []
    for i in range(pages_needed):
        page = Page(project_id=project.id, index=i + 1, template="auto")
        session.add(page)
        pages.append(page)
    session.commit()

    char_by_name = dict(by_name)

    # 资产库：场景按地点名自动建卡（已有同名场景则复用，保证一致性资产不被冲掉）
    scenes = session.exec(
        select(Asset).where(Asset.project_id == project.id, Asset.kind == "scene")  # type: ignore
    ).all()
    scene_by_name: dict[str, Asset] = {a.name: a for a in scenes}
    used_scene_names: set[str] = set()

    for i, p in enumerate(panels):
        page = pages[i // per_page]
        char_ids = [
            char_by_name[n].id
            for n in p.get("characters", [])
            if n in char_by_name
        ]
        loc_name = (p.get("location") or "").strip()
        scene_id = ""
        if loc_name:
            used_scene_names.add(loc_name)
            scene = scene_by_name.get(loc_name)
            if scene is None:
                scene = Asset(project_id=project.id, kind="scene", name=loc_name)
                session.add(scene)
                session.commit()
                scene_by_name[loc_name] = scene
            scene_id = scene.id
        panel = Panel(
            project_id=project.id,
            page_id=page.id,
            index=i + 1,
            characters=json.dumps(char_ids),
            scene_id=scene_id,
            location=loc_name,
            camera=p.get("camera", "medium_shot"),
            action=p.get("action", ""),
            emotion=p.get("emotion", "calm"),
            lighting=p.get("lighting", "day"),
            dialogue=json.dumps(p.get("dialogue", []), ensure_ascii=False),
            seed=random.randint(1, 2**31 - 1),
        )
        session.add(panel)
    session.commit()

    # 清理：新分镜没用到、且没有任何投入（无参考图/无设定）的空场景卡
    for name, scene in list(scene_by_name.items()):
        if name in used_scene_names:
            continue
        if scene.reference_image or (scene.description or "").strip():
            continue
        session.delete(scene)
        scene_by_name.pop(name, None)
    session.commit()

    return {
        "pages": len(pages),
        "panels": len(panels),
        "characters": len(by_name),
        "scenes": len(scene_by_name),
        "used_llm": used_llm,
    }


def _handle_character_sheet(session: Session, job: Job, payload: dict) -> dict:
    """角色设定图：出一张（或几张）可当参考图的角色设定图，并登记为参考图。"""
    character = session.get(Character, payload["character_id"])
    if not character:
        raise RuntimeError("角色不存在")
    project = session.get(Project, character.project_id)
    if not project:
        raise RuntimeError("项目不存在")

    provider = get_provider(payload.get("provider", "mlx"))
    count = max(1, min(payload.get("count", 1), 3))
    view = payload.get("view", "multi")
    prompt = apply_style_directive(
        project.style, build_sheet_prompt(project, character, view)
    )
    prompt += ", absolutely no text anywhere" if provider.name == "mlx" else ""

    out_dir = character_dir(project.id, character.id)
    created: list[str] = []
    for i in range(count):
        seed = (payload.get("seed") or 12345) + i * 7919
        out = out_dir / f"sheet_{project.style}_{seed}_{_run_token()}.png"
        req = GenRequest(
            prompt=prompt,
            output_path=out,
            width=payload.get("width") or config.SHEET_WIDTH,
            height=payload.get("height") or config.SHEET_HEIGHT,
            steps=payload.get("steps") or config.SHEET_STEPS,
            seed=seed,
        )
        provider.generate(req)
        created.append(str(out))
        _set_progress(session, job, i + 1, count, f"角色设定图 {i + 1}/{count}")

    # 登记参考图（第一张作为主参考）
    rel = str(Path(created[0]).relative_to(config.PROJECTS_DIR))
    character.reference_image = rel
    extra = {}
    try:
        extra = json.loads(character.extra or "{}")
    except Exception:
        extra = {}
    extra["reference_images"] = [str(Path(p).relative_to(config.PROJECTS_DIR)) for p in created]
    character.extra = json.dumps(extra, ensure_ascii=False)
    session.add(character)
    session.commit()
    return {
        "character_id": character.id,
        "reference_image": rel,
        "created": [f"/files/{p}" for p in extra["reference_images"]],
    }


def _handle_asset_sheet(session: Session, job: Job, payload: dict) -> dict:
    """资产（场景/道具）设定图：产出即登记为该资产的参考图，供分镜一致性使用。"""
    asset = session.get(Asset, payload["asset_id"])
    if not asset:
        raise RuntimeError("资产不存在")
    project = session.get(Project, asset.project_id)
    if not project:
        raise RuntimeError("项目不存在")

    provider = get_provider(payload.get("provider", "mlx"))
    count = max(1, min(payload.get("count", 1), 3))
    view = payload.get("view", "")
    prompt = apply_style_directive(
        project.style, build_asset_sheet_prompt(project, asset, view)
    )

    out_dir = asset_dir(project.id, asset.kind, asset.id)
    created: list[str] = []
    for i in range(count):
        seed = (payload.get("seed") or (7 if asset.kind == "scene" else 21)) + i * 6421
        out = out_dir / f"sheet_{project.style}_{seed}_{_run_token()}.png"
        req = GenRequest(
            prompt=prompt,
            output_path=out,
            width=payload.get("width") or config.SHEET_WIDTH,
            height=payload.get("height") or config.SHEET_HEIGHT,
            steps=payload.get("steps") or config.SHEET_STEPS,
            seed=seed,
        )
        provider.generate(req)
        created.append(str(out))
        _set_progress(session, job, i + 1, count, f"资产设定图 {i + 1}/{count}")

    rel = str(Path(created[0]).relative_to(config.PROJECTS_DIR))
    asset.reference_image = rel
    extra = {}
    try:
        extra = json.loads(asset.extra or "{}")
    except Exception:
        extra = {}
    extra["reference_images"] = [str(Path(p).relative_to(config.PROJECTS_DIR)) for p in created]
    asset.extra = json.dumps(extra, ensure_ascii=False)
    session.add(asset)
    session.commit()
    return {
        "asset_id": asset.id,
        "kind": asset.kind,
        "reference_image": rel,
        "created": [f"/files/{p}" for p in extra["reference_images"]],
    }


def _handle_panel_generation(session: Session, job: Job, payload: dict, stage: str) -> dict:
    panel = session.get(Panel, payload["panel_id"])
    if not panel:
        raise RuntimeError("分镜格不存在")
    project = session.get(Project, panel.project_id)

    chars = []
    for cid in panel.char_ids():
        ch = session.get(Character, cid)
        if ch:
            chars.append(ch)
    # 资产库：场景（单选）+ 道具（多选）
    scene = session.get(Asset, panel.scene_id) if panel.scene_id else None
    props = [a for a in (session.get(Asset, pid) for pid in panel.prop_id_list()) if a]
    prompt, negative = build_panel_prompt(
        project, panel, chars, payload.get("prompt", ""), scene=scene, props=props
    )
    panel.prompt, panel.negative_prompt = prompt, negative
    panel.status = "generating"
    session.add(panel)
    session.commit()

    # 角色一致性：挂参考图 → 走参考条件生成（蓝图 §6 阶段一，先不训 LoRA）
    use_refs = payload.get("use_references", True)
    refs: list[Path] = []
    if use_refs:
        for ch in chars:
            refs.extend(reference_images(project.id, ch, project.style))
        # 场景/道具参考图排后（角色优先，受 MAX_REFERENCE_IMAGES 限制）
        if scene is not None:
            refs.extend(asset_reference_images(project.id, scene, project.style))
        for pr in props:
            refs.extend(asset_reference_images(project.id, pr, project.style))
        refs = refs[: config.MAX_REFERENCE_IMAGES]

    provider_name = payload.get("provider") or ("mlx-edit" if refs else "mlx")
    provider = get_provider(provider_name)
    if refs:
        guards = ""
        if scene is not None or props:
            guards = guard_text("scene", [scene] if scene else [], 1) + \
                guard_text("prop", props, len(props))
        prompt = guards + build_edit_prompt(prompt, chars, len(refs))
    # 风格指令前后夹击（必须在最外层，别被守护语句淹没）
    prompt = apply_style_directive(project.style, prompt)
    panel.prompt = prompt
    session.add(panel)
    session.commit()
    count = payload.get("count", 3)
    # 风格推荐参数：步数按风格；guidance/cfg 按引擎（MLX klein 只能 1.0，由 provider 兜底）
    rec = config.style_rec(project.style)
    if stage == "final":
        width, height, steps = config.FINAL_WIDTH, config.FINAL_HEIGHT, config.FINAL_STEPS
        steps = max(steps, rec["steps"])
    else:
        width, height, steps = config.DRAFT_WIDTH, config.DRAFT_HEIGHT, rec["steps"]
    guidance = 0.0
    model = payload.get("model", "") or ""
    # 人物 LoRA（权重级一致性；比参考图更硬）
    lora_paths, lora_scales = lora_for_characters(chars)
    # ComfyUI（SD1.5 系）与 MLX（FLUX）规格不同：分辨率、步数、cfg 都要分开
    if provider.name == "comfyui":
        width, height, steps = config.COMFYUI_WIDTH, config.COMFYUI_HEIGHT, config.COMFYUI_STEPS
        guidance = rec.get("cfg") or config.COMFYUI_GUIDANCE
        if stage == "final":
            width, height = max(width, 512), max(height, 768)
        if not model:  # 未指定 checkpoint → 用 ComfyUI 里第一个可用模型
            from ..providers.comfyui import list_checkpoints
            ckpts = list_checkpoints()
            model = ckpts[0] if ckpts else ""

    pdir = config.project_dir(project.id) / "panels" / panel.id / stage
    created: list[str] = []
    for i in range(count):
        seed = panel.seed + i * 1009
        out = pdir / f"{stage}_{seed}_{_run_token()}.png"
        req = GenRequest(
            prompt=prompt,
            negative_prompt=negative,
            output_path=out,
            lora_paths=lora_paths,
            lora_scales=lora_scales,
            width=payload.get("width") or width,
            height=payload.get("height") or height,
            steps=payload.get("steps") or steps,
            seed=seed,
            guidance=guidance,
            reference_images=refs,
            model=model,
        )
        provider.generate(req)
        cand = Candidate(
            panel_id=panel.id,
            path=str(out.relative_to(config.PROJECTS_DIR)),
            prompt=prompt,
            negative_prompt=negative,
            seed=seed,
            provider=provider.name,
            model=req.model or (config.MLX_MODEL if provider.name.startswith("mlx") else ""),
            params=json.dumps({
                "width": req.width, "height": req.height, "steps": req.steps,
                "guidance": req.guidance,
                "references": [str(r.relative_to(config.PROJECTS_DIR)) for r in refs],
            }, ensure_ascii=False),
            stage=stage,
        )
        session.add(cand)
        session.commit()
        created.append(cand.id)
        _set_progress(session, job, i + 1, count, f"panel {panel.index} 候选 {i + 1}/{count}")

    panel.status = "review" if stage == "draft" else panel.status
    # 升级画风等场景：新生成的候选自动成为选中项（旧选择是旧画风的图，已过时）
    if payload.get("auto_select") and created:
        panel.selected_candidate_id = created[0]
        if stage == "draft":
            panel.status = "approved"
    session.add(panel)
    session.commit()
    return {"candidates": created, "stage": stage}


def _handle_panel_inpaint(session: Session, job: Job, payload: dict) -> dict:
    """局部重绘：裁剪—条件重绘—羽化回贴，产出新的 inpaint 候选。"""
    panel = session.get(Panel, payload["panel_id"])
    if not panel:
        raise RuntimeError("分镜格不存在")
    _set_progress(session, job, 0, 1, "局部重绘中")
    result = inpaint_region(
        session,
        panel,
        rect_norm=payload.get("rect", {}),
        prompt=payload.get("prompt", ""),
        provider_name=payload.get("provider", ""),
        steps=payload.get("steps"),
        seed=payload.get("seed"),
        margin_ratio=payload.get("margin_ratio", 0.25),
        feather=payload.get("feather", 14),
        candidate_id=payload.get("candidate_id", ""),
    )
    _set_progress(session, job, 1, 1, "完成")
    return result


def _handle_train_lora(session: Session, job: Job, payload: dict) -> dict:
    """训练角色 LoRA（需要完整精度权重；数据集自动从设定图与分镜图准备）。"""
    tick = lambda cur: _set_progress(session, job, cur, 1, "训练角色 LoRA（耗时较长）")
    tick(0)
    r = train_character_lora(
        session, payload["project_id"], payload["character_id"],
        epochs=int(payload.get("epochs") or 30),
        rank=int(payload.get("rank") or 16),
        lr=float(payload.get("lr") or 1e-4),
        quantize=payload.get("quantize"),
        max_resolution=int(payload.get("max_resolution") or 1024),
        seed=int(payload.get("seed") or 42),
    )
    tick(1)
    return r


def _handle_translate(session: Session, job: Job, payload: dict) -> dict:
    """整项目对白翻译到目标语言（本机 LLM）。"""
    tick = lambda cur: _set_progress(session, job, cur, 1, f"翻译 {payload.get('lang')}")
    tick(0)
    result = translate_project(session, payload["project_id"], payload["lang"])
    tick(1)
    return result


def _handle_page_render(session: Session, job: Job, payload: dict) -> dict:
    page = session.get(Page, payload["page_id"])
    if not page:
        raise RuntimeError("页面不存在")
    path = layout_mod.render_page(session, page, lang=payload.get("lang") or "")
    return {"page_png": str(path)}


def _handle_export(session: Session, job: Job, payload: dict) -> dict:
    project = session.get(Project, payload["project_id"])
    if not project:
        raise RuntimeError("项目不存在")
    fmt = payload.get("fmt", "pdf")
    lang = payload.get("lang") or ""
    paths = export_mod.export_project(session, project, fmt, lang=lang)
    # 标记已导出
    for panel in session.exec(
        select(Panel).where(Panel.project_id == project.id)
    ).all():
        if panel.status == "approved":
            panel.status = "exported"
            session.add(panel)
    session.commit()
    return {"files": [str(p) for p in paths], "fmt": fmt}


def _handle_score_candidates(session: Session, job: Job, payload: dict) -> dict:
    """候选图评分：客观指标（毫秒级）+ 可选本地 VLM 点评。

    评分只更新 Candidate.score / score_detail，不动图片、不动选择状态，
    所以可以随时重跑（比如换了更严的 VLM 提示词）。
    """
    panel = session.get(Panel, payload["panel_id"]) if payload.get("panel_id") else None
    project_id = payload.get("project_id") or (panel.project_id if panel else "")
    project = session.get(Project, project_id)
    if not project:
        raise RuntimeError("项目不存在")
    expect_color = project.style in config.COLOR_STYLES

    q = select(Candidate)
    if panel is not None:
        q = select(Candidate).where(Candidate.panel_id == panel.id)  # type: ignore
    else:
        panel_ids = [p.id for p in session.exec(
            select(Panel).where(Panel.project_id == project_id)
        ).all()]
        cands = [c for c in session.exec(select(Candidate)).all() if c.panel_id in panel_ids]
        q = None
    if q is not None:
        cands = session.exec(q).all()

    if payload.get("candidate_ids"):
        wanted = set(payload["candidate_ids"])
        cands = [c for c in cands if c.id in wanted]
    if payload.get("only_unscored", True):
        cands = [c for c in cands if not c.score]

    use_vlm = bool(payload.get("use_vlm"))
    if use_vlm and not vlm_available():
        raise RuntimeError(
            f"本地 VLM 不可用（{config.MLX_VLM_BIN.name} / {config.LLM_VLM_MODEL}），"
            "请先关闭「VLM 点评」或安装模型"
        )

    scored = []
    total = len(cands)
    for i, cand in enumerate(cands):
        path = config.PROJECTS_DIR / cand.path
        if not path.exists() or path.stat().st_size == 0:
            continue
        obj = score_objective(path, expect_color=expect_color)
        vlm = None
        if use_vlm:
            pnl = session.get(Panel, cand.panel_id)
            desc = (pnl.action if pnl else "") or ""
            try:
                vlm = vlm_review(path, desc)
            except Exception as e:              # VLM 失败不应让整批评分失败
                vlm = {"score": None, "issues": [f"VLM 评分失败：{e}"], "comment": "", "source": "vlm"}
        merged = combine(obj, vlm)
        cand.score = merged["score"]
        cand.score_detail = json.dumps(merged, ensure_ascii=False)
        cand.scored_at = datetime.now(timezone.utc)
        session.add(cand)
        session.commit()
        scored.append({"candidate_id": cand.id, "score": cand.score})
        _set_progress(session, job, i + 1, total, f"评分 {i + 1}/{total}")

    # 评分后顺手算出每格最佳候选（只报告，不自动选择）
    best: dict[str, str] = {}
    best_score: dict[str, float] = {}
    affected = {c.panel_id for c in cands if c.panel_id}
    if affected:
        for c in session.exec(select(Candidate)).all():
            if c.panel_id in affected and c.score and c.score > best_score.get(c.panel_id, -1):
                best[c.panel_id] = c.id
                best_score[c.panel_id] = c.score
    return {
        "scored": len(scored),
        "use_vlm": use_vlm,
        "expect_color": expect_color,
        "results": scored[:50],
        "best": best,
    }


def _handle_upgrade_style(session: Session, job: Job, payload: dict) -> dict:
    """一键升级画风：切风格 → 重生成全部资产设定图 → 重跑全部分镜 → 渲染。

    用于「老黑白项目升级彩色」这类整体换风格场景（资产一改、全场同步的编排版）。
    """
    project = session.get(Project, payload["project_id"])
    if not project:
        raise RuntimeError("项目不存在")
    new_style = payload.get("style") or project.style
    steps = payload.get("steps")          # 留空用风格推荐步数
    provider = payload.get("provider") or "mlx"

    total_units = 0
    done_units = 0

    def tick(label: str, n: int = 1):
        nonlocal done_units
        done_units += n
        _set_progress(session, job, done_units, max(total_units, 1), label)

    # ① 收集工作量
    chars = session.exec(select(Character).where(Character.project_id == project.id)).all()
    assets = session.exec(select(Asset).where(Asset.project_id == project.id)).all()
    panels = sorted(
        session.exec(select(Panel).where(Panel.project_id == project.id)).all(),
        key=lambda p: p.index,
    )
    # 只重跑「已经有候选图」的格子——从没出过图的格子保持原状（用户可能还没定稿）
    has_cand: dict[str, bool] = {}
    for c in session.exec(select(Candidate)).all():
        has_cand[c.panel_id] = True
    panels_with_image = [p for p in panels if has_cand.get(p.id)]

    total_units = len(chars) + len(assets) + len(panels_with_image) + 1
    tick(f"切换画风为 {new_style}", 0)

    # ② 切风格
    project.style = new_style
    session.add(project)
    session.commit()

    gen_provider = get_provider(provider)

    # ③ 人物设定图（彩色）
    for ch in chars:
        prompt = apply_style_directive(
            project.style, build_sheet_prompt(project, ch, payload.get("char_view", "multi"))
        )
        out_dir = character_dir(project.id, ch.id)
        seed = (payload.get("seed") or 20260925)
        out = out_dir / f"sheet_{new_style}_{seed}_{_run_token()}.png"
        gen_provider.generate(GenRequest(
            prompt=prompt, output_path=out,
            width=config.SHEET_WIDTH, height=config.SHEET_HEIGHT,
            steps=steps or config.style_rec(new_style)["steps"], seed=seed,
        ))
        rel = str(out.relative_to(config.PROJECTS_DIR))
        ch.reference_image = rel
        extra = {}
        try:
            extra = json.loads(ch.extra or "{}")
        except Exception:
            extra = {}
        extra.setdefault("reference_images", []).append(rel)
        ch.extra = json.dumps(extra, ensure_ascii=False)
        session.add(ch)
        session.commit()
        tick(f"人物设定图 {ch.name}")

    # ④ 资产设定图（场景/道具）
    for a in assets:
        prompt = apply_style_directive(
            project.style, build_asset_sheet_prompt(project, a, payload.get("asset_view", ""))
        )
        out_dir = asset_dir(project.id, a.kind, a.id)
        seed = (payload.get("seed") or 20260925) + 6421
        out = out_dir / f"sheet_{new_style}_{seed}_{_run_token()}.png"
        gen_provider.generate(GenRequest(
            prompt=prompt, output_path=out,
            width=config.SHEET_WIDTH, height=config.SHEET_HEIGHT,
            steps=steps or config.style_rec(new_style)["steps"], seed=seed,
        ))
        rel = str(out.relative_to(config.PROJECTS_DIR))
        a.reference_image = rel
        extra = {}
        try:
            extra = json.loads(a.extra or "{}")
        except Exception:
            extra = {}
        extra.setdefault("reference_images", []).append(rel)
        a.extra = json.dumps(extra, ensure_ascii=False)
        session.add(a)
        session.commit()
        tick(f"资产设定图 {a.name}")

    # ⑤ 重跑全部分镜（带参考图）
    for p in panels_with_image:
        sub = Job(
            type="panel_draft",
            payload=json.dumps({
                "panel_id": p.id, "count": 1, "provider": gen_provider.name,
                "use_references": True, "auto_select": True,
            }, ensure_ascii=False),
            project_id=project.id,
        )
        session.add(sub)
        session.commit()
        _run_one(sub.id)
        tick(f"分镜 {p.index}")

    # ⑥ 渲染全部页面
    pages = session.exec(select(Page).where(Page.project_id == project.id)).all()
    for pg in pages:
        layout_mod.render_page(session, pg)
    tick("页面渲染")

    return {
        "style": new_style,
        "character_sheets": len(chars),
        "asset_sheets": len(assets),
        "panels_rerun": len(panels_with_image),
        "pages_rendered": len(pages),
    }


_HANDLERS = {
    "storyboard": _handle_storyboard,
    "character_sheet": _handle_character_sheet,
    "asset_sheet": _handle_asset_sheet,
    "panel_draft": lambda s, j, p: _handle_panel_generation(s, j, p, "draft"),
    "panel_final": lambda s, j, p: _handle_panel_generation(s, j, p, "final"),
    "panel_inpaint": _handle_panel_inpaint,
    "score_candidates": _handle_score_candidates,
    "translate_dialogues": _handle_translate,
    "train_lora": _handle_train_lora,
    "upgrade_style": _handle_upgrade_style,
    "page_render": _handle_page_render,
    "export": _handle_export,
}


# ── Worker 循环 ──────────────────────────────────────

def _run_one(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.status != "pending":
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()
        try:
            payload = json.loads(job.payload or "{}")
            handler = _HANDLERS.get(job.type)
            if handler is None:
                raise RuntimeError(f"未知任务类型: {job.type}")
            result = handler(session, job, payload)
            job.result = json.dumps(result, ensure_ascii=False, default=str)
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.error = f"{e}\n{traceback.format_exc()[-1500:]}"
        finally:
            job.finished_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()


def _loop(interval: float = 1.0) -> None:
    while not _stop.is_set():
        picked = False
        try:
            with Session(engine) as session:
                job = session.exec(
                    select(Job).where(Job.status == "pending")
                    .order_by(Job.created_at)
                ).first()
                job_id = job.id if job else None
            if job_id:
                picked = True
                _run_one(job_id)
        except Exception:
            traceback.print_exc()
        if not picked:
            _stop.wait(interval)


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="cw-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()


def enqueue(job_type: str, payload: dict, project_id: str = "") -> Job:
    with Session(engine) as session:
        job = Job(
            type=job_type,
            payload=json.dumps(payload, ensure_ascii=False),
            project_id=project_id,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def run_job_blocking(job: Job) -> Job:
    """测试/同步执行入口。"""
    _run_one(job.id)
    with Session(engine) as session:
        return session.get(Job, job.id)
