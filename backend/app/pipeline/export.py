"""导出：PNG / PDF / CBZ。"""
from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image
from sqlmodel import Session, select

from .. import config
from ..models import Page, Project
from . import layout as layout_mod


def render_all_pages(session: Session, project: Project, lang: str = "") -> list[Path]:
    pages = sorted(
        session.exec(select(Page).where(Page.project_id == project.id)).all(),
        key=lambda p: p.index,
    )
    out: list[Path] = []
    for page in pages:
        out.append(layout_mod.render_page(session, page, lang=lang))
    return out


def export_project(session: Session, project: Project, fmt: str, lang: str = "") -> list[Path]:
    # 多语言：非中文时页面文件带语言后缀，导出目录按语言分开
    pages = render_all_pages(session, project, lang=lang)
    if not pages:
        raise RuntimeError("项目没有可导出的页面")
    sub = f"{fmt}" if not lang else f"{fmt}_{lang}"
    base = config.project_dir(project.id) / "exports" / sub
    base.mkdir(parents=True, exist_ok=True)
    suffix = f"_{lang}" if lang else ""

    if fmt == "png":
        targets = [base / f"page_{i + 1:03d}{suffix}.png" for i in range(len(pages))]
        for src, dst in zip(pages, targets):
            dst.write_bytes(src.read_bytes())
        return targets

    if fmt == "pdf":
        imgs = [Image.open(p).convert("RGB") for p in pages]
        pdf_path = base / f"{project.name}_comic{suffix}.pdf"
        imgs[0].save(pdf_path, save_all=True, append_images=imgs[1:], resolution=150.0)
        return [pdf_path]

    if fmt == "cbz":
        cbz_path = base / f"{project.name}{suffix}.cbz"
        with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_STORED) as zf:
            for i, p in enumerate(pages):
                zf.write(p, arcname=f"page_{i + 1:03d}{suffix}.png")
        return [cbz_path]

    raise RuntimeError(f"不支持的导出格式: {fmt}（可选 png/pdf/cbz）")
