"""漫画工作台 Comic Workbench — 全局配置。"""
from __future__ import annotations

import os
from pathlib import Path

# ── 路径 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # manhua/
PROJECTS_DIR = BASE_DIR / "projects"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
DB_PATH = BASE_DIR / "comic_workbench.db"

# 测试模式保护：pytest 通过 conftest 设置 CW_TEST_DB_PATH / CW_TEST_PROJECTS_DIR 环境变量，
# 在此（模块导入早期、db.py 创建 engine 之前）就把路径钉到临时目录，
# 从根上杜绝测试写穿真实库 / 真实项目目录（2026-09-25 曾发生 7→3 项目丢失事故）。
if os.environ.get("CW_TEST_DB_PATH"):
    DB_PATH = Path(os.environ["CW_TEST_DB_PATH"])
    PROJECTS_DIR = Path(os.environ.get("CW_TEST_PROJECTS_DIR", str(BASE_DIR / "projects")))

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 图像生成 Provider ────────────────────────────────
VMLX_PYTHON_BIN = Path(
    "/Applications/vMLX.app/Contents/Resources/bundled-python/python/bin"
)
MFLUX_BIN = VMLX_PYTHON_BIN / "mflux-generate-flux2"
# FLUX.2 Klein Edit —— 参考图条件生成（角色一致性的核心，蓝图 §6）
MFLUX_EDIT_BIN = VMLX_PYTHON_BIN / "mflux-generate-flux2-edit"
# 已验证的本地模型路径（vLMX 平铺缓存，见 vlm-studio/gen_all_cardfx.sh）；
# 离线模式避免 mflux 重新拉取 black-forest-labs 远端仓库
MLX_MODEL = str(Path.home() / ".cache/huggingface/hub/toxicdog/flux2-klein-4b-8bit")
MLX_LOW_RAM = True
# FLUX.2 系列不支持负向提示词（mflux 会直接报错），负向词仅对 ComfyUI 后端生效
MLX_SUPPORTS_NEGATIVE = False

# Draft / Final 两阶段分辨率（蓝图 §4：Storyboard 512~768 快速出候选）
DRAFT_WIDTH = 640
DRAFT_HEIGHT = 960      # 2:3 竖版，适配大多数分镜格
FINAL_WIDTH = 896
FINAL_HEIGHT = 1344
DRAFT_STEPS = 6
FINAL_STEPS = 10

# 角色设定图（Character Bible）：一次出一张多视角设定图
SHEET_WIDTH = 768
SHEET_HEIGHT = 1024
SHEET_STEPS = 8
# 角色参考图最多挂几张（mflux flux2-edit 支持多图输入，太多会拖慢并串味）
MAX_REFERENCE_IMAGES = 2

# ComfyUI（本机已部署：v0.37.0 @ 127.0.0.1:8188，设备 mps）
COMFYUI_URL = "http://127.0.0.1:8188"
# SD1.5 系模型的规格与 FLUX 不同：512 系分辨率、20+ 步、cfg 7
COMFYUI_WIDTH = 512
COMFYUI_HEIGHT = 768
COMFYUI_STEPS = 20
COMFYUI_GUIDANCE = 7.0

# ── LLM 故事拆解 ─────────────────────────────────────
MLX_LM_BIN = VMLX_PYTHON_BIN / "mlx_lm.generate"
# LoRA 训练（MVP-3：权重级角色一致性）
MLX_TRAIN_BIN = VMLX_PYTHON_BIN / "mflux-train"
TRAIN_TIMEOUT_SEC = 7200

# 本地 VLM（看图评分/点评用；Qwen3.5 系列是可视觉的语言模型）
# 优先用独立 venv 里的新版 mlx-vlm（vMLX 内置的 0.5.0 与 mlx 0.32.2 在 qwen3_vl 视觉塔上
# 有 mx.repeat API 不兼容的 bug）；没有独立环境时回退到 vMLX 内置版。
_VLM_ENV_BIN = Path.home() / ".workbuddy/binaries/python/envs/vlm/bin/mlx_vlm.generate"
MLX_VLM_BIN = _VLM_ENV_BIN if _VLM_ENV_BIN.exists() else VMLX_PYTHON_BIN / "mlx_vlm.generate"
def _hf_snapshot(repo_dir: str) -> str:
    """把 HF cache 的 repo 目录解析成真实的 snapshot 目录（mlx 需要能直接读 config.json）。"""
    base = Path.home() / ".cache/huggingface" / "hub" / repo_dir / "snapshots"
    if base.exists():
        subs = sorted(x for x in base.iterdir() if x.is_dir())
        if subs:
            return str(subs[-1])
    return str(base)


LLM_VLM_MODEL = _hf_snapshot("models--mlx-community--Qwen3.5-9B-MLX-4bit")
VLM_MAX_TOKENS = 400
VLM_TIMEOUT_SEC = 600
LLM_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT_SEC = 900

HF_ENDPOINT = "https://hf-mirror.com"

# ── 排版 ─────────────────────────────────────────────
PAGE_W = 1240           # A4 @150dpi
PAGE_H = 1754
PAGE_MARGIN = 42
GUTTER = 16
BORDER_W = 3

# 中文字体（macOS 系统自带）—— 气泡/标注用，按预设切换
FONT_PRESETS: dict[str, list[str]] = {
    "heiti": [   # 黑体（默认，漫画对白最常用）
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
    "songti": [  # 宋体（正式/旁白感）
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ],
    "light": [   # 细黑（轻声/内心独白）
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ],
    "unicode": ["/Library/Fonts/Arial Unicode.ttf"],
}
FONT_PRESET_LABELS = {
    "heiti": "黑体",
    "songti": "宋体",
    "light": "细黑体",
    "unicode": "系统通用",
}
DEFAULT_FONT_PRESET = "heiti"

FONT_CANDIDATES = FONT_PRESETS[DEFAULT_FONT_PRESET]  # 兼容旧引用

# ── 默认负面提示词 ───────────────────────────────────
DEFAULT_NEGATIVE = (
    "text, watermark, signature, blurry, deformed hands, extra fingers, "
    "bad anatomy, low quality, jpeg artifacts"
)

def _s(key, label, group, desc, prompt, color=True, directive="", steps=None, cfg_hint=None):
    """steps / guidance 是该风格的**推荐采样参数**（留空用全局默认）。

    依据：细节密度高的题材（玄幻特效、机甲、末世）需要更多步数；
    平涂/写意/水墨类少步数即可，多步反而糊掉笔触。
    cfg_hint 是 **ComfyUI/SD 系**的 cfg 参考值（写意类低、细节类高）。

    注意：MLX 的 flux2-klein 是蒸馏模型，**guidance 只能是 1.0**，
    传其它值 CLI 会直接报错（"only supported for FLUX.2 base models"），
    所以风格层面只调步数，guidance/cfg 交给各引擎自己决定。
    """
    return {
        "key": key, "label": label, "group": group, "desc": desc,
        "color": color, "prompt": prompt, "directive": directive,
        "steps": steps, "cfg_hint": cfg_hint,
    }


# 常用风格库 —— 取材自腾讯动漫热门题材族：
#   玄幻修真（斗破苍穹 / 我的徒弟都是大反派 / 我的模拟长生路 / 道诡异仙）
#   古风历史（绍宋 / 铜雀锁金钗）· 都市搞笑灵异（一人之下 / 中国惊奇先生 / 情绪病）
#   热血战斗机甲（星甲魂将传 / 传武 / 铳火）· 少女恋爱（恰似寒光遇骄阳）
#   末世求生（我叫白小飞 / 地球尽头）· 神话日常（非人哉 / 有兽焉）
# 说明：每条的 prompt 必须自带「无文字」约束 —— FLUX.2 不吃负向提示词。
_NO_TEXT = (
    "artwork completely free of any lettering: no text, no characters, "
    "no sound effect text, no signage with writing, no watermark"
)
_COLOR_DIRECTIVE = (
    "FULL COLOR ILLUSTRATION — vividly colored artwork, rich saturated colors, "
    "no black-and-white, no grayscale, no monochrome, no screentone dots"
)
_BW_DIRECTIVE = (
    "BLACK AND WHITE MANGA — pure monochrome ink lineart with screentone shading, "
    "absolutely no color"
)

STYLE_LIBRARY: list[dict] = [
    # ── 国漫主风 ──
    _s("manhua_color", "国漫彩色（默认）", "国漫主风",
       "通用国漫彩色，线条干净、上色通透，适合绝大多数题材",
       "Chinese manhua style, full color illustration, clean lineart, cel shading with "
       f"soft gradients, vivid saturated colors, detailed painted background, dramatic "
       f"cinematic lighting, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 6, 7.0),
    _s("xuanhuan_epic", "玄幻修真", "国漫主风",
       "东方玄幻：灵光法阵、能量爆气、飘逸道袍，适合斗破苍穹类战斗场面",
       "Chinese xuanhuan cultivation manhua, full color, immortal cultivator in flowing "
       "robes, glowing qi energy aura, floating rune circles and formation glyphs, "
       "spirit beasts, exuberant particle effects, dramatic backlight, epic scale, "
       f"sharp clean lineart, cel shading, vivid colors, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 7.5),
    _s("xianxia_ethereal", "仙侠唯美", "国漫主风",
       "唯美仙侠：云雾仙山、飘带衣袂、花瓣飞散，适合狐妖小红娘类情感场面",
       "ethereal xianxia manhua illustration, full color, elegant flowing silk robes and "
       "ribbons, misty immortal mountain peaks, drifting petals, soft rim light, "
       "delicate lineart, airy pastel-and-jade palette, romantic atmosphere, "
       f"refined cel shading, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 6, 6.5),
    _s("dao_weird", "仙道诡秘", "国漫主风",
       "诡秘仙道：惨白符纸、扭曲暗影、诡异氛围，适合道诡异仙类惊悚题材",
       "eerie occult daoist manhua, full color, pale talisman papers, twisted shadow "
       "silhouettes, unsettling ritual atmosphere, sickly green and crimson accents, "
       "heavy ink textures, high contrast chiaroscuro, "
       f"detailed creepy background, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 8.0),

    # ── 古风 · 历史 ──
    _s("guofeng_ink", "国风水墨", "古风 · 历史",
       "水墨写意：笔触留白、墨色晕染，适合写意封面与大场面",
       "Chinese ink wash painting, generous negative space, expressive brush strokes, "
       "ink bleed and dry-brush texture, muted ink tones with a single accent color, "
       f"poetic composition, rice paper texture, {_NO_TEXT}", True,
       "FULL COLOR INK WASH ILLUSTRATION — ink tones with a restrained color accent, "
       "no gray screentone, no monochrome photo look", 5, 5.5),
    _s("ancient_history", "古风历史", "古风 · 历史",
       "历史正剧：工笔服饰、朝堂宫阙，适合绍宋类历史题材",
       "Chinese historical manhua illustration, full color, meticulously rendered "
       "traditional hanfu and armor with fine textile patterns, palace halls and "
       "banners, solemn wide composition, warm lamplight and dust motes, "
       f"precise lineart, muted yet rich palette, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 7.0),

    # ── 都市 · 现代 ──
    _s("urban_slice", "都市校园", "都市 · 现代",
       "日常向：干净平涂、明亮配色，适合校园与都市日常",
       "modern urban manhua illustration, full color, clean flat cel shading, bright "
       "cheerful palette, contemporary city or school setting, crisp lineart, "
       f"simple readable composition, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 5, 6.5),
    _s("urban_supernatural", "都市灵异", "都市 · 现代",
       "都市灵异：夜景暗调、符咒阴气，适合中国惊奇先生类题材",
       "modern supernatural thriller manhua, full color, night city alley, glowing "
       "paper talismans, cold teal shadows with eerie red highlights, fog and rain, "
       "heavy blacks, tense cinematic composition, "
       f"detailed urban decay, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 7.5),
    _s("office_romance", "都市职场甜宠", "都市 · 现代",
       "时尚清透的现代言情，适合总裁/职场恋爱题材",
       "modern romance manhua illustration, full color, polished fashion styling, "
       "glossy hair and skin rendering, bright airy interiors, soft bokeh highlights, "
       f"clean elegant lineart, fresh contemporary palette, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 5, 6.5),
    _s("shoujo_romance", "少女恋爱", "都市 · 现代",
       "粉彩柔和、闪亮大眼，适合少女漫与甜宠题材",
       "shoujo romance manhua, full color, soft pastel palette, sparkling large eyes "
       "with layered highlights, delicate detailed hair strands, flower petals and "
       f"light bokeh, gentle gradient shading, tender atmosphere, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 5, 6.0),
    _s("chibi_comedy", "Q版搞笑", "都市 · 现代",
       "Q版二头身、夸张表情、粗描边，适合吐槽与搞笑分镜",
       "chibi comedy manhua style, full color, two-head-tall super deformed characters, "
       "exaggerated comedic expressions with sweat drops and anger veins, bold thick "
       "outlines, flat vivid color blocks, simple backgrounds, "
       f"clean vector-like finish, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 4, 5.5),

    # ── 热血 · 科幻 · 末世 ──
    _s("shonen_action", "少年热血", "热血 · 科幻 · 末世",
       "强透视、速度线、冲击感，适合格斗与热血战斗",
       "shonen action manhua, full color, dynamic low-angle perspective with foreshortening, "
       "speed lines and impact bursts, sweat and debris flying, clenched fists, "
       "high contrast rim light, intense motion blur accents, "
       f"bold lineart, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 7.5),
    _s("mecha_scifi", "机甲科幻", "热血 · 科幻 · 末世",
       "金属高光、霓虹科技，适合星甲魂将传类机甲题材",
       "sci-fi mecha manhua, full color, armored mechanical suit with crisp panel lines "
       "and metallic specular highlights, glowing energy core, neon-lit futuristic city, "
       f"hard surface detail, cool blue-cyan palette with hot orange accents, {_NO_TEXT}", True,
       _COLOR_DIRECTIVE, 9, 8.0),
    _s("apocalypse", "末世求生", "热血 · 科幻 · 末世",
       "破败写实、尘埃与冷灰橙，适合我叫白小飞/地球尽头类题材",
       "post-apocalyptic manhua, full color, ruined city overgrown with debris, "
       "dusty atmosphere with volumetric light shafts, survivors in weathered gear, "
       "desaturated gray-teal palette with rusty orange accents, gritty textures, "
       f"realistic proportions, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 8, 7.5),

    # ── 日韩系 ──
    _s("manga_color", "日漫彩色", "日韩系",
       "日式彩色漫画：平涂赛璐璐、线条利落",
       "colored Japanese manga illustration, full color, clean lineart, cel shading, "
       f"vivid colors, detailed background, {_NO_TEXT}", True, _COLOR_DIRECTIVE, 6, 7.0),
    _s("webtoon", "韩式条漫", "日韩系",
       "韩式条漫：清透柔和、适合竖屏阅读",
       "Korean webtoon style, full color, clean lines, soft pastel colors, "
       f"simple backgrounds, {_NO_TEXT}", True,
       "FULL COLOR WEBTOON ILLUSTRATION — soft pastel colors, no black-and-white, no grayscale", 5, 6.5),
    _s("cinematic", "电影感插画", "日韩系",
       "电影分镜质感：强光影、宽画幅构图",
       "cinematic digital illustration, full color, dramatic lighting, detailed, "
       f"movie still composition, {_NO_TEXT}", True,
       "FULL COLOR CINEMATIC ILLUSTRATION — rich colors and lighting, no black-and-white, no grayscale", 8, 7.0),

    # ── 黑白 ──
    _s("manga_bw", "黑白漫画", "黑白",
       "传统黑白漫画：墨线 + 网点，出版级质感",
       "black and white Japanese manga, clean ink lineart, screentone shading, "
       f"high contrast, no color, {_NO_TEXT}", False, _BW_DIRECTIVE, 6, 7.0),
    _s("sumi_bw", "水墨黑白", "黑白",
       "粗犷毛笔线条、干笔飞白，适合写意与封面",
       "black and white sumi-e brush illustration, bold expressive ink strokes, "
       "dry-brush texture and ink splatter, strong contrast, generous negative space, "
       f"no color, {_NO_TEXT}", False,
       "BLACK AND WHITE SUMI-E — monochrome brush ink artwork, absolutely no color", 5, 5.5),
]

# ── 以下均由 STYLE_LIBRARY 派生，避免多处维护 ──
STYLE_PRESETS: dict[str, str] = {s["key"]: s["prompt"] for s in STYLE_LIBRARY}
STYLE_DIRECTIVES: dict[str, str] = {s["key"]: s["directive"] for s in STYLE_LIBRARY}
STYLE_LABELS: dict[str, str] = {s["key"]: s["label"] for s in STYLE_LIBRARY}
STYLE_GROUPS: list[str] = list(dict.fromkeys(s["group"] for s in STYLE_LIBRARY))
COLOR_STYLES: tuple[str, ...] = tuple(s["key"] for s in STYLE_LIBRARY if s["color"])


# 新建项目默认画风（彩色）
DEFAULT_STYLE = "manhua_color"
# 分镜/资产设定图尺寸等沿用原配置


def style_directive(style_key: str) -> str:
    return STYLE_DIRECTIVES.get(style_key, "")


def style_rec(style_key: str) -> dict:
    """风格的推荐采样参数：步数（各引擎通用）+ cfg（仅 ComfyUI/SD 系使用）。"""
    for s in STYLE_LIBRARY:
        if s["key"] == style_key:
            return {
                "steps": s.get("steps") or DRAFT_STEPS,
                "cfg": s.get("cfg_hint") or COMFYUI_GUIDANCE,
            }
    return {"steps": DRAFT_STEPS, "cfg": COMFYUI_GUIDANCE}


def styles_payload() -> list[dict]:
    """给前端的风格库（含分组/中文名/说明/是否彩色）。"""
    return [
        {"key": s["key"], "label": s["label"], "group": s["group"],
         "desc": s["desc"], "color": s["color"], "default": s["key"] == DEFAULT_STYLE,
         "steps": s.get("steps"), "cfg_hint": s.get("cfg_hint")}
        for s in STYLE_LIBRARY
    ]

CAMERA_HINTS = {
    "wide_shot": "wide establishing shot, full body visible, environment emphasized",
    "medium_shot": "medium shot, waist up, eye level",
    "close_up": "close-up shot, face fills the frame, detailed expression",
    "extreme_close_up": "extreme close-up, extreme detail",
    "low_angle": "low angle shot looking up, dramatic and imposing",
    "high_angle": "high angle shot looking down",
    "over_shoulder": "over-the-shoulder shot, back of a character in foreground",
    "birds_eye": "bird's eye view, top-down",
    "pov": "first person point of view",
}

LIGHTING_PRESETS = {
    "day": "soft natural daylight",
    "night": "cool moonlight, dark atmosphere",
    "sunset": "warm sunset glow, golden hour",
    "indoor": "soft indoor lighting",
    "dramatic": "dramatic chiaroscuro lighting, strong shadows",
}


def project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:10]}"
