# ComfyUI 工作流模板

本目录存放提交给本机 ComfyUI 的 **API 格式**工作流（不是前端导出格式）。

## panel.json

当前生效的分镜出图工作流：
`CheckpointLoaderSimple → CLIPTextEncode ×2 → EmptyLatentImage → KSampler → VAEDecode → SaveImage`

- **`_role` 标记**：`CLIPTextEncode` 节点上的 `_role: "positive" | "negative"` 是漫画工作台的注入标记，
  系统据此把「正向提示词 / 负向提示词」写进对应节点。这是本系统约定，ComfyUI 自身会忽略该字段。
- ⚠️ **模板里不能出现非节点键**（例如 `_meta`、注释字段），否则 ComfyUI 会报
  `missing_node_type: Node 'ID #_meta' has no class_type`。
  provider 已做容错：加载时会跳过所有没有 `class_type` 的条目，但仍建议保持模板干净。
- 换模型：把 `CheckpointLoaderSimple.inputs.ckpt_name` 改成 `~/ComfyUI/models/checkpoints/` 下的文件名，
  或在前端「生成引擎」选 ComfyUI 后从模型下拉里挑（无需改文件）。

## 参数注入规则

| 节点 | 注入字段 |
|---|---|
| `CheckpointLoaderSimple` | `ckpt_name` ← 请求的 model |
| `CLIPTextEncode` + `_role=positive` | `text` ← 正向提示词 |
| `CLIPTextEncode` + `_role=negative` | `text` ← 负向提示词 |
| `KSampler` / `KSamplerAdvanced` | `seed` / `steps` / `cfg` |
| `EmptyLatentImage` / `EmptySD3LatentImage` | `width` / `height` |
| `LoadImage`（img2img 类） | `image` ← 第一张参考图路径 |

## 后端规格差异

| 引擎 | 分辨率 | 步数 | 引导 | 负向提示词 |
|---|---|---|---|---|
| ComfyUI（SD1.5 系） | 512×768 | 20（Final 24） | cfg 7.0 | **支持** |
| MLX · flux2-klein-4b | 640×960 | 6（Final 8） | 1.0 | 不支持（写进正向） |

规格在 `backend/app/config.py` 里以 `COMFYUI_*` / `DRAFT_*` 分别配置，worker 按 provider 自动选择。
