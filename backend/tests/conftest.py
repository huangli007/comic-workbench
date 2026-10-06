"""pytest 全局保护：确保测试绝不可能写穿真实数据库 / 真实项目目录。

教训（2026-09-25）：全量测试把真实 comic_workbench.db 写穿了（7 个项目回退到 3 个）。
根因：db.py 在模块导入时用 config.DB_PATH（真实路径）创建 engine，
而 fixture 在导入后才 monkeypatch，为时已晚。

解法：conftest.py 在 **模块顶层**（测试收集最早执行、app.* 尚未 import 之前）
设置 `CW_TEST_DB_PATH` / `CW_TEST_PROJECTS_DIR` 环境变量；config.py 在 import 早期
读取这两个变量，把 DB_PATH / PROJECTS_DIR 钉到临时目录。这样 db.py 首次建 engine
时用的就是临时库，从根上隔离。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ── 必须在任何 app.* import 之前生效（conftest 顶层 = 收集最早阶段）──
_TMP = Path(tempfile.mkdtemp(prefix="cw_test_"))
os.environ["CW_TEST_DB_PATH"] = str(_TMP / "test.db")
os.environ["CW_TEST_PROJECTS_DIR"] = str(_TMP / "projects")

import atexit  # noqa: E402

atexit.register(lambda: __import__("shutil").rmtree(_TMP, ignore_errors=True))
