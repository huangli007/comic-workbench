#!/usr/bin/env python3
"""从磁盘资产恢复项目数据库（DB 丢失/重置后的自救工具）。

恢复内容：
- Project（名字优先取导出 PDF 的名字，风格从设定图文件名推断）
- Page / Panel（页面数按 pages/page_XXX.png 推断，面板目录按序分配到各页）
- Candidate（panels/<id>/{draft,inpaint,manual}/*.png，含 seed/stage）
- Character（characters/<id>/ 的设定图）与 Asset（assets/scene|prop/<id>/）

用法：
  python scripts/recover_projects.py [--dry-run]
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Asset, Candidate, Character, Page, Panel, Project  # noqa: E402

DRY = "--dry-run" in sys.argv


def infer_style(pdir: Path) -> str:
    chars = pdir / "characters"
    if chars.exists():
        for f in chars.rglob("sheet_*.png"):
            m = re.match(r"sheet_([a-z_]+?)_\d+", f.name)
            if m and m.group(1) in config.STYLE_PRESETS:
                return m.group(1)
    return config.DEFAULT_STYLE


KNOWN_NAMES = {
    "proj_9d2b26213b": "资产库验证",
    "proj_b556125a65": "雨夜",
    "proj_a190d2f3b2": "雨夜一致性",
    "proj_e37e5dbc41": "ComfyUI 通路验证",
}


def infer_name(pdir: Path) -> str:
    if pdir.name in KNOWN_NAMES:
        return KNOWN_NAMES[pdir.name]
    pdfs = sorted((pdir / "exports").rglob("*.pdf"))
    for f in pdfs:
        name = re.sub(r"(_comic)?(_[a-zA-Z-]+)?\.pdf$", "", f.name)
        if name:
            return name
    return pdir.name


def main():
    init_db()
    root = config.PROJECTS_DIR
    restored = 0
    with Session(engine) as s:
        existing = {p.id for p in s.exec(select(Project)).all()}
        for pdir in sorted(root.iterdir()):
            if not pdir.is_dir() or pdir.name in existing:
                continue
            style = infer_style(pdir)
            name = infer_name(pdir)
            project = s.get(Project, pdir.name)
            if project is None:
                project = Project(id=pdir.name, name=name, style=style)
                if not DRY:
                    s.add(project)
            else:
                project.style = style
            print(f"项目 {pdir.name}「{name}」风格={style}")

            # ── 人物 ──
            for cdir in sorted((pdir / "characters").glob("*")):
                if not cdir.is_dir():
                    continue
                sheets = sorted(cdir.glob("sheet_*.png"))
                if s.get(Character, cdir.name):
                    print(f"  人物 {cdir.name} 已存在，跳过")
                    continue
                ch = Character(
                    id=cdir.name, project_id=pdir.name, name="（恢复）人物",
                    appearance="", reference_image=str(sheets[-1].relative_to(config.PROJECTS_DIR)) if sheets else "",
                )
                if not DRY:
                    s.add(ch)
                print(f"  人物 {cdir.name} 设定图 {len(sheets)}")

            # ── 资产（场景/道具）──
            for kind in ("scene", "prop"):
                for adir in sorted((pdir / "assets" / kind).glob("*")) if (pdir / "assets" / kind).exists() else []:
                    if not adir.is_dir():
                        continue
                    sheets = sorted(adir.glob("sheet_*.png"))
                    if s.get(Asset, adir.name):
                        print(f"  资产[{kind}] {adir.name} 已存在，跳过")
                        continue
                    a = Asset(
                        id=adir.name, project_id=pdir.name, kind=kind, name=f"（恢复）{kind}",
                        reference_image=str(sheets[-1].relative_to(config.PROJECTS_DIR)) if sheets else "",
                    )
                    if not DRY:
                        s.add(a)
                    print(f"  资产[{kind}] {adir.name} 设定图 {len(sheets)}")

            # ── 页面（由 pages/page_XXX.png 推断页数）──
            page_pngs = sorted((pdir / "pages").glob("page_*.png"))
            page_ids = sorted({re.match(r"page_(\d+)", f.name).group(1) for f in page_pngs}) or ["1"]
            pages = []
            for i, num in enumerate(page_ids, start=1):
                pg = s.get(Page, f"page_rec_{pdir.name}_{num}")
                if pg is None:
                    pg = Page(id=f"page_rec_{pdir.name}_{num}", project_id=pdir.name, index=int(num))
                    if not DRY:
                        s.add(pg)
                pages.append(pg)

            # ── 分镜（panel 目录按修改时间序分配到各页，均分）──
            panel_dirs = sorted(
                (d for d in (pdir / "panels").glob("*") if d.is_dir()),
                key=lambda d: d.stat().st_mtime,
            )
            n_pages = max(1, len(page_ids))
            per = (len(panel_dirs) + n_pages - 1) // n_pages if panel_dirs else 0
            for pi, pdir_panel in enumerate(panel_dirs):
                page = pages[min(pi // max(per, 1), len(pages) - 1)]
                idx = (pi % max(per, 1)) + 1
                if s.get(Panel, pdir_panel.name):
                    print(f"  分镜 {pdir_panel.name[:20]} 已存在，跳过")
                    continue
                panel = Panel(
                    id=pdir_panel.name, project_id=pdir.name, page_id=page.id,
                    index=idx, status="review",
                )
                if not DRY:
                    s.add(panel)
                # 候选
                for stage in ("draft", "inpaint", "manual", "final"):
                    sdir = pdir_panel / stage
                    if not sdir.is_dir():
                        continue
                    for img in sorted(sdir.glob("*.png")):
                        if img.stat().st_size == 0:
                            continue
                        cid = f"cand_rec_{stage}_{img.stem}"
                        if s.get(Candidate, cid):
                            continue
                        c = Candidate(
                            id=cid, panel_id=panel.id,
                            path=str(img.relative_to(config.PROJECTS_DIR)),
                            provider="recovered", stage=stage, seed=0, score=0.0,
                        )
                        if not DRY:
                            s.add(c)
                print(f"  分镜 {pdir_panel.name[:20]} → 页 {page.index}（含候选）")

            restored += 1
        if not DRY:
            s.commit()
    print(f"\n恢复 {restored} 个项目{'（dry-run 未写库）' if DRY else ''}")


if __name__ == "__main__":
    main()
