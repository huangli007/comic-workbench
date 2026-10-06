"""多语言对白：本地 LLM 翻译 + 按语言渲染。

- 翻译走本机 mlx_lm（Qwen3.5-9B-MLX），整项目一次性批量翻译（一次模型加载）
- 译文存 `Panel.dialogue_translations`（JSON: {lang: [对白列表]}），原文永远保留
- 渲染时按 lang 取对白：该语言没翻译就回退中文原文

支持语言：zh（原文）/ zh-Hant 繁體 / en 英文 / ja 日文
"""
from __future__ import annotations

import json
import re
import subprocess

from sqlmodel import select

from .. import config
from ..models import Panel, Project

LANGS = {
    "zh-Hant": "繁體中文",
    "en": "English",
    "ja": "日本語",
}


def _load_dialogue(panel: Panel) -> list[dict]:
    try:
        return json.loads(panel.dialogue or "[]")
    except Exception:
        return []


def collect_dialogue(session, project_id: str) -> list[tuple[Panel, list[dict]]]:
    panels = sorted(
        session.exec(select(Panel).where(Panel.project_id == project_id)).all(),
        key=lambda p: p.index,
    )
    return [(p, [d for d in _load_dialogue(p) if (d.get("text") or "").strip()]) for p in panels]


def build_translation_prompt(items: list[tuple[str, str]], lang: str) -> str:
    """items: [(编号, 原文)] —— 一次请求翻完整个项目，编号对应回填。

    提示词开头就用强指令禁掉思考过程：Qwen3.5 会先输出一大段分析再给结论，
    而 max-tokens 截断会让 JSON 永远出不来（实测踩坑）。
    """
    lines = "\n".join(f"{k}. {t}" for k, t in items)
    return (
        "你是一个翻译接口。直接输出一个 JSON 对象，禁止输出任何思考过程、分析、解释。\n"
        f"任务：把对白翻译成{LANGS.get(lang, lang)}，口语化、简短（适合漫画文字框）、保留语气。\n"
        '输出格式：以 { 开头、以 } 结束，形如 {"translations": {"d1": "译文", ...}}。\n\n'
        f"对白：\n{lines}\n\n现在直接输出 JSON："
    )


def _last_json_object(text: str) -> dict | None:
    """取文本中最后一个配平的 {...}（模型可能先输出思考过程再给 JSON）。"""
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            c = text[i]
            if c == "}":
                depth += 1
            elif c == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:end + 1])
                    except json.JSONDecodeError:
                        break
        end = text.rfind("}", 0, end)
    return None


def _parse_translations(text: str) -> dict[str, str]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    data = _last_json_object(text)
    if not data:
        return {}
    tr = data.get("translations")
    return tr if isinstance(tr, dict) else {}


def translate_project(session, project_id: str, lang: str) -> dict:
    """翻译整个项目的对白到目标语言并落库。返回统计。"""
    project = session.get(Project, project_id)
    if not project:
        raise RuntimeError("项目不存在")
    if lang not in LANGS:
        raise RuntimeError(f"不支持的语言: {lang}（可选 {'/'.join(LANGS)}）")
    if not config.MLX_LM_BIN.exists():
        raise RuntimeError(f"本机 LLM 不可用: {config.MLX_LM_BIN}")

    pairs = collect_dialogue(session, project_id)
    items: list[tuple[str, str]] = []
    key_by_panel: dict[str, list[str]] = {}
    n = 0
    for panel, dlg in pairs:
        keys = []
        for d in dlg:
            n += 1
            k = f"d{n}"
            items.append((k, d["text"]))
            keys.append(k)
        if keys:
            key_by_panel[panel.id] = keys

    if not items:
        return {"lang": lang, "translated": 0, "panels": 0}

    prompt = build_translation_prompt(items, lang)
    import os
    proc = subprocess.run(
        [
            str(config.MLX_LM_BIN),
            "--model", config.LLM_MODEL,
            "--prompt", prompt,
            "--max-tokens", str(max(2048, n * 160)),
            "--temp", "0.3",
        ],
        capture_output=True, text=True, timeout=config.LLM_TIMEOUT_SEC,
        env={**os.environ, "HF_ENDPOINT": config.HF_ENDPOINT, "HF_HUB_OFFLINE": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"翻译失败: {proc.stderr[-600:]}")
    tr = _parse_translations(proc.stdout)

    translated = 0
    touched_panels = 0
    for panel, dlg in pairs:
        if not dlg:
            continue
        keys = key_by_panel.get(panel.id, [])
        out = []
        for i, d in enumerate(dlg):
            t = tr.get(keys[i], "").strip() if i < len(keys) else ""
            nd = {**d, "text": t or d["text"]}
            out.append(nd)
            translated += 1 if t else 0
        store = {}
        try:
            store = json.loads(panel.dialogue_translations or "{}")
        except Exception:
            store = {}
        store[lang] = out
        panel.dialogue_translations = json.dumps(store, ensure_ascii=False)
        session.add(panel)
        touched_panels += 1
    session.commit()
    return {"lang": lang, "translated": translated, "panels": touched_panels,
            "coverage": f"{translated}/{len(items)}"}
