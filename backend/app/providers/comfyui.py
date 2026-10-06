"""ComfyUI Provider —— 对接本机已部署的 ComfyUI（HTTP API /prompt → /history → /view）。

当前本机状态（2026-09-25）：
- ComfyUI v0.37.0 运行于 127.0.0.1:8188，设备 mps（16GB 统一内存）
- 可用 checkpoint：v1-5-pruned-emaonly-fp16.safetensors（SD1.5 基础模型）

工作流模板：`workflows/panel.json`（ComfyUI **API 格式**）
- CLIPTextEncode 节点上的 `_role: positive|negative` 是本系统的注入标记
- 模板里**不能出现非节点键**（如 "_meta"），否则 ComfyUI 报 missing_node_type
  → 本 provider 会主动跳过没有 class_type 的条目，容忍手工编辑过的模板
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from .base import GenRequest, ImageProvider, ImageProviderError

WORKFLOW_PATH = config.BASE_DIR / "workflows" / "panel.json"
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 3600


def _http_json(url: str, data: dict | None = None, timeout: float = 10):
    if data is not None:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:1500]
        except Exception:
            pass
        raise ImageProviderError(f"ComfyUI HTTP {e.code}: {detail}") from e


def load_workflow() -> dict:
    if not WORKFLOW_PATH.exists():
        raise ImageProviderError(f"工作流不存在: {WORKFLOW_PATH}")
    try:
        raw = json.loads(WORKFLOW_PATH.read_text())
    except Exception as e:
        raise ImageProviderError(f"工作流 JSON 解析失败: {e}") from e
    # 只保留真正的节点（有 class_type 的 dict），跳过 _meta / 注释等
    return {
        k: v for k, v in raw.items()
        if isinstance(v, dict) and v.get("class_type")
    }


def list_checkpoints() -> list[str]:
    """当前 ComfyUI 可用的 checkpoint 列表（用于前端下拉）。"""
    try:
        info = _http_json(f"{config.COMFYUI_URL}/object_info/CheckpointLoaderSimple", timeout=5)
        return list(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
    except Exception:
        return []


class ComfyUIProvider(ImageProvider):
    name = "comfyui"

    def available(self) -> bool:
        try:
            _http_json(f"{config.COMFYUI_URL}/system_stats", timeout=3)
        except Exception:
            return False
        return WORKFLOW_PATH.exists()

    def info(self) -> dict:
        d = super().info()
        d["url"] = config.COMFYUI_URL
        d["checkpoints"] = list_checkpoints()
        return d

    def generate(self, req: GenRequest) -> Path:
        workflow = load_workflow()
        if not workflow:
            raise ImageProviderError("工作流里没有任何有效节点")

        for node in workflow.values():
            cls = node.get("class_type", "")
            ins = node.get("inputs", {})
            if cls == "CheckpointLoaderSimple" and req.model:
                ins["ckpt_name"] = req.model
            elif cls in ("KSampler", "KSamplerAdvanced"):
                if ins.get("seed") is not None or "seed" in ins:
                    ins["seed"] = req.seed
                if "steps" in ins:
                    ins["steps"] = req.steps
                if "cfg" in ins:
                    ins["cfg"] = req.guidance
            elif cls == "CLIPTextEncode":
                role = node.get("_role", "")
                if role == "positive":
                    ins["text"] = req.prompt
                elif role == "negative":
                    ins["text"] = req.negative_prompt or ""
            elif cls in ("EmptyLatentImage", "EmptySD3LatentImage"):
                ins["width"] = req.width
                ins["height"] = req.height
            elif cls in ("LoadImage",) and req.reference_images:
                # 参考图（img2img 类工作流）：填第一张
                ins["image"] = str(req.reference_images[0])

        res = _http_json(f"{config.COMFYUI_URL}/prompt", {"prompt": workflow})
        prompt_id = res.get("prompt_id")
        if not prompt_id:
            raise ImageProviderError(f"ComfyUI 未返回 prompt_id: {res}")

        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            hist = _http_json(f"{config.COMFYUI_URL}/history/{prompt_id}")
            entry = hist.get(prompt_id)
            if not entry:
                continue
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise ImageProviderError(f"ComfyUI 执行出错: {json.dumps(status)[:1200]}")
            if not status.get("completed", False) and status.get("status_str") != "success":
                continue
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    fname = urllib.parse.quote(img["filename"])
                    sub = urllib.parse.quote(img.get("subfolder", ""))
                    url = (f"{config.COMFYUI_URL}/view?filename={fname}"
                           f"&subfolder={sub}&type={img.get('type', 'output')}")
                    req.output_path.parent.mkdir(parents=True, exist_ok=True)
                    with urllib.request.urlopen(url, timeout=120) as resp, \
                            open(req.output_path, "wb") as f:
                        f.write(resp.read())
                    if req.output_path.exists() and req.output_path.stat().st_size > 0:
                        return req.output_path
            raise ImageProviderError("ComfyUI 完成但未找到输出图像")
        raise ImageProviderError("ComfyUI 生成超时")
