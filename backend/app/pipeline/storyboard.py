"""故事 → Storyboard 拆解。

两级策略：
1. LLM（本机 mlx-lm + Qwen3.5-9B-MLX-4bit）输出严格 JSON；
2. 启发式 fallback：句子切分 + 镜头轮换 + 引号抽对白。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .. import config

CAMERA_ROTATION = [
    "wide_shot", "medium_shot", "close_up", "medium_shot",
    "low_angle", "over_shoulder", "high_angle", "medium_shot",
]

EMOTION_KEYWORDS = {
    "开心": "happy", "高兴": "happy", "笑": "smiling",
    "伤心": "sad", "哭": "crying", "泪": "tearful",
    "怒": "angry", "气愤": "angry", "咬牙": "angry",
    "怕": "fearful", "惊": "shocked", "震惊": "shocked",
    "冷": "cold", "平静": "calm", "沉默": "silent",
}

LLM_SYSTEM = (
    "你是专业漫画分镜师。把给定故事拆解为漫画分镜 JSON。"
    "只输出 JSON，不要任何解释文字。JSON 格式：\n"
    '{"characters": [{"name": "角色名", "appearance": "英文外貌描述(发型/眼睛/服装/体型)"}],\n'
    ' "panels": [{"characters": ["角色名"], "location": "地点(中文)", "camera": '
    '"wide_shot|medium_shot|close_up|extreme_close_up|low_angle|high_angle|over_shoulder|birds_eye|pov",\n'
    '   "action": "画面动作描述(中文)", "emotion": "calm|happy|sad|angry|shocked|fearful",\n'
    '   "lighting": "day|night|sunset|indoor|dramatic",\n'
    '   "dialogue": [{"speaker": "角色名或旁白", "text": "台词(中文)", "type": "speech|thought|shout|whisper|narration|caption|sfx"}]}]}\n'
    "要求：每格动作具体可视化；镜头有变化节奏；对白精炼；覆盖故事全部关键情节。"
)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # 剥掉 <think>...</think>（Qwen3.5 推理段）
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    # 找到最外层 { ... }
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("panels"), list):
        return data
    return None


def llm_storyboard(story: str, max_panels: int = 24) -> dict | None:
    """调用本机 mlx-lm 生成 storyboard JSON，失败返回 None。"""
    if not config.MLX_LM_BIN.exists():
        return None
    prompt = f"{LLM_SYSTEM}\n\n故事：\n{story}\n\n请输出分镜 JSON（不超过 {max_panels} 格）："
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(prompt)
            prompt_file = f.name
        proc = subprocess.run(
            [
                str(config.MLX_LM_BIN),
                "--model", config.LLM_MODEL,
                "--prompt-file", prompt_file,
                "--max-tokens", str(config.LLM_MAX_TOKENS),
                "--temp", "0.4",
            ],
            capture_output=True,
            text=True,
            timeout=config.LLM_TIMEOUT_SEC,
            env={**__import__("os").environ, "HF_ENDPOINT": config.HF_ENDPOINT},
        )
        Path(prompt_file).unlink(missing_ok=True)
        if proc.returncode != 0:
            return None
        return _extract_json(proc.stdout)
    except Exception:
        return None


def split_sentences(text: str) -> list[str]:
    """按句末标点切句，**但不切引号内部**（避免「你终于来了。」被拆散）。"""
    return [s for s, _ in split_sentences_with_offsets(text)]


def split_sentences_with_offsets(text: str) -> list[tuple[str, int]]:
    """返回 [(句子, 起始偏移)]，用于代词说话人回指。"""
    out: list[tuple[str, int]] = []
    buf = ""
    start = 0
    in_quote = False
    for i, ch in enumerate(text):
        if ch in "「“『（":
            in_quote = True
        elif ch in "」”』）":
            in_quote = False
        if ch in "。！？!?\n" and not in_quote:
            if buf.strip():
                out.append((buf.strip(), start))
            buf = ""
            start = i + 1
            continue
        if not buf:
            start = i
        buf += ch
    if buf.strip():
        out.append((buf.strip(), start))
    return out


# 说话人检测：「XX说：」「XX喊道：」……（长度量词用非贪婪，避免把修饰语并入名字）
_SPEAKER_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,4}?)(?:轻声|大声|低声|冷冷地|笑着|忽然)?(?:说|喊|道|问|答|吼|叫)[：:]?[「“]"
)
# 具名角色：2~3 字且出现在动作/说话语境中
_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,3})(?=轻声|大声|低声|说|喊|道|问|答|吼|叫|站在|看着|停下|"
    r"转身|跑来|跑来|走过|撑着|望着|沉默|震惊|点头|摇头)"
)
PRONOUNS = {"他", "她", "它", "我", "你", "他们", "她们", "我们", "你们", "对方", "两人"}

# 过滤误报：以虚词/动词开头，或含常见物件字 的候选都不是人名
_BAD_PREFIX = set("着了的在从被把和与并而就都也还很太更最一二三四五六七八九十这那有没不无")
_BAD_CHARS = set("伞门口路雨手眼头身声心地天人车校街衣书包灯夜水风雪花云山树")


def _is_name(cand: str) -> bool:
    if cand in PRONOUNS or len(cand) < 2:
        return False
    if cand[0] in _BAD_PREFIX:
        return False
    if any(c in _BAD_CHARS for c in cand):
        return False
    # 逐字均为汉字（本正则已保证），且不含叠词式虚词
    return not any(c in "的着了过" for c in cand)


def collect_characters(story: str) -> list[str]:
    """从全文抽出候选角色名（按首次出现顺序）。先取高精度的「XX说：」语境，再补动作语境。"""
    names: list[str] = []
    for m in _SPEAKER_RE.finditer(story):
        n = m.group(1)
        if _is_name(n) and n not in names:
            names.append(n)
    for m in _NAME_RE.finditer(story):
        n = m.group(1)
        if _is_name(n) and n not in names:
            names.append(n)
    return names[:8]


def _resolve_speaker(speaker: str, chunk_end: int, story: str, names: list[str]) -> str:
    """代词/空说话人 → 回指该位置之前最近出现的具名角色。"""
    if speaker and speaker not in PRONOUNS:
        return speaker
    best = ""
    best_pos = -1
    for n in names:
        pos = story.rfind(n, 0, chunk_end)
        if pos > best_pos:
            best, best_pos = n, pos
    return best or speaker


def heuristic_storyboard(story: str, max_panels: int = 24) -> dict:
    """无 LLM 时的确定性拆解。"""
    sents = split_sentences_with_offsets(story)
    if not sents:
        sents = [(story.strip() or "（空场景）", 0)]
    all_names = collect_characters(story)

    # 两句一格（保留每格在原文中的起止位置，用于代词回指）
    grouped: list[tuple[str, int, int]] = []
    buf = ""
    buf_start = 0
    for text, off in sents:
        if not buf:
            buf_start = off
        buf = f"{buf}{text}。" if buf else f"{text}。"
        if len(buf) >= 18:
            grouped.append((buf, buf_start, off + len(text)))
            buf = ""
    if buf:
        if grouped and len(buf) < 8:
            chunk, s0, _ = grouped[-1]
            grouped[-1] = (chunk + buf, s0, len(story))
        else:
            grouped.append((buf, buf_start, len(story)))
    grouped = grouped[:max_panels]

    panels = []
    for i, (chunk, chunk_start, chunk_end) in enumerate(grouped):
        # 对话抽取：同句内的「说：」→ 引号内容（说话人回指只看引号之前）
        dialogue = []
        for m in _SPEAKER_RE.finditer(chunk):
            speaker = m.group(1)
            q = re.match(r"([^」”]{1,60})[」”]", chunk[m.end():])
            if q:
                dialogue.append({
                    "speaker": _resolve_speaker(
                        speaker, chunk_start + m.end(), story, all_names
                    ),
                    "text": q.group(1).strip(),
                    "type": "shout" if ("喊" in m.group(0) or "吼" in m.group(0)) else "speech",
                })
        # 无「说」的裸引号对白
        for m in re.finditer(r"[「“]([^」”]{1,60})[」”]", chunk):
            txt = m.group(1).strip()
            if any(d["text"] == txt for d in dialogue):
                continue
            dialogue.append({"speaker": "", "text": txt, "type": "speech"})

        # 情绪关键词
        emotion = "calm"
        for kw, emo in EMOTION_KEYWORDS.items():
            if kw in chunk:
                emotion = emo
                break
        # 地点：首个「在XX」短语
        loc = ""
        m = re.search(r"在([\u4e00-\u9fa5]{2,8})(?:的|里|中|上|内|后|前)", chunk)
        if m:
            loc = m.group(1)
        # 出场角色：本格提到过的具名角色
        present = [n for n in all_names if n in chunk]
        panels.append({
            "characters": present,
            "location": loc,
            "camera": CAMERA_ROTATION[i % len(CAMERA_ROTATION)],
            "action": chunk[:60],
            "emotion": emotion,
            "lighting": "night" if ("夜" in chunk or "晚" in chunk or "雨" in chunk) else "day",
            "dialogue": dialogue[:3],
        })

    # 角色收集：全文具名角色 ∪ 对白说话人
    names: list[str] = list(all_names)
    for p in panels:
        for d in p["dialogue"]:
            if d.get("speaker") and d["speaker"] not in names:
                names.append(d["speaker"])
    characters = [{"name": n, "appearance": ""} for n in names]
    return {"characters": characters, "panels": panels}


def make_storyboard(story: str, use_llm: bool = True, max_panels: int = 24) -> tuple[dict, bool]:
    """返回 (storyboard_dict, used_llm)。"""
    if use_llm:
        result = llm_storyboard(story, max_panels)
        if result and result.get("panels"):
            return result, True
    return heuristic_storyboard(story, max_panels), False
