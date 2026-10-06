"""候选图质量评分（蓝图 MVP-3：AI Panel Ranking）。

两级评分，互相独立、可组合：

1. **客观指标**（毫秒级、无模型）：清晰度 / 对比度 / 曝光 / 色彩度 / 信息量
   —— 用来快速筛掉糊图、死白死黑、纯色空图、莫名变灰阶的图
2. **本地 VLM 点评**（可选、慢）：Qwen3.5-9B-MLX 看图打分，能识别"人物畸形 / 画面里有文字 /
   与分镜描述不符"这类客观指标抓不到的问题

注意：客观指标里的**色彩度**要按项目画风判断——彩色项目要求高、黑白项目要求低，
否则黑白漫画会被误判为"没有颜色、低分"。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .. import config


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def objective_metrics(path: Path | str) -> dict:
    """返回原始度量值（不做评分归一化）。"""
    img = Image.open(path).convert("RGB")
    gray = img.convert("L")

    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = float(ImageStat.Stat(edges).stddev[0])
    contrast = float(ImageStat.Stat(gray).stddev[0])
    brightness = float(ImageStat.Stat(gray).mean[0])
    r, g, b = ImageStat.Stat(img).mean
    colorfulness = float(max(r, g, b) - min(r, g, b))

    small = img.resize((64, 64), Image.LANCZOS)
    # 唯一色数量（getcolors 超过上限返回 None = 信息量很大）
    colors = small.getcolors(maxcolors=8192)
    unique_colors = len(colors) if colors else 8192
    return {
        "sharpness": sharpness,
        "contrast": contrast,
        "brightness": brightness,
        "colorfulness": colorfulness,
        "unique_colors": unique_colors,
        "size": list(img.size),
    }


def score_objective(path: Path | str, expect_color: bool = True) -> dict:
    """客观评分：总分 0~100 + 各维度细分 + 命中的问题标签。"""
    m = objective_metrics(path)
    issues: list[str] = []

    # 清晰度：漫画线稿边缘强度天然较高；< 4 基本是糊图
    sharp = _clamp(m["sharpness"] / 8.0, 0, 10)
    if m["sharpness"] < 4:
        issues.append("画面偏糊/细节缺失")

    # 对比度：过低是灰蒙蒙，过高可能死黑死白
    contrast = _clamp(m["contrast"] / 8.0, 0, 10)
    if m["contrast"] < 12:
        issues.append("对比度偏低（灰蒙）")

    # 曝光：40~210 视为正常曝光区间
    b = m["brightness"]
    if b < 25:
        exposure, issues = 2.0, issues + ["整体过暗"]
    elif b < 40:
        exposure = 6.0
    elif b > 232:
        exposure, issues = 2.0, issues + ["整体过曝/死白"]
    elif b > 210:
        exposure = 7.0
    else:
        exposure = 10.0

    # 色彩度：按画风期望判断
    cf = m["colorfulness"]
    if expect_color:
        color = _clamp(cf / 6.0, 0, 10)
        if cf < 2.5:
            issues.append("该彩色画风但画面几乎无色")
    else:
        color = 10.0 if cf < 2.5 else 4.0     # 黑白画风：越接近灰阶越好
        if cf >= 2.5:
            issues.append("该黑白画风但画面有色彩")

    # 信息量：纯色/近纯色空图
    uniq = m["unique_colors"]
    info = _clamp(uniq / 900.0, 0, 10)
    if uniq < 200:
        issues.append("画面信息量过少（疑似空图）")

    weights = {"sharp": 0.30, "contrast": 0.22, "exposure": 0.18, "color": 0.15, "info": 0.15}
    total = (
        sharp * weights["sharp"] + contrast * weights["contrast"]
        + exposure * weights["exposure"] + color * weights["color"]
        + info * weights["info"]
    ) * 10

    return {
        "score": round(total, 1),
        "detail": {
            "sharpness": round(sharp, 2), "contrast": round(contrast, 2),
            "exposure": round(exposure, 2), "color": round(color, 2), "info": round(info, 2),
            "metrics": m,
        },
        "issues": issues,
        "source": "objective",
    }


# ── 本地 VLM 点评（可选） ────────────────────────────

VLM_SYSTEM = (
    "你是资深漫画编辑，负责从多个候选中挑出最可用的分镜画面。"
    "只输出 JSON，不要解释文字。"
)
VLM_PROMPT = (
    "评估这张漫画分镜候选图，输出严格 JSON：\n"
    '{"score": 0到10的整数, "issues": ["问题标签，如 人物畸形/画面有文字/与描述不符/构图差/手部崩坏"], '
    '"comment": "一句中文点评"}\n'
    "评分维度：① 与下面这段分镜描述是否契合 ② 构图与镜头感 ③ 人物比例与手部是否正常 "
    "④ 画面里是否残留文字/水印 ⑤ 作为漫画分镜的可用性。\n"
    "分镜描述：{desc}"
)


def vlm_available() -> bool:
    return config.MLX_VLM_BIN.exists() and Path(config.LLM_VLM_MODEL).exists()


def vlm_review(image_path: Path | str, description: str = "") -> dict:
    """用本地 VLM（Qwen3.5-MLX）看图打分。失败抛 RuntimeError。"""
    if not vlm_available():
        raise RuntimeError(f"VLM 不可用: {config.MLX_VLM_BIN} / {config.LLM_VLM_MODEL}")
    prompt = VLM_PROMPT.replace("{desc}", (description or "（未提供描述）")[:400])
    try:
        proc = subprocess.run(
            [
                str(config.MLX_VLM_BIN),
                "--model", config.LLM_VLM_MODEL,
                "--image", str(image_path),
                "--prompt", prompt,
                "--system", VLM_SYSTEM,
                "--max-tokens", str(config.VLM_MAX_TOKENS),
                "--temperature", "0.2",
            ],
            capture_output=True, text=True, timeout=config.VLM_TIMEOUT_SEC,
            env={**__import__("os").environ, "HF_ENDPOINT": config.HF_ENDPOINT,
                 "HF_HUB_OFFLINE": "1"},
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"VLM 评分超时（>{config.VLM_TIMEOUT_SEC}s）") from e
    if proc.returncode != 0:
        raise RuntimeError(f"VLM 调用失败: {proc.stderr[-800:]}")

    text = proc.stdout
    data = _extract_json(text)
    if data is None:
        return {"score": None, "issues": [], "comment": text.strip()[-200:],
                "source": "vlm", "parse_error": True}
    score = data.get("score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = None
    return {
        "score": score,
        "issues": [str(i) for i in (data.get("issues") or [])][:6],
        "comment": str(data.get("comment", ""))[:200],
        "source": "vlm",
    }


def _extract_json(text: str) -> dict | None:
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def combine(objective: dict, vlm: dict | None) -> dict:
    """合并客观分与 VLM 分：各占 50%，VLM 缺失时只用客观分。"""
    o = objective.get("score")
    v = (vlm or {}).get("score")
    if v is None:
        total = float(o or 0)
    else:
        total = float(o or 0) * 0.5 + float(v) * 10 * 0.5
    issues = list(objective.get("issues") or []) + list((vlm or {}).get("issues") or [])
    return {
        "score": round(total, 1),
        "objective": objective,
        "vlm": vlm,
        "issues": issues,
        "comment": (vlm or {}).get("comment", ""),
    }
