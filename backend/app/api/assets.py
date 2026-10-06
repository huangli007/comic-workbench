"""项目资产库 API —— 人物 / 场景 / 道具（一致性资产）。

- 人物沿用 Character（已有接口），这里额外把人物也纳入统一视图以便前端一次拉全
- 场景 / 道具使用 Asset 表，能力与人物对齐：文字设定 + 设定图 + 参考图
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import ASSET_KINDS, Asset, Character, Panel, Project
from ..pipeline import worker

router = APIRouter(prefix="/api", tags=["assets"])

VALID_KINDS = tuple(ASSET_KINDS.keys())


class AssetCreate(BaseModel):
    kind: str                # scene | prop
    name: str
    description: str = ""    # 英文设定描述（进 prompt）
    appearance: str = ""     # 兼容字段：与 description 同义


class AssetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    appearance: str | None = None


class LoraTrainRequest(BaseModel):
    epochs: int = 30
    rank: int = 16
    lr: float = 1e-4
    quantize: int | None = None      # 8/4 可在 16GB 机器上省内存（QLoRA 式训练）
    max_resolution: int = 1024       # 样本长边上限，降低可省内存
    seed: int = 42


class AssetRegenRequest(BaseModel):
    count: int = 1
    use_references: bool = True
    provider: str = ""


class AssetSheetRequest(BaseModel):
    count: int = 1
    view: str = ""           # scene: wide|interior|exterior ; prop: multi|front|detail
    seed: int | None = None
    steps: int | None = None
    provider: str = "mlx"


def _asset_dict(asset: Asset) -> dict:
    refs = asset.ref_images()
    if asset.reference_image and asset.reference_image not in refs:
        refs.insert(0, asset.reference_image)
    return {
        **asset.model_dump(),
        "kind_label": ASSET_KINDS.get(asset.kind, asset.kind),
        "text": asset.description or asset.appearance or asset.name,
        "reference_urls": [f"/files/{r}" for r in refs],
    }


@router.get("/asset-kinds")
def asset_kinds():
    return [{"key": k, "label": v} for k, v in ASSET_KINDS.items()]


@router.get("/projects/{pid}/assets")
def list_assets(pid: str, kind: str = "", session: Session = Depends(get_session)):
    """列出项目资产。kind 留空则返回 人物 + 场景 + 道具 的统一视图。"""
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")

    out: dict[str, list] = {"character": [], "scene": [], "prop": []}
    if kind in ("", "character"):
        chars = session.exec(select(Character).where(Character.project_id == pid)).all()
        for c in chars:
            refs = c.ref_images()
            if c.reference_image and c.reference_image not in refs:
                refs.insert(0, c.reference_image)
            out["character"].append({
                **c.model_dump(),
                "kind": "character",
                "kind_label": "人物",
                "text": c.appearance or c.name,
                "reference_urls": [f"/files/{r}" for r in refs],
            })
    if kind in ("", "scene", "prop"):
        q = select(Asset).where(Asset.project_id == pid)
        if kind:
            q = select(Asset).where(Asset.project_id == pid, Asset.kind == kind)  # type: ignore
        for a in session.exec(q).all():
            out[a.kind if a.kind in out else "prop"].append(_asset_dict(a))

    if kind:
        return out.get(kind, [])
    # 统一视图：附带每个资产被多少分镜引用
    usage: dict[str, int] = {}
    for p in session.exec(select(Panel).where(Panel.project_id == pid)).all():
        for cid in p.char_ids():
            usage[cid] = usage.get(cid, 0) + 1
        if p.scene_id:
            usage[p.scene_id] = usage.get(p.scene_id, 0) + 1
        for aid in p.prop_id_list():
            usage[aid] = usage.get(aid, 0) + 1
    for items in out.values():
        for item in items:
            item["usage"] = usage.get(item["id"], 0)
    return out


@router.post("/projects/{pid}/assets")
def create_asset(pid: str, body: AssetCreate, session: Session = Depends(get_session)):
    project = session.get(Project, pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if body.kind not in VALID_KINDS:
        raise HTTPException(400, f"不支持的资产类型：{body.kind}（可选 {'/'.join(VALID_KINDS)}）")
    if not body.name.strip():
        raise HTTPException(400, "资产名称不能为空")
    asset = Asset(
        project_id=pid, kind=body.kind, name=body.name.strip(),
        description=body.description or body.appearance,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return _asset_dict(asset)


@router.put("/assets/{aid}")
def update_asset(aid: str, body: AssetUpdate, session: Session = Depends(get_session)):
    asset = session.get(Asset, aid)
    if not asset:
        raise HTTPException(404, "资产不存在")
    if body.name is not None:
        asset.name = body.name
    text = body.description if body.description is not None else body.appearance
    if text is not None:
        asset.description = text
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return _asset_dict(asset)


@router.delete("/assets/{aid}")
def delete_asset(aid: str, session: Session = Depends(get_session)):
    asset = session.get(Asset, aid)
    if not asset:
        raise HTTPException(404, "资产不存在")
    # 引用清理：把分镜上的引用摘掉（不删分镜）
    for p in session.exec(select(Panel).where(Panel.project_id == asset.project_id)).all():
        changed = False
        if asset.kind == "scene" and p.scene_id == aid:
            p.scene_id = ""
            changed = True
        if asset.kind == "prop" and aid in p.prop_id_list():
            p.prop_ids = str([x for x in p.prop_id_list() if x != aid]).replace("'", '"')
            changed = True
        if changed:
            session.add(p)
    session.delete(asset)
    session.commit()
    return {"ok": True}


@router.post("/assets/{aid}/regenerate")
def regenerate_asset_panels(aid: str, body: AssetRegenRequest, session: Session = Depends(get_session)):
    """资产一改，全场同步：重跑所有引用该资产的分镜（场景走 scene_id，道具走 prop_ids）。"""
    asset = session.get(Asset, aid)
    if not asset:
        raise HTTPException(404, "资产不存在")
    panels = [
        p for p in session.exec(
            select(Panel).where(Panel.project_id == asset.project_id)
        ).all()
        if (asset.kind == "scene" and p.scene_id == aid)
        or (asset.kind == "prop" and aid in p.prop_id_list())
    ]
    if not panels:
        return {"enqueued": 0, "job_ids": [], "message": "没有分镜引用该资产"}
    job_ids = []
    for p in panels:
        job = worker.enqueue("panel_draft", {
            "panel_id": p.id,
            "count": max(1, min(body.count, 4)),
            "provider": body.provider,
            "use_references": body.use_references,
        }, project_id=asset.project_id)
        job_ids.append(job.id)
    return {"enqueued": len(job_ids), "job_ids": job_ids}


@router.post("/characters/{cid}/train-lora")
def train_lora(cid: str, body: LoraTrainRequest, session: Session = Depends(get_session)):
    """训练该人物的 LoRA（数据集自动从设定图与已出图分镜准备）。

    注意：mflux-train 需要完整精度的 FLUX.2-klein-4B；本地仅有 8bit 量化版时会去下载
    （约 16GB），耗时较长。
    """
    ch = session.get(Character, cid)
    if not ch:
        raise HTTPException(404, "人物不存在")
    return worker.enqueue("train_lora", {
        "project_id": ch.project_id, "character_id": cid,
        "epochs": max(1, min(body.epochs, 200)),
        "rank": max(2, min(body.rank, 64)),
        "lr": body.lr,
        "quantize": body.quantize,
        "max_resolution": max(256, min(body.max_resolution, 2048)),
        "seed": body.seed,
    }, project_id=ch.project_id)


@router.post("/characters/{cid}/regenerate")
def regenerate_character_panels(cid: str, body: AssetRegenRequest, session: Session = Depends(get_session)):
    """人物设定图更新后，重跑所有引用该人物的分镜。"""
    ch = session.get(Character, cid)
    if not ch:
        raise HTTPException(404, "人物不存在")
    panels = [
        p for p in session.exec(
            select(Panel).where(Panel.project_id == ch.project_id)
        ).all()
        if cid in p.char_ids()
    ]
    if not panels:
        return {"enqueued": 0, "job_ids": [], "message": "没有分镜引用该人物"}
    job_ids = []
    for p in panels:
        job = worker.enqueue("panel_draft", {
            "panel_id": p.id,
            "count": max(1, min(body.count, 4)),
            "provider": body.provider,
            "use_references": body.use_references,
        }, project_id=ch.project_id)
        job_ids.append(job.id)
    return {"enqueued": len(job_ids), "job_ids": job_ids}


@router.post("/assets/{aid}/sheet")
def asset_sheet(aid: str, body: AssetSheetRequest, session: Session = Depends(get_session)):
    """生成资产设定图（场景背景图 / 道具三视图），并自动登记为参考图。"""
    asset = session.get(Asset, aid)
    if not asset:
        raise HTTPException(404, "资产不存在")
    job = worker.enqueue("asset_sheet", {
        "asset_id": aid,
        "kind": asset.kind,
        "count": max(1, min(body.count, 3)),
        "view": body.view,
        "seed": body.seed,
        "steps": body.steps,
        "provider": body.provider,
    }, project_id=asset.project_id)
    return job
