# 漫画工作台 Comic Workbench — 安装与启动

本地优先的漫画生产系统：故事拆解 → 分镜 → 出图 → 一致性资产 → 中文排版 → 多语言导出。

## 1. 快速开始

```bash
# macOS / Linux
./start.sh              # 启动，默认 http://127.0.0.1:8770
./start.sh 9000         # 换端口
./stop.sh               # 停止（按端口找监听进程，不会误杀浏览器/IDE）

# Windows
start.bat               # 启动
start.bat 9000
stop.bat                # 停止
```

首次运行会自动创建 `.venv` 并安装后端依赖（约 1-2 分钟）。启动后浏览器打开提示的地址即可。

**系统要求**：Python 3.11+（3.13 已验证）。Windows 安装 Python 时记得勾选 *Add python.exe to PATH*。

## 2. 前端

分发包**自带构建产物** `frontend/dist`，无需 Node.js。
若要改前端源码：

```bash
cd frontend
npm install          # 首次
npm run build        # 产物回到 dist/，FastAPI 直接托管
npm run dev          # 开发模式（需另起后端）
```

## 3. 图像生成后端（重要）

工作台本身不含模型权重，出图能力来自下面三种后端之一，在项目页顶部「引擎」下拉里选择：

| 引擎 | 说明 | 适用 |
|---|---|---|
| `mock` | 假图，仅占位 | **无 GPU 环境试跑全流程**（默认可用） |
| `mlx` / `mlx-edit` | 本机 mflux + FLUX.2 Klein 4B（Apple Silicon） | macOS 出图主力 |
| `comfyui` | HTTP 调本机 ComfyUI（默认 8188） | 有 SD 系模型的机器 |

### macOS 走 MLX 出图

需要本机装好 vMLX（自带 mflux / mlx-lm / mlx-vlm 与模型），工作台按下面的默认路径调用：

```
/Applications/vMLX.app/Contents/Resources/bundled-python/python/bin/mflux-generate-flux2
模型: ~/.cache/huggingface/hub/toxicdog/flux2-klein-4b-8bit
```

路径不合就改 `backend/app/config.py` 顶部的 `VMLX_PYTHON_BIN` / `MLX_MODEL`。

### Windows / 无 Apple Silicon

- 出图请用 `comfyui` 后端（另装 ComfyUI + 任一 SD 模型）
- 或先用 `mock` 跑通拆解/排版/导出/多语言等全部非生成环节

## 4. 目录说明

```
backend/           FastAPI 后端（app/api 接口、app/pipeline 流水线、app/providers 出图引擎）
frontend/          Vue3 + Element Plus（dist/ 为构建产物）
scripts/           辅助脚本（e2e 验证、打包、大模型下载、恢复工具）
workflows/         出图/训练用的配置模板
projects/          作品数据（图片、页面、导出）—— 运行时生成
comic_workbench.db SQLite 索引（项目/分镜/候选/资产的元数据）
docs/              界面截图与效果样张
```

**数据都在 `projects/` 与 `comic_workbench.db`**，备份这两项即可；图片文件不依赖数据库，
数据库万一损坏可用 `python scripts/recover_projects.py` 从磁盘重建索引。

## 5. 常用操作

```bash
# 换端口启动
./start.sh 8888

# 跑测试（需要 requirements-dev.txt 里的 pytest）
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest -q

# 从磁盘恢复数据库索引
python scripts/recover_projects.py --dry-run    # 先看会恢复什么
python scripts/recover_projects.py
```

## 6. 常见问题

**端口被占用** → 先 `./stop.sh` 看是不是工作台自己还开着；换端口用 `./start.sh 8888`。
服务在后台跑着关不掉时，`./stop.sh` 会先 SIGTERM 优雅退出、10 秒后再强制结束。

**启动报缺依赖** → 删掉 `.venv` 重跑 `start.sh`；或手动 `.venv/bin/pip install -r backend/requirements.txt`。

**出图报 mflux 相关错误** → 确认 vMLX 已安装且 `backend/app/config.py` 里的路径正确；
没有 Apple Silicon 就把引擎切到 `mock` 或 `comfyui`。

**图片能看但页面空白** → 确认 `frontend/dist/index.html` 存在（分发包自带的，别删）。

**想把自己的作品一起迁移到另一台机器** → 拷贝 `projects/` 与 `comic_workbench.db` 即可，
也可在源机器用 `./scripts/package.sh --with-data` 打出带数据的完整包。

## 7. 许可与致谢

个人项目。出图依赖 FLUX.2 Klein（Black Forest Labs）、mflux、ComfyUI，各自遵循其原始许可；
使用生成内容时请遵守对应模型的使用条款。
