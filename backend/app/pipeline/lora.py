"""角色 LoRA 训练（MVP-3，权重级一致性）。

一致性三档：
    1. 文字描述        最弱
    2. 参考图条件生成  中等（当前主力）
    3. LoRA 权重       最强 ← 本模块

⚠️ 本机现状：mflux-train 需要 **完整精度的 FLUX.2-klein-4B**（内置名 `flux2-klein-4b`，
会去拉 `black-forest-labs/FLUX.2-klein-4B`，约 16GB）。本机当前只有 8bit 量化版
（toxicdog，平铺目录），拿它训练会落到 Flux1 分支并报 "Flux1 training is no longer supported."。
所以：**链路已就绪，训练前需要先下载完整权重**；下面的 `trainable()` 会自检并给出明确原因。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..models import Asset, Candidate, Character, Panel, Project

# mflux-train 内置名（不是本地路径）
LORA_BASE_MODEL = "flux2-klein-4b"
DEFAULT_EPOCHS = 30


def lora_dir(project_id: str, character_id: str) -> Path:
    d = config.project_dir(project_id) / "lora" / character_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def trainable() -> tuple[bool, str]:
    """能否真的开始训练（前置：mflux-train 存在 + 完整权重可获取）。"""
    if not config.MLX_TRAIN_BIN.exists():
        return False, f"缺少 {config.MLX_TRAIN_BIN.name}"
    # 已缓存完整权重则可离线训练
    base = Path.home() / ".cache/huggingface" / "hub" / "models--black-forest-labs--FLUX.2-klein-4B"
    big = [p for p in base.rglob("*.safetensors") if p.stat().st_size > 100 * 1024 * 1024] if base.exists() else []
    if len(big) >= 4:
        return True, "ok"
    return True, (
        f"缺少完整精度的 {LORA_BASE_MODEL} 权重（当前只有 {len(big)} 个大文件）。"
        "HF 镜像对本 repo 限速到 ~90KB/s（15GB 需 50 小时），请改用 ModelScope 镜像下载：\n"
        "  python scripts/ms_hf_download.py black-forest-labs/FLUX.2-klein-4B "
        "--files \"transformer/diffusion_pytorch_model.safetensors,"
        "text_encoder/model-00001-of-00002.safetensors,"
        "text_encoder/model-00002-of-00002.safetensors,"
        "vae/diffusion_pytorch_model.safetensors\" --jobs 3"
    )


def caption_for(character: Character) -> str:
    """训练提示词：用「名字 + 外貌描述」当触发词组合。"""
    parts = [character.name]
    if character.appearance:
        parts.append(character.appearance)
    return ", ".join(parts)


def _same_name_elsewhere(session: Session, character: Character) -> list[Character]:
    """其它项目里同名的角色（同一角色的跨项目素材，可显著增加训练样本）。"""
    others = session.exec(select(Character).where(Character.project_id != character.project_id)).all()
    return [c for c in others if c.name == character.name and c.name]


def prepare_dataset(
    session: Session,
    project: Project,
    character: Character,
    max_images: int = 8,
) -> tuple[Path, int]:
    """收集该角色的图片（设定图优先当前画风 + 含该角色的分镜图），生成平铺数据集。

    mflux 要求：data 目录下图片（png/jpg/webp）+ 同名 .txt 提示词，平铺、不分子目录。
    """
    d = lora_dir(project.id, character.id) / "dataset"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)

    imgs: list[Path] = []
    # ① 设定图（优先当前画风）
    ch_dir = config.project_dir(project.id) / "characters" / character.id
    styled = sorted(ch_dir.glob(f"sheet_{project.style}_*.png"))
    imgs += styled
    if not styled:
        imgs += sorted(ch_dir.glob("sheet_*.png"))
    if character.reference_image:
        p = config.PROJECTS_DIR / character.reference_image
        if p.exists():
            imgs.insert(0, p)

    # ② 含该角色的分镜图（取选中候选）
    order = sorted(
        session.exec(select(Panel).where(Panel.project_id == project.id)).all(),
        key=lambda p: p.index,
    )
    for p in order:
        if character.id not in p.char_ids():
            continue
        if p.selected_candidate_id:
            c = session.get(Candidate, p.selected_candidate_id)
            if c and c.path:
                ip = config.PROJECTS_DIR / c.path
                if ip.exists():
                    imgs.append(ip)

    # ③ 样本不足时，从其它项目收集同名角色的图（同一角色的跨项目素材）
    if len(imgs) < 5:
        for other in _same_name_elsewhere(session, character):
            odir = config.project_dir(other.project_id) / "characters" / other.id
            imgs += sorted(odir.glob("sheet_*.png"))[-2:]
            if other.reference_image:
                rp = config.PROJECTS_DIR / other.reference_image
                if rp.exists():
                    imgs.append(rp)
            for p in session.exec(select(Panel).where(Panel.project_id == other.project_id)).all():
                if other.id in p.char_ids() and p.selected_candidate_id:
                    c = session.get(Candidate, p.selected_candidate_id)
                    if c and c.path:
                        cp = config.PROJECTS_DIR / c.path
                        if cp.exists():
                            imgs.append(cp)

    seen: set[str] = set()
    n = 0
    caption = caption_for(character)
    for src in imgs:
        if n >= max_images:
            break
        key = str(src.resolve())
        if key in seen or not src.exists() or src.stat().st_size == 0:
            continue
        seen.add(key)
        dst = d / f"{character.name}_{n:02d}.png"
        shutil.copyfile(src, dst)
        (d / f"{character.name}_{n:02d}.txt").write_text(caption, encoding="utf-8")
        n += 1
    return d, n


def build_train_config(
    data_dir: Path,
    out_dir: Path,
    epochs: int = DEFAULT_EPOCHS,
    rank: int = 16,
    lr: float = 1e-4,
    max_resolution: int = 1024,
    quantize: int | None = None,
    seed: int = 42,
    save_frequency: int | None = None,
    preview_frequency: int | None = None,
) -> Path:
    """生成 mflux-train 所需配置（与 workflows/train_character_lora.json 同结构）。

    注意：mflux 会把 config 里的 data / output_path 当作**相对 config 文件所在目录**解析，
    所以这里必须先绝对化，否则会拼出 .../lora/<cid>/projects/.../dataset 这种重复路径（踩过）。
    """
    data_dir = Path(data_dir).resolve()
    out_dir = Path(out_dir).resolve()
    # LoRA 目标路径必须与 mflux 的模块结构严格对应（踩过 AttributeError）：
    #   transformer_blocks[i].attn 是 Flux2Attention（to_q/to_k/to_v/to_out）
    #   single_transformer_blocks[i].attn 是 Flux2ParallelSelfAttention（to_qkv_mlp_proj/to_out）
    # blocks 的 end 是 **exclusive**（实现为 range(start, end)）；
    # flux2-klein-4b：num_layers=5、num_single_layers=20
    cfg = {
        "model": LORA_BASE_MODEL,
        "data": str(data_dir),
        "seed": int(seed),
        "steps": 6,
        "guidance": 1.0,
        "quantize": quantize,
        "max_resolution": max_resolution,
        "low_ram": True,
        "training_loop": {
            "num_epochs": int(epochs), "batch_size": 1,
            "timestep_low": 2, "timestep_high": 6,
        },
        "optimizer": {"name": "AdamW", "learning_rate": float(lr)},
        "checkpoint": {
            # save_frequency 是**按步**计的（不是按 epoch）。每存一次要打包
            # ~185MB 的 LoRA+优化器状态，既慢又是 16GB 机器上的内存峰值来源
            # （实测每步 4-7s，但每步都存 checkpoint 时平均被拉到 90s+ 且会 OOM）。
            # 默认只在训练结束时保存一次。
            "save_frequency": int(save_frequency or max(1, epochs) * 1000),
            "output_path": str(out_dir),
        },
        "monitoring": {
            # 预览尺寸跟随训练分辨率，且只在末尾生成一次：
            # 每次 preview 都要额外前向 + VAE 解码，是 16GB 机器上的内存峰值来源之一
            "preview_width": 384 if max_resolution <= 640 else 512,
            "preview_height": 576 if max_resolution <= 640 else 768,
            "plot_frequency": 1,
            # 同样是**按步**计。每次预览都要完整推理 6 步 + VAE 解码（约 60-70s
            # 且是内存峰值），默认只在训练结束时出 1 张（踩过：按 epoch 数填会退化成
            # 每 N 步一次，24 步生成 8 张预览，把 5 分钟的活拖成 20 分钟）。
            "generate_image_frequency": int(preview_frequency or max(1, epochs) * 1000),
        },
        "lora_layers": {"targets": [
            {"module_path": "transformer_blocks.{block}.attn.to_q", "blocks": {"start": 0, "end": 5}, "rank": int(rank)},
            {"module_path": "transformer_blocks.{block}.attn.to_k", "blocks": {"start": 0, "end": 5}, "rank": int(rank)},
            {"module_path": "transformer_blocks.{block}.attn.to_v", "blocks": {"start": 0, "end": 5}, "rank": int(rank)},
            {"module_path": "transformer_blocks.{block}.attn.to_out", "blocks": {"start": 0, "end": 5}, "rank": int(rank)},
            {"module_path": "single_transformer_blocks.{block}.attn.to_qkv_mlp_proj", "blocks": {"start": 0, "end": 20}, "rank": int(rank)},
            {"module_path": "single_transformer_blocks.{block}.attn.to_out", "blocks": {"start": 0, "end": 20}, "rank": int(rank)},
        ]},
    }
    p = out_dir.parent / "train_config.json"
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return p


def find_adapter(out_dir: Path) -> Path | None:
    """找到训练产物里的 adapter 权重。

    mflux-train 把 adapter 打包在 `checkpoints/<step>_checkpoint.zip` 里
    （`<step>_adapter.safetensors`），所以要先找散落的 safetensors，
    找不到就从最新的 checkpoint.zip 里提取出来（踩过）。
    """
    direct = sorted(out_dir.rglob("*adapter*.safetensors"))
    if direct:
        return direct[-1]

    zips = sorted(out_dir.rglob("*checkpoint*.zip"), key=lambda p: p.stat().st_mtime)
    for zp in reversed(zips):                       # 从最新的开始试
        try:
            with zipfile.ZipFile(zp) as zf:
                names = [n for n in zf.namelist() if n.endswith("_adapter.safetensors")]
                if not names:
                    continue
                dest_dir = out_dir / "adapter"
                dest_dir.mkdir(parents=True, exist_ok=True)
                zf.extract(names[0], dest_dir)
                src = dest_dir / names[0]
                dst = dest_dir / "adapter.safetensors"
                if src != dst:
                    src.replace(dst)
                return dst
        except zipfile.BadZipFile:
            continue
    return None


def train_character_lora(
    session: Session,
    project_id: str,
    character_id: str,
    epochs: int = DEFAULT_EPOCHS,
    rank: int = 16,
    lr: float = 1e-4,
    quantize: int | None = None,
    max_resolution: int = 1024,
    seed: int = 42,
) -> dict:
    project = session.get(Project, project_id)
    character = session.get(Character, character_id)
    if not project or not character:
        raise RuntimeError("项目或人物不存在")
    ok, reason = trainable()
    if not ok:
        raise RuntimeError(f"无法训练 LoRA：{reason}")

    out_dir = lora_dir(project_id, character_id) / "training"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir, n = prepare_dataset(session, project, character)
    if n == 0:
        raise RuntimeError(
            "没有可用于训练的图片：请先为该人物生成设定图，或至少有引用该人物且已出图的分镜"
        )
    total_steps = max(1, int(epochs) * max(1, n))     # batch_size=1
    cfg_path = build_train_config(data_dir, out_dir, epochs=epochs, rank=rank, lr=lr,
                                  quantize=quantize, max_resolution=max_resolution, seed=seed,
                                  save_frequency=total_steps, preview_frequency=total_steps)

    import os
    proc = subprocess.run(
        [str(config.MLX_TRAIN_BIN), "--config", str(cfg_path)],
        capture_output=True, text=True, timeout=config.TRAIN_TIMEOUT_SEC,
        env={**os.environ, "HF_ENDPOINT": config.HF_ENDPOINT},
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"LoRA 训练失败：{tail}")

    adapter = find_adapter(out_dir)
    if not adapter:
        raise RuntimeError("训练结束但未找到 adapter 权重文件")
    character.lora_path = str(adapter)
    character.lora_scale = 1.0
    session.add(character)
    session.commit()
    return {
        "character_id": character_id,
        "images": n,
        "epochs": epochs,
        "rank": rank,
        "lora_path": str(adapter),
        "note": reason,
    }


def lora_for_characters(chars: list[Character]) -> tuple[list[Path], list[float]]:
    """取这些人物已训练好的 LoRA（用于生成时挂载）。"""
    paths: list[Path] = []
    scales: list[float] = []
    for c in chars:
        if c.lora_path:
            p = Path(c.lora_path)
            if not p.is_absolute():
                p = config.PROJECTS_DIR / c.lora_path
            if p.exists():
                paths.append(p)
                scales.append(float(c.lora_scale or 1.0))
    return paths, scales
