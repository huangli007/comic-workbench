# 漫画工作台 Comic Workbench

**本地优先的漫画生产系统** —— 故事拆解 → 分镜 → 出图 → 一致性资产 → 中文排版 → 多语言导出，全流程可复现、每个阶段都能人工接管。

> 核心原则：**生成引擎（MLX/ComfyUI）只是可替换的部件，本系统是漫画生产管理系统。**
> 数据（Project / Character / Scene / Storyboard / Panel / Job）与模型解耦，模型可替换、生成可复现。

**环境要求**：Python 3.11+ 即可跑起界面与全部非生成流程；出图后端按平台选——
Apple Silicon 走 MLX（mflux + FLUX.2 Klein 4B，16GB 内存实测可用）、任意平台走 ComfyUI、无 GPU 用 `mock`。
快速开始见 [INSTALL.md](INSTALL.md) 或直接 `./scripts/package.sh && tar -xzf dist/*.tar.gz`。

---

## 一、它已经能做什么（MVP-1 全链路）
```
故事文本
  ↓  ① 拆解（本地 LLM 或启发式）
Storyboard JSON（角色 + 分镜格：镜头/动作/情绪/光照/对白）
  ↓  ② 生成候选图（每个分镜格 N 张，Draft 640×960 快速）
候选图 → 人工选择
  ↓  ③ 最终图（可选高清重绘 896×1344）
  ↓  ④ 自动排版（1x1 / 1x2 / 2x2 / 1大2小 / 满版）+ 中文对白气泡
页面 PNG
  ↓  ⑤ 导出 PNG / PDF / CBZ
```

已实现能力：

| 层 | 实现 |
|---|---|
| 画风 | **常用风格库 19 种，分 6 组**（取材腾讯动漫热门题材族）：国漫主风（国漫彩色/玄幻修真/仙侠唯美/仙道诡秘）、古风·历史（国风水墨/古风历史）、都市·现代（都市校园/都市灵异/都市职场甜宠/少女恋爱/Q版搞笑）、热血·科幻·末世（少年热血/机甲科幻/末世求生）、日韩系（日漫彩色/韩式条漫/电影感插画）、黑白（黑白漫画/水墨黑白）。默认**国漫彩色**，项目页可随时切换，设定图同步跟画风 |

### 常用风格库（取材腾讯动漫热门题材族）

| 分组 | 风格 | 说明 | 对标的常见题材 |
|---|---|---|---|
| 国漫主风 | 国漫彩色（默认） | 通用国漫彩色，线条干净、上色通透 | 通用 |
| 国漫主风 | 玄幻修真 | 灵光法阵、能量爆气、飘逸道袍 | 斗破苍穹 / 我的徒弟都是大反派 / 我的模拟长生路 |
| 国漫主风 | 仙侠唯美 | 云雾仙山、飘带衣袂、花瓣飞散 | 狐妖小红娘 / 魔道祖师类 |
| 国漫主风 | 仙道诡秘 | 惨白符纸、扭曲暗影、诡异氛围 | 道诡异仙 |
| 古风 · 历史 | 国风水墨 | 水墨写意、笔触留白 | 写意封面 / 大场面 |
| 古风 · 历史 | 古风历史 | 工笔服饰、朝堂宫阙 | 绍宋 / 铜雀锁金钗 |
| 都市 · 现代 | 都市校园 | 干净平涂、明亮配色 | 一人之下（日常） |
| 都市 · 现代 | 都市灵异 | 夜景暗调、符咒阴气 | 中国惊奇先生 / 情绪病 / 第七名被害人 |
| 都市 · 现代 | 都市职场甜宠 | 时尚清透的现代言情 | 总裁 / 职场恋爱题材 |
| 都市 · 现代 | 少女恋爱 | 粉彩柔和、闪亮大眼 | 恰似寒光遇骄阳 / 请与我同眠 |
| 都市 · 现代 | Q版搞笑 | 二头身、夸张表情、粗描边 | 我家大师兄脑子有坑 / 非人哉 |
| 热血 · 科幻 · 末世 | 少年热血 | 强透视、速度线、冲击感 | 传武 / 拳王归来 |
| 热血 · 科幻 · 末世 | 机甲科幻 | 金属高光、霓虹科技 | 星甲魂将传 |
| 热血 · 科幻 · 末世 | 末世求生 | 破败写实、尘埃冷灰橙 | 我叫白小飞 / 地球尽头 |
| 日韩系 | 日漫彩色 / 韩式条漫 / 电影感插画 | 日式赛璐璐 / 竖屏条漫 / 电影分镜 | 日漫引进、条漫 |
| 黑白 | 黑白漫画 / 水墨黑白 | 墨线网点 / 毛笔写意 | 出版级黑白 |

- 风格库由后端 `config.STYLE_LIBRARY` **单一数据源**派生（`STYLE_PRESETS` / `STYLE_DIRECTIVES` / `STYLE_LABELS` / 分组），
  前端只消费 `GET /api/styles`，不存在两处维护
- 每种风格都自带**风格硬指令**与**无文字约束**，有测试守住（缺一个就报错）
- 横向对比样张：`docs/style_gallery.png`（同一场景 × 9 种风格），
  生成脚本 `scripts/gen_style_gallery.py`

### 画风是怎么被"钉死"的（踩坑记录，别删）

提示词里只写一次 "full color" 不够用。参考图条件生成时前面会拼很长的一致性守护语句，
风格词被挤到中段后**模型会退化成黑白网点**（实测：色差 0.3，纯灰阶）。三重保障：

1. **首尾夹击**：`config.STYLE_DIRECTIVES` + `prompt.apply_style_directive()`，
   同一段风格硬指令放在提示词**最前**和**最后**（`COLOR REQUIREMENT: …`）
2. **对白剥离**：`prompt.sanitize_action()` 把动作里的引号对白与「她说/他喊」剥掉
   （蓝图 §14：对白不进图像模型）。带着 `「你终于来了。」` 时模型会理解成"要排对白的漫画页"
   并自行画上对话框，同时退向黑白
3. **参考图按画风隔离**：设定图文件名 `sheet_<画风>_<种子>_<运行标识>.png`；
   `reference_images(..., style=当前画风)` **只挂当前画风**的图，该画风没有图就不挂（宁可只靠文字描述）

效果（实测色差 = 通道均值极差，越大越彩）：纯文生图 23.9、参考图条件 37.4、成页 31.1；
修复前为 0.3（灰阶）。
| 图像后端 | **MLX（mflux + FLUX.2 Klein 4B 8bit）**：`mlx` 文生图 + **`mlx-edit` 参考图条件生成**；**ComfyUI（已接入，本机 v0.37.0 @8188）**：API 格式工作流，SD1.5 512×768/20 步 ≈ 43s，**支持负向提示词**；`mock` 供测试。前端可逐项目切换引擎与 checkpoint |
| 角色一致性 | **项目资产库（人物 / 场景 / 道具）**：每项资产沉淀「中文名 + 英文设定 + 设定图」，分镜引用后自动把设定写进提示词、把设定图作为参考图 → 跨格一致；重跑分镜不会冲掉已生成的角色/场景资产 |
| LLM 拆解 | 本机 `mlx_lm` + Qwen3.5-9B-MLX-4bit；失败自动回落启发式拆解（引号感知切句 + 代词回指 + 角色名抽取） |
| 任务系统 | SQLite + 单 Worker 线程，串行执行（适配 16GB 统一内存，不并发大模型） |
| 数据结构 | SQLModel/SQLite：Project / Character / Page / Panel / Candidate / Job |
| 对白 | **不进图像模型**，由 Pillow 后期合成**中文**文字框（对白/内心独白/喊叫/低语/旁白/说明框/拟声词），带**标点禁则**（避头尾） |
| 排版 | Layout Engine 按面板数与重要性选模板，cover-crop 填格，统一边框；**字体预设（黑体/宋体/细黑）+ 字号缩放**按项目保存 |
| 界面 | **全中文界面**（`frontend/src/labels.js` 统一枚举中文映射） |
| 可复现 | prompt / negative / seed / provider / model / steps / 尺寸 / **使用的参考图** 全部落库 |

### 候选质量评分与自动选最佳（MVP-3：AI Panel Ranking）

批量出图之后最缺的是"从一堆候选里挑出好的"，为此做了两级评分：

```text
① 客观指标（毫秒级、无模型）
   清晰度（边缘强度）/ 对比度 / 曝光区间 / 色彩度（按画风期望判断）/ 信息量（纯色空图检测）
   → 0~100 分 + 问题标签（画面偏糊 / 对比度偏低 / 整体过暗 / 该彩色画风但画面几乎无色 …）

② 本地 VLM 点评（可选，每张约 30-60 秒）
   mlx-vlm 0.7.3 + 本机 Qwen3.5-9B-MLX-4bit 看图打分
   → 能识别"与分镜描述不符 / 人物比例失调 / 手部崩坏 / 画面残留文字"这类客观指标抓不到的问题

综合 = 客观 × 50% + VLM × 50%（VLM 缺失时只用客观分）
```

- 评分只写 `Candidate.score / score_detail`，不动图片与选择状态，可随时重跑
- 排口：`POST /api/panels/{id}/score`、`POST /api/projects/{id}/score-candidates`（批量）、
  `GET /api/panels/{id}/ranking`（按分排序）、`POST /api/panels/{id}/auto-select-best`、
  `POST /api/projects/{id}/auto-select-best`
- 前端：候选缩略图带**分数徽标**（≥75 绿 / ≥55 黄 / 其余红）与「最佳」标记，
  悬停显示 VLM 中文点评与问题清单；工具条「批量评分 / 整项目自动选最佳」
- VLM 环境说明：vMLX 内置的 mlx-vlm 0.5.0 与 mlx 0.32.2 在 qwen3_vl 视觉塔上有
  `mx.repeat` API 不兼容的 bug；已用独立 venv 装 **mlx-vlm 0.7.3** 解决
  （`~/.workbuddy/binaries/python/envs/vlm`，检测到就用它，否则回退内置版并明确报错）

实测：6 张候选 VLM 点评 95 秒；对 ComfyUI/SD1.5 的空洞图准确给出 2 分
"内容空洞、线条粗糙、不具备叙事功能"；对成页图指出"与描述不符、人物比例失调"。

### 效率与质量优化

**① 风格关联推荐参数**：每种风格带推荐**步数**（写意/平涂类 4-6 步，细节密的玄幻/机甲/末世 8-9 步），
未手动指定时自动采用；**cfg/guidance 按引擎分开**——ComfyUI/SD 系用风格的 `cfg_hint`（5.5~8.0），
MLX 的 flux2-klein 是蒸馏模型**只接受 `--guidance 1.0`**（传其它值 CLI 直接报错，已在 provider 层兜底）。

**② 批量出图**（分镜页工具条）：

```text
范围：整个项目 / 指定页   ×   每个格子出几张   ☑ 仅未出图的格   ☑ 带参考图
→ 批量生成候选（串行执行，进度在任务队列可见）
```

`only_missing=true` 时已有候选的格子会被跳过，避免重复烧算力（第二次点会提示"没有需要生成的格子"）。

**③ 资产一改，全场同步**：资产库卡片上的「重跑引用分镜」——
改完人物/场景/道具的设定图或描述后，一键重跑所有引用它的分镜
（场景走 `scene_id`、道具走 `prop_ids`、人物走 `characters`；没有分镜引用时给出明确提示）。

**④ 导出文件下载**：排版/导出页列出所有已导出文件（格式 / 文件名 / 大小 / 下载链接），
接口 `GET /api/projects/{pid}/exports`；图片同样可通过 `/files/...` 直接访问。

### 一键升级画风 / 分镜编辑 / 多语言 / 统计

**一键升级画风**（老黑白项目升级彩色的正确姿势）：

```text
项目页顶部把画风切成彩色 → 弹窗「立即重新生成」
→ 自动编排：重生成全部人物/场景/道具设定图 → 带参考图重跑全部分镜 → 渲染全部页面
（后端 POST /api/projects/{pid}/upgrade-style，进度在任务队列可见）
```

**分镜编辑**：分镜卡支持「复制」（含设定与选中候选图，自动插入并重排序）、「删除」
（候选记录一并删、图片文件保留），工具条「+ 新增空格」；同页序号自动重排。

**多语言对白与导出**：排版/导出页可选对白语言（中文原文 / 繁體中文 / English / 日本語），
「翻译对白」用本机 LLM（Qwen3.5-MLX）整项目一次翻译，译文存 `Panel.dialogue_translations`
（原文永不覆盖）；渲染与导出按语言取译文，缺失的格自动回退中文；
导出目录/文件名带语言后缀（如 `exports/pdf_en/雨夜_comic_en.pdf`）。

**首页统计卡**：项目 / 分镜格 / 人物与资产 / 已导出文件 / 队列任务
（`GET /api/stats`）。

### 数据库自救：从磁盘恢复项目

工作台的所有生成资产都在 `projects/<项目id>/` 磁盘目录里，数据库只是索引。
万一 DB 丢失/重置，运行恢复脚本即可从磁盘重建（幂等，已存在的按 id 跳过）：

```bash
python scripts/recover_projects.py [--dry-run]
```

恢复内容：项目（名字取自导出 PDF / 画风从设定图文件名推断）、页面（按 `pages/page_XXX.png`）、
分镜与候选（`panels/<id>/{draft,inpaint,manual}/*.png`）、人物与资产设定图。

### 大模型权重的国内高速下载（重要环境知识）

`hf-mirror.com` 对某些大 repo 会限速到 **~87 KB/s**（FLUX.2-klein-4B 的 15GB 要 50 小时，
完全不可用）；而 **ModelScope 同 repo 镜像有 ~5 MB/s**（15GB 约 50 分钟）。

但 mflux / huggingface_hub 只认标准 HF 缓存布局，所以用混合下载器：

```bash
# 文件清单与 sha256（blob 名）从 HF API 取（轻量秒回），文件内容从 ModelScope 下
python scripts/ms_hf_download.py black-forest-labs/FLUX.2-klein-4B \
  --files "transformer/diffusion_pytorch_model.safetensors,\
text_encoder/model-00001-of-00002.safetensors,\
text_encoder/model-00002-of-00002.safetensors,\
vae/diffusion_pytorch_model.safetensors" --jobs 3
```

脚本会构造 `blobs/<sha256>` + `snapshots/<commit>/<file>` 符号链接 + `refs/main`，
huggingface_hub 的完整性检查会认为缓存完整（`flux-2-klein-4b.safetensors` 那种 ComfyUI
单文件版不必下，省 7.4GB）。

### 角色 LoRA（MVP-3，权重级一致性）

一致性三档：**文字描述 < 参考图条件生成 < LoRA 权重**。资产库人物卡可「训练 LoRA」：

```text
数据集自动准备（当前画风的设定图 + 引用该人物且已出图的分镜，平铺图片 + 同名 .txt 提示词）
   → mflux-train 训练（rank 16 / 30 epoch / AdamW 1e-4，默认只训 attention 投影层）
   → adapter 权重登记到 Character.lora_path
   → 之后该人物的分镜生成自动挂载 --lora-paths / --lora-scales
```

- 接口：`POST /api/characters/{cid}/train-lora {epochs, rank, lr}`
- ⚠️ **本机前置条件**：`mflux-train` 需要完整精度的 `black-forest-labs/FLUX.2-klein-4B`（约 16GB）。
  本机当前只有 8bit 量化版（toxicdog），用它训练会落到 Flux1 分支并报
  "Flux1 training is no longer supported."。下载完整权重后即可一键训练。
- 训练配置模板：`workflows/train_character_lora.json`（已通过 `mflux-train --dry-run` 验证）
- **内存开关**：`quantize: 8`（QLoRA 式量化训练，16GB 机器必开）、`max_resolution`（样本长边上限）
- **样本不足时自动跨项目收集同名角色的图**（小项目也能凑够训练集）
- 生成侧用 mflux 新写法 `--lora PATH [SCALE]`（可重复，比废弃的
  `--lora-paths/--lora-scales` 语义清晰且不会配对错位）
- 效果对比：`python scripts/lora_compare.py --project <pid> --character <cid> --prompt "..."`
- **已在真机跑通**（2026-10-06）：8 张样本 / 3 epoch / rank 16 / 512px / quantize 8，
  24 步 29 分钟产出 66.8MB adapter；同 prompt 同 seed 对比显示面部特征明显向训练目标收敛
  （`docs/lora_compare.png`）
- mflux 会把 adapter 打包进 `checkpoints/<step>_checkpoint.zip`，
  `find_adapter()` 会自动提取（踩过）

#### 16GB M4 上的 LoRA 实用参数（实测）

| 参数 | 建议 | 说明 |
|---|---|---|
| `max_resolution` | 512 | 768 会在 swap 上拖死；512 每步 5-7 秒 |
| `quantize` | 8 | QLoRA 式量化训练，必开 |
| 样本数 | 4-8 张 | 设定图 + 引用该角色的分镜（自动跨项目收集同名角色） |
| `epochs` | 10-15 | 24 步已能看出特征收敛；30 epoch 在 16GB 上要数小时 |
| `save/preview_frequency` | = 总步数 | **这两个参数按「步」计**，填小值会导致每步存 185MB checkpoint + 完整推理预览，既慢又 OOM |

⚠️ **真要跑大规模正式训练，建议放到 RTX 3070 桌面机**（8GB 显存 + 32GB 内存，
不存在 TE 7.5GB 常驻把内存吃满的问题）。Mac 这条链路用来验证与出小样是够的。

### 项目资产库：一致性资产（人物 / 场景 / 道具）

漫画难的不是生成一张好图，而是**同一个角色、同一个场景、同一件道具跨格不乱**。
所以把「一致性」做成项目级资产库，而不是靠每次手写提示词：

| 资产类型 | 能力 | 设定图形态 |
|---|---|---|
| **人物** | 中文名 + 英文外貌 + 设定图 | 三视图 / 正面全身 / 半身像 |
| **场景** | 中文名 + 英文环境描述 + 设定图 | 全景 / 室内 / 外景（无人物） |
| **道具** | 中文名 + 英文道具描述 + 设定图 | 三视图 / 正面 / 细节 |

工作方式：

```text
资产库（描述 + 设定图）
      ↓  分镜卡片里勾选「场景 / 道具」，人物由故事拆解自动挂靠
提示词注入：CHARACTERS + SCENE + PROPS 三段（描述进 prompt）
      ↓
参考图挂载：人物 → 场景 → 道具（按优先级，最多 MAX_REFERENCE_IMAGES 张）
      ↓
flux2-edit 条件生成 → 跨格一致的画面
```

- **故事拆解时自动建场景卡**：故事里的「在旧图书馆前」会自动生成场景资产「旧图书馆」，
  之后每格复用同一个场景 ID；已被人工补过设定/设定图的场景，重跑分镜**不会被清掉**
- **删除资产会自动摘掉分镜上的引用**（分镜本身保留），不留悬挂外键
- 资产卡片显示**被引用次数**，一眼看出哪个资产还没用起来
- **设定图按画风分文件**：`sheet_<画风>_<种子>.png`；切换画风后挂参考图会**优先取当前画风**的设定图，
  不会把旧黑白设定图混进彩色生成
- 参考图上限 `config.MAX_REFERENCE_IMAGES`（默认 2，超出部分只进文字描述不影响一致性语义）

### 角色一致性怎么用（蓝图 §6）

1. 在「资产库」填外貌/环境/道具描述（英文）→ 点「生成设定图」；
2. 分镜卡片里选「场景 / 道具」（人物由拆解自动挂），勾选「带角色参考图」→ 系统自动用 `mlx-edit`；
3. 「高清重绘」默认**始终**挂参考图（Final 档追求一致性，草图档追求速度）；
4. 重新拆解分镜时，已生成设定图的资产会被**按名字合并保留**，不会丢资产。

> 两阶段策略（蓝图原则）：先 Reference + Prompt 跑通一致性，**不要第一天就训 LoRA**。

### 局部重绘与人工精修（蓝图 §17）

**选区重绘**（无需 mask 模型）：

```
在分镜图上拖拽框选 → 填「选区内画什么」→ 局部重绘
   ↓ 裁剪选区(带上下文边距) → flux2-edit 条件重绘 → 羽化回贴原图
新候选（stage=inpaint）→ 与原图并排对比后再决定是否采用
```

原图不受影响，重绘结果永远作为一个新候选进入候选池；候选记录里落库选区坐标、上下文比例、羽化、使用的参考图。

**外部编辑器往返**（Krita / Photoshop / 任意图像编辑器）：

```bash
# 面板上「外部编辑」= 导出工作图并在编辑器中打开（未装 Krita 则 Finder 定位）
projects/<proj>/panels/<panel>/edit/work.png
# 改完点「回填」→ 作为 stage=manual 的新候选进入候选池
```

- 已装 Krita/Photoshop/Pixelmator/GIMP 时自动 `open -a` 打开，否则 Finder 定位工作图
- 回填用 md5 校验：文件没改过会明确拒绝，避免产生无意义的重复候选
- 全程不依赖 Krita AI Diffusion 插件（蓝图里的 ComfyUI 通道仍保留）

---

## 二、运行

```bash
# 生产模式（FastAPI 直接托管前端构建产物）
./scripts/run.sh            # → http://127.0.0.1:8770

# 开发模式（后端 8000 + Vite 5173 热更新）
./scripts/dev.sh

# 端到端自检（建项目→拆解→真实出图→选择→排版→导出三种格式）
python scripts/e2e_demo.py

# MVP-2 角色一致性自检（设定图→参考图条件出图→成页导出）
python scripts/e2e_consistency.py

# 资产库自检（人物 + 场景 + 道具 → 提示词注入 + 参考图挂载 → 成页导出）
python scripts/e2e_assets.py

# 彩色画风自检（切画风 → 重新生成参考图 → 带参考图出图 → 成页）
python scripts/e2e_color.py ["项目名"]   # 不带项目名则新建彩色项目跑全流程
```

> 画风说明：新建项目默认 **彩色（国漫风 `manhua_color`）**；
> 老项目在项目页顶部把「画风」切成彩色即可，之后重新生成的分镜与设定图都是彩色。

> 端口说明：本机 **8000 已被 Novel Studio 占用**，漫画工作台默认 **8770**。
> 前端深链接：`/#/project/<id>?tab=panels|chars|jobs|export`

界面截图见 `docs/`：
- `ui_home_cn.png`（项目列表·全中文）、`ui_storyboard.png`（故事导入）、`ui_export_cn.png`（排版/导出·文字框字体设置）
- `ui_panels.png` / `ui_panels_inpaint.png`（分镜格工作区 + 选区重绘控件）、`ui_assets.png`（资产库）、`ui_color_style.png`（画风切换）
- **彩色产出**：`sample_color_page.png`（彩色成页）、`sample_color_character_sheet.png`（彩色人物三视图）、
  `sample_color_scene_sheet.png`（彩色场景设定图）、`sample_color_prop_sheet.png`（彩色道具三视图）
- 一致性实测：`sample_page_consistency.png`、`sample_asset_page.png`、`sample_inpaint_lamp.png`

依赖（已在本机装好）：
- Python venv：`/Users/craxus/.workbuddy/binaries/python/envs/default`（fastapi / uvicorn / sqlmodel / pillow / pytest / httpx）
- Node / npm：`/Users/craxus/.workbuddy/binaries/node/versions/22.22.2-3/bin`
- 图像引擎：vMLX Studio 内置 mflux：`/Applications/vMLX.app/Contents/Resources/bundled-python/python/bin/mflux-generate-flux2`
  模型：`~/.cache/huggingface/hub/toxicdog/flux2-klein-4b-8bit`（8.0G）

测试：

```bash
cd backend && /Users/craxus/.workbuddy/binaries/python/envs/default/bin/python -m pytest -q
# 注意：本机沙箱会拦 pytest 的 tmp 目录创建，需前置：
# env -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR -u CODEBUDDY_TOOL_CALL_ID \
#     CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
#     CODEBUDDY_SAFE_DELETE_ENABLED=0 ... -m pytest -q
```

---

## 三、目录

```
manhua/
├── backend/
│   ├── app/
│   │   ├── config.py            # 路径 / 模型 / 分辨率 / 风格 / 字体
│   │   ├── db.py  models.py     # SQLite + SQLModel 表（含资产库 Asset）
│   │   ├── api/                 # projects.py panels.py assets.py
│   │   ├── providers/           # base.py mlx_provider.py comfyui.py mock.py
│   │   └── pipeline/
│   │       ├── prompt.py        # STYLE+CHARACTERS+ACTION+SCENE+PROPS+CAMERA+…
│   │       ├── character.py     # 人物一致性：设定图 + 参考图条件
│   │       ├── asset.py         # 资产库：场景/道具设定图 + 提示词段 + 参考图
│   │       ├── storyboard.py    # 故事 → 分镜 JSON（LLM / 启发式）
│   │       ├── worker.py        # SQLite Job 队列 + 串行 Worker
│   │       ├── inpaint.py       # 局部重绘（裁剪—条件重绘—羽化回贴）
│   │       ├── external.py      # 外部编辑器往返
│   │       ├── bubbles.py       # 中文文字框（字体预设 + 标点禁则）
│   │       ├── layout.py        # 排版引擎
│   │       └── export.py        # PNG / PDF / CBZ
│   └── tests/                   # 40 项测试全绿
├── frontend/                    # Vue3 + Vite + Element Plus 工作台
│   └── src/views/               # HomeView（项目）/ ProjectView（分镜·角色·队列·导出）
├── workflows/panel.json         # ComfyUI API 工作流模板（放入 ckpt 即可用）
├── projects/                    # 每个项目的全部资产（角色/分镜图/页面/导出）
└── scripts/                     # run.sh / dev.sh
```

---

## 四、切换图像后端

**方式一：界面切换（推荐）** —— 项目页右上角「生成引擎」选择
`自动 / 本机 MLX 文生图 / 本机 MLX 参考图 / ComfyUI / 测试用假引擎`；
选 ComfyUI 时右侧会出现 **模型下拉**（自动读取 ComfyUI 的 checkpoint 列表）。

**方式二：改配置** —— `backend/app/config.py`：

```python
MLX_MODEL = "/绝对路径/或HF仓库名"     # 换其它 MLX 模型
MFLUX_BIN = Path(".../mflux-generate-flux2")
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_WIDTH, COMFYUI_HEIGHT, COMFYUI_STEPS, COMFYUI_GUIDANCE = 512, 768, 20, 7.0
```

**ComfyUI 工作流**：`workflows/panel.json`（API 格式）。
换 checkpoint 只需改 `ckpt_name`，或直接用界面下拉。
⚠️ 模板里不要出现 `_meta` 之类的非节点键，否则 ComfyUI 会报 `missing_node_type`
（provider 已做容错跳过，但保持模板干净最稳）。详见 `workflows/README.md`。

### 两个引擎的定位

| | 本机 MLX（FLUX.2 Klein 4B） | ComfyUI（当前 SD1.5） |
|---|---|---|
| 速度 | 640×960/6 步 ≈ 40s | 512×768/20 步 ≈ 36s |
| 画质 | **明显更好**（漫画风、线条干净） | 取决于 checkpoint（SD1.5 base 偏弱） |
| 负向提示词 | 不支持（写进正向） | **支持** |
| 参考图一致性 | 支持（`mlx-edit`，≈2min/张） | 需配 img2img/IPAdapter 工作流 |
| 建议 | 正式出图主力 | 负向控制、批量省钱、后续接漫画向 checkpoint |

> 若想让 ComfyUI 承担正式出图，建议放一个漫画向 checkpoint（如 SDXL 系动漫模型）到
> `~/ComfyUI/models/checkpoints/`，其余无需改动。

---

## 五、已知限制与对策

| 限制 | 说明 | 对策 |
|---|---|---|
| FLUX.2 不支持负向提示 | mflux 对 FLUX.2 会直接拒绝 `--negative-prompt` | 排除类要求写进**正向**提示词（`prompt.py` 已内置 no text/no bubbles 约束）；负向词仅对 ComfyUI 后端生效 |
| 画面偶发残留文字 | 漫画风模型倾向于画招牌/拟声词 | 已强化风格预设中的「无任何文字」要求；残留可用候选重抽或后续 inpaint 流程擦除 |
| 参考图条件生成较慢 | `mlx-edit` 640×960/6 步 ≈ 2 分钟/张（文生图约 40s） | 草稿走文生图多候选，选定后用参考图做 Final；草图档也提供「角色参考图」开关 |
| 无真 mask 重绘 | FLUX.2 edit 只吃图片不吃 mask；`mflux-generate-fill` 需 FLUX.1 Fill 权重（未下载） | 已用「裁剪—条件重绘—羽化回贴」替代（选区重绘可用）；下载 Fill 权重后可直接切真 mask 路径 |
| 重绘区域边缘融合 | 选区内内容变化较大时，边缘接缝可能可见 | 提高上下文边距（`margin_ratio`）或多轮小幅重绘；羽化像素可调（默认 14） |
| 角色一致性仍非 100% | 参考图条件能锁住发型/服装/脸型，但极端角度仍会漂 | 后续：多张参考图（已支持最多 2 张）、角色 LoRA（MVP-3）、人工 Krita 精修 |

---

## 六、进度与下一阶段

- [x] **MVP-1**：故事 → 结构化分镜 → 候选图 → 选择 → 排版（中文气泡）→ PNG/PDF/CBZ
- [x] **MVP-2（角色一致性）**：Character Bible 设定图 + `mlx-edit` 参考图条件生成 + 重跑分镜保资产
- [x] **MVP-2 余项（人工接管）**：选区局部重绘 + 外部编辑器往返
- [x] **多引擎 + 中文体验**：ComfyUI 接入（含 checkpoint 下拉）；中文文字框字体/字号/禁则；**前端全中文**
- [x] **项目资产库**：人物 / 场景 / 道具 三类一致性资产（设定图 + 提示词注入 + 参考图挂载 + 引用计数）
- [x] **常用风格库**：19 种 / 6 组（取材腾讯动漫热门题材族），单一数据源，带风格硬指令与推荐参数
- [x] **效率优化**：批量出图（整页/整项目、仅未出图）、资产改动一键重跑引用分镜、导出文件下载清单
- [x] **候选质量评分（MVP-3）**：客观指标 + 本地 VLM（Qwen3.5-MLX）点评、评分徽标、自动选最佳
- [x] **收官批次**：一键升级画风（老黑白项目→彩色）、分镜编辑（复制/删除/新增）、
  多语言对白与导出（繁中/英/日，本机 LLM 翻译）、首页统计卡
- [x] **角色 LoRA（MVP-3）**：数据集自动准备 → `mflux-train` 训练 → 权重登记 → 生成时挂载
  （⚠️ 训练需完整精度 FLUX.2-klein-4B 权重，本机只有 8bit，需先下载约 16GB）
- [ ] **MVP-3**：角色 LoRA 训练（`mflux-train` 已就绪）、候选排序 / 自动质量检测、多语言、真 mask 重绘
- [ ] ComfyUI 深化：接漫画向 checkpoint、IPAdapter/ControlNet 工作流（角色姿势控制）

---

## 打包分发

```bash
./scripts/package.sh                 # 纯代码包（自带前端构建产物，解包即跑）
./scripts/package.sh --with-docs     # 附带 docs/ 截图与样张
./scripts/package.sh --with-data     # 附带作品数据（projects/ + 数据库）
./scripts/package.sh --all           # 代码 + 文档 + 数据（完整备份/迁移）
```

产物在 `dist/`：`comic-workbench-<版本>-<日期>.tar.gz` 与同名 `.zip`（带数据时加 `-full` 后缀）。

**包内有什么**：backend / frontend（含 `dist/` 构建产物）/ scripts / workflows /
`start.sh` + `start.bat` + `INSTALL.md` + README。

**包内没有什么**（有意排除）：`node_modules`、虚拟环境、`__pycache__`、
LoRA 训练中间产物（`training*` / `dataset` / `checkpoint.zip`）、HF 模型权重（15GB，外部依赖）。

拿到包的人只需 Python 3.11+：

```bash
./start.sh          # macOS/Linux；Windows 双击 start.bat
```

首次运行自动建 `.venv` 并装依赖，然后开在 http://127.0.0.1:8770。
出图能力来自外部后端（Mac 用 vMLX/mflux、任意平台可用 ComfyUI、无 GPU 时用 mock），
详见包内 `INSTALL.md`。

### 实测体积

| 包 | 体积 | 内容 |
|---|---|---|
| `…-20261006.tar.gz` | **556 KB** | 80 文件 / 解压 2.0 MB（可分发、可直接跑） |
| `…-20261006-full.tar.gz` | **240 MB** | 193 文件（含 98 张作品图、5 份 PDF、4 份 CBZ、LoRA adapter） |

打包后会做**解包实测**：干净目录 → `./start.sh` → 自动装依赖 → `/` 与全部 API 返回 200。

### 完整备份包（`i` 开头的 HF 缓存）

`i` 开头的 HF 缓存（15GB 模型权重）不随包分发。新机器上按 README
「大模型权重的国内高速下载」一节用 `scripts/ms_hf_download.py` 获取。
