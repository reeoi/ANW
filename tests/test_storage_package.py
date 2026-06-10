"""storage/ 包抽取（改进清单 #11）的验收测试。

四个关注点：

1. ``schema_version`` 表随 ``initialize_database`` 建立、幂等、可查询；
2. ``review_queue.db`` / ``models`` / ``metrics`` 退化为 shim，与 storage 同一对象；
3. generator 子树导入后不得出现任何 ``review_queue`` 模块（循环耦合根治的守卫，
   用子进程验证以避免本进程 sys.modules 污染）；
4. 统一连接入口 ``storage.connection.connect`` 可用。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from storage import connection as storage_connection
from storage import models as storage_models
from storage import schema as storage_schema
from storage import stories as storage_stories
from storage import usage as storage_usage


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "s.sqlite3")}},
        path=Path("s.yaml"),
    )


def test_connect_opens_usable_sqlite_connection(tmp_path: Path) -> None:
    db = tmp_path / "c.sqlite3"
    with storage_connection.connect(db) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t (x) VALUES (1)")
    assert db.exists()
    with storage_connection.connect(db) as conn:
        assert conn.execute("SELECT x FROM t").fetchone() == (1,)


def test_initialize_records_schema_version(tmp_path: Path) -> None:
    db = storage_schema.initialize_database(_config(tmp_path))
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert rows == [(storage_schema.SCHEMA_VERSION,)]
    assert storage_schema.get_schema_version(db) == storage_schema.SCHEMA_VERSION


def test_initialize_schema_version_idempotent(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db = storage_schema.initialize_database(cfg)
    storage_schema.initialize_database(cfg)
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1


def test_get_schema_version_zero_for_legacy_db(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE stories (id INTEGER PRIMARY KEY)")
    assert storage_schema.get_schema_version(legacy) == 0


def test_review_queue_modules_are_shims() -> None:
    import review_queue.db as rq_db
    import review_queue.metrics as rq_metrics
    import review_queue.models as rq_models

    assert rq_db.insert_story is storage_stories.insert_story
    assert rq_db.story_from_row is storage_stories.story_from_row
    assert rq_db.initialize_database is storage_schema.initialize_database
    assert rq_db.get_database_path is storage_connection.get_database_path
    assert rq_db.insert_pipeline_cost_log is storage_usage.insert_pipeline_cost_log
    assert rq_db.SCHEMA is storage_schema.SCHEMA
    assert rq_models.Story is storage_models.Story
    assert rq_models.DailyPublishPlan is storage_models.DailyPublishPlan
    assert rq_models.PipelineCostLogEntry is storage_models.PipelineCostLogEntry
    assert rq_metrics.record_api_usage is storage_usage.record_api_usage
    assert rq_metrics.query_overview is storage_usage.query_overview
    assert rq_metrics.ensure_metrics_schema is storage_schema.ensure_metrics_schema


def test_insert_pipeline_cost_log_honors_occurred_at(tmp_path: Path) -> None:
    """dataclass 的 occurred_at 字段必须真正写入（清单 #13 顺手修）。"""
    from storage.models import PipelineCostLogEntry

    db = storage_schema.initialize_database(_config(tmp_path))
    entry = PipelineCostLogEntry(story_id=None, phase="phase_1", model="m", occurred_at="2026-01-02 03:04:05")
    row_id = storage_usage.insert_pipeline_cost_log(db, entry)
    with sqlite3.connect(db) as conn:
        stored = conn.execute("SELECT occurred_at FROM pipeline_cost_log WHERE id = ?", (row_id,)).fetchone()[0]
    assert stored == "2026-01-02 03:04:05"


def test_insert_pipeline_cost_log_defaults_occurred_at(tmp_path: Path) -> None:
    from storage.models import PipelineCostLogEntry

    db = storage_schema.initialize_database(_config(tmp_path))
    row_id = storage_usage.insert_pipeline_cost_log(db, PipelineCostLogEntry(story_id=None, phase="phase_1", model="m"))
    with sqlite3.connect(db) as conn:
        stored = conn.execute("SELECT occurred_at FROM pipeline_cost_log WHERE id = ?", (row_id,)).fetchone()[0]
    assert stored  # CURRENT_TIMESTAMP default still applies when occurred_at is None


def test_generator_tree_does_not_import_review_queue() -> None:
    """循环耦合守卫：导入 generator 全子树后，sys.modules 不得含 review_queue。"""

    code = (
        "import importlib, pkgutil, sys\n"
        "import generator\n"
        "for m in pkgutil.walk_packages(generator.__path__, 'generator.'):\n"
        "    importlib.import_module(m.name)\n"
        "leaked = sorted(n for n in sys.modules if n.startswith('review_queue'))\n"
        "assert not leaked, f'generator imported review_queue modules: {leaked}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
