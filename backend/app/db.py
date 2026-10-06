"""SQLite + SQLModel。"""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from . import config

# 轻量列迁移：老库补列（SQLModel.create_all 不会给已存在的表加列）
COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "project": {
        "bubble_style": "TEXT DEFAULT 'heiti'",
        "font_scale": "REAL DEFAULT 1.0",
    },
    "panel": {
        "scene_id": "TEXT DEFAULT ''",
        "prop_ids": "TEXT DEFAULT '[]'",
        "dialogue_translations": "TEXT DEFAULT '{}'",
    },
    "character": {
        "lora_path": "TEXT DEFAULT ''",
        "lora_scale": "REAL DEFAULT 1.0",
    },
    "candidate": {
        "score": "REAL DEFAULT 0.0",
        "score_detail": "TEXT DEFAULT '{}'",
        "scored_at": "DATETIME",
    },
}

# engine 是模块级可变引用：测试可用 `monkeypatch.setattr(db, "engine", tmp_engine)` 替换，
# 生产环境在 init_db 时按 config.DB_PATH 创建。
engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


def reset_engine() -> None:
    """按当前 config.DB_PATH 重建 engine（测试 / 切库时用）。"""
    global engine
    engine.dispose()
    engine = create_engine(
        f"sqlite:///{config.DB_PATH}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


def _ensure_columns() -> None:
    with engine.connect() as conn:
        for table, cols in COLUMN_MIGRATIONS.items():
            try:
                existing = {
                    row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
                }
            except Exception:
                continue
            if not existing:
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        conn.commit()


def init_db() -> None:
    # 导入所有表模型以便 SQLModel.metadata 注册
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_columns()


def get_session():
    with Session(engine) as session:
        yield session
