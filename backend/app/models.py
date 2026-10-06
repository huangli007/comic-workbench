"""SQLModel 表模型 — 漫画生产核心数据结构（蓝图 §7）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

from .config import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("proj"), primary_key=True)
    name: str
    style: str = "manhua_color"   # 默认彩色（国漫风）
    # 气泡排版设置（中文）：字体预设 + 字号缩放
    bubble_style: str = "heiti"
    font_scale: float = 1.0
    created_at: datetime = Field(default_factory=_now)


class Character(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("char"), primary_key=True)
    project_id: str = Field(index=True)
    name: str
    appearance: str = ""          # 文字设定，直接进 prompt
    reference_image: str = ""     # 相对 projects/ 的路径
    lora_path: str = ""           # 角色 LoRA 权重（权重级一致性，比参考图更硬）
    lora_scale: float = 1.0
    extra: str = "{}"             # 预留 JSON

    def ref_images(self) -> list[str]:
        try:
            return json.loads(self.extra).get("reference_images", [])
        except Exception:
            return []


class Asset(SQLModel, table=True):
    """项目资产库：场景 / 道具（人物沿用 Character，语义一致、能力对齐）。

    存在意义就是**一致性**：资产沉淀「文字设定 + 参考图」，
    之后任何分镜引用该资产时，描述自动进 prompt、参考图自动挂条件生成。
    """
    id: str = Field(default_factory=lambda: new_id("asset"), primary_key=True)
    project_id: str = Field(index=True)
    kind: str = Field(index=True)      # scene=场景 | prop=道具
    name: str                          # 中文名（界面显示、分镜引用）
    description: str = ""              # 英文设定描述（进 prompt）
    appearance: str = ""               # 兼容字段：与 description 同义（前端统一取用）
    reference_image: str = ""          # 主参考图（相对 projects/）
    extra: str = "{}"

    def ref_images(self) -> list[str]:
        try:
            return json.loads(self.extra).get("reference_images", [])
        except Exception:
            return []


ASSET_KINDS = {"scene": "场景", "prop": "道具"}


class Page(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("page"), primary_key=True)
    project_id: str = Field(index=True)
    index: int = 1
    template: str = "auto"        # auto|1x1|1x2|2x2|big2|full
    created_at: datetime = Field(default_factory=_now)


class Panel(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("panel"), primary_key=True)
    project_id: str = Field(index=True)
    page_id: str = Field(index=True)
    index: int = 1
    # 分镜字段
    characters: str = "[]"        # JSON: [char_id, ...]
    scene_id: str = ""            # 引用资产库的场景（Asset.kind=scene）
    prop_ids: str = "[]"          # JSON: [asset_id, ...] 道具
    location: str = ""            # 自由文本地点（未建资产时的兜底）
    camera: str = "medium_shot"
    action: str = ""
    emotion: str = ""
    lighting: str = "day"
    dialogue: str = "[]"          # JSON: [{speaker,text,type,x,y}, ...]
    dialogue_translations: str = "{}"  # JSON: {"en":[{...}],"ja":[...]} 多语言对白
    # 生成字段
    prompt: str = ""
    negative_prompt: str = ""
    seed: int = 0
    status: str = "draft"         # draft|generating|review|approved|final|exported
    selected_candidate_id: str = ""

    def char_ids(self) -> list[str]:
        try:
            return json.loads(self.characters)
        except Exception:
            return []

    def prop_id_list(self) -> list[str]:
        try:
            return json.loads(self.prop_ids)
        except Exception:
            return []

    def dialogues(self) -> list[dict]:
        try:
            return json.loads(self.dialogue)
        except Exception:
            return []


class Candidate(SQLModel, table=True):
    """一次生成的候选图（Draft 或 Final）。所有参数落库保证可复现。"""
    id: str = Field(default_factory=lambda: new_id("cand"), primary_key=True)
    panel_id: str = Field(index=True)
    path: str = ""                # 相对 projects/ 的路径
    prompt: str = ""
    negative_prompt: str = ""
    seed: int = 0
    provider: str = ""
    model: str = ""
    params: str = "{}"            # width/height/steps/guidance 等
    stage: str = "draft"          # draft|final|inpaint|manual
    # 质量评分（蓝图 MVP-3：AI Panel Ranking）
    score: float = 0.0            # 综合分 0~100（0 = 未评分）
    score_detail: str = "{}"      # JSON：客观各维度 + VLM 点评 + 问题标签
    scored_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class Job(SQLModel, table=True):
    """单机任务队列（SQLite，蓝图 §11：不需要 Redis）。"""
    id: str = Field(default_factory=lambda: new_id("job"), primary_key=True)
    type: str = ""                # storyboard|panel_draft|panel_final|page_render|export
    payload: str = "{}"
    status: str = "pending"       # pending|running|done|failed
    error: str = ""
    result: str = "{}"
    progress: str = "{}"          # {"current":1,"total":3,"label":"..."}
    project_id: str = ""
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
