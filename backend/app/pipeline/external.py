"""外部编辑器往返（蓝图 §17：Krita 是人工修图终端，不是被替代对象）。

    Workbench ──导出工作图──▶ <project>/panels/<panel>/edit/work.png
                                     │  在你惯用的编辑器里改（Krita / Photoshop / 预览…）
    Workbench ◀──回填────────────  work.png（改动后作为新候选进入候选池）

本机未安装 Krita 时也能用：导出后自动在 Finder 里定位文件，改动完点「回填」即可。
装了 Krita 则自动 `open -a Krita` 打开。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..models import Candidate, Panel

EDITOR_CANDIDATES = ["Krita", "krita", "Adobe Photoshop 2024", "Photoshop", "Pixelmator Pro", "GIMP"]
META_NAME = "work.meta.json"


def edit_dir(project_id: str, panel_id: str) -> Path:
    d = config.project_dir(project_id) / "panels" / panel_id / "edit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pick_editor() -> str:
    """返回本机可用的图像编辑器名（用于 `open -a`）。"""
    apps = [p for p in Path("/Applications").glob("*.app")]
    names = {p.stem for p in apps}
    for want in EDITOR_CANDIDATES:
        if want in names:
            return want
    return ""


def _current_source(session: Session, panel: Panel, candidate_id: str = "") -> Candidate | None:
    cand: Candidate | None = None
    if candidate_id:
        cand = session.get(Candidate, candidate_id)
    if cand is None and panel.selected_candidate_id:
        cand = session.get(Candidate, panel.selected_candidate_id)
    if cand is None:
        cands = session.exec(
            select(Candidate).where(Candidate.panel_id == panel.id)
        ).all()
        cand = cands[-1] if cands else None
    return cand


def _digest(path: Path) -> str:
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()


def export_for_edit(
    session: Session, panel: Panel, candidate_id: str = "", open_editor: bool = True
) -> dict:
    cand = _current_source(session, panel, candidate_id)
    if cand is None or not cand.path:
        raise RuntimeError("该分镜格还没有可用图像，请先生成候选")
    src = config.PROJECTS_DIR / cand.path
    if not src.exists() or src.stat().st_size == 0:
        raise RuntimeError(f"候选图不存在: {src}")

    d = edit_dir(panel.project_id, panel.id)
    work = d / "work.png"
    shutil.copyfile(src, work)
    meta = {
        "panel_id": panel.id,
        "source_candidate": cand.id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_md5": _digest(work),
    }
    (d / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    editor = _pick_editor()
    opened = ""
    if open_editor:
        if editor:
            subprocess.Popen(["open", "-a", editor, str(work)])
            opened = f"{editor} 已打开"
        else:
            subprocess.Popen(["open", "-R", str(work)])  # Finder 定位
            opened = "未检测到 Krita/Photoshop，已在 Finder 中定位工作图"
    return {
        "work_path": str(work),
        "url": f"/files/{work.relative_to(config.PROJECTS_DIR)}",
        "source_candidate": cand.id,
        "editor": editor,
        "opened": opened,
    }


def import_from_edit(session: Session, panel: Panel) -> dict:
    d = edit_dir(panel.project_id, panel.id)
    work = d / "work.png"
    meta_path = d / META_NAME
    if not work.exists() or work.stat().st_size == 0:
        raise RuntimeError("没有可回填的工作图，请先「外部编辑」导出")
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
    # 与导出时的内容一致 = 用户没改，无需回填
    if meta.get("exported_md5") and _digest(work) == meta["exported_md5"]:
        raise RuntimeError("工作图与导出时完全一致（没有改动），无需回填")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = config.project_dir(panel.project_id) / "panels" / panel.id / "manual"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"manual_{stamp}.png"
    shutil.copyfile(work, dest)

    cand = Candidate(
        panel_id=panel.id,
        path=str(dest.relative_to(config.PROJECTS_DIR)),
        prompt="[外部编辑器人工修改]",
        provider="external",
        model=meta.get("editor", "") or "external",        stage="manual",
        params=json.dumps({
            "source_candidate": meta.get("source_candidate", ""),
            "exported_at": meta.get("exported_at", ""),
            "size": work.stat().st_size,
        }, ensure_ascii=False),
    )
    session.add(cand)
    panel.status = "review"
    session.add(panel)
    session.commit()
    session.refresh(cand)

    meta["imported_once"] = True
    meta["last_import"] = cand.id
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {"candidate": cand.model_dump(), "url": f"/files/{cand.path}"}
