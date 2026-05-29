"""测试 ``review_queue.yaml_writer`` 在写回时保留 YAML 注释 / 顺序 / 缩进。"""

from __future__ import annotations

from pathlib import Path

import pytest

from review_queue.yaml_writer import (
    load_yaml,
    save_yaml,
    update_yaml_field,
    update_yaml_fields,
)

SAMPLE_YAML = """\
# 顶部注释 — 必须保留
deepseek:
  api_key: ""                                       # 注释 A
  base_url: "https://api.deepseek.com"              # 注释 B
  model: "deepseek-v4-flash"
  timeout_seconds: 60

runtime:
  mode: "semi-auto"           # auto | semi-auto
  dry_run: true
  project_root: "."

# 调度小节注释
scheduler:
  enabled: false
  timezone: "Asia/Shanghai"
  generate_cron: "0 9 * * *"
  review_cron: "30 9 * * *"
"""


@pytest.fixture()
def yaml_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


def test_load_returns_commented_map(yaml_file: Path) -> None:
    data = load_yaml(yaml_file)
    assert data["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert data["scheduler"]["enabled"] is False


def test_save_preserves_top_comment(yaml_file: Path) -> None:
    data = load_yaml(yaml_file)
    data["scheduler"]["enabled"] = True
    save_yaml(yaml_file, data)
    text = yaml_file.read_text(encoding="utf-8")
    assert text.startswith("# 顶部注释 — 必须保留")
    assert "# 调度小节注释" in text


def test_save_preserves_inline_comments(yaml_file: Path) -> None:
    data = load_yaml(yaml_file)
    data["deepseek"]["api_key"] = "sk-secret"
    save_yaml(yaml_file, data)
    text = yaml_file.read_text(encoding="utf-8")
    assert "# 注释 A" in text
    assert "# 注释 B" in text
    assert "sk-secret" in text


def test_save_preserves_key_order(yaml_file: Path) -> None:
    data = load_yaml(yaml_file)
    data["scheduler"]["generate_cron"] = "30 8 * * *"
    save_yaml(yaml_file, data)
    text = yaml_file.read_text(encoding="utf-8")
    deepseek_pos = text.find("deepseek:")
    runtime_pos = text.find("runtime:")
    scheduler_pos = text.find("scheduler:")
    assert 0 <= deepseek_pos < runtime_pos < scheduler_pos


def test_save_preserves_indentation_style(yaml_file: Path) -> None:
    data = load_yaml(yaml_file)
    data["runtime"]["mode"] = "auto"
    save_yaml(yaml_file, data)
    lines = yaml_file.read_text(encoding="utf-8").splitlines()
    runtime_index = next(i for i, ln in enumerate(lines) if ln.startswith("runtime:"))
    assert lines[runtime_index + 1].startswith("  mode:")


def test_save_uses_atomic_rename(yaml_file: Path, tmp_path: Path) -> None:
    data = load_yaml(yaml_file)
    save_yaml(yaml_file, data)
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_update_yaml_field_dotted_path(yaml_file: Path) -> None:
    update_yaml_field(yaml_file, "scheduler.generate_cron", "15 7 * * *")
    data = load_yaml(yaml_file)
    assert data["scheduler"]["generate_cron"] == "15 7 * * *"
    text = yaml_file.read_text(encoding="utf-8")
    assert "# 顶部注释 — 必须保留" in text
    assert "# 注释 A" in text


def test_update_yaml_field_creates_missing_branch(yaml_file: Path) -> None:
    update_yaml_field(yaml_file, "notifications.critical_enabled", True)
    data = load_yaml(yaml_file)
    assert data["notifications"]["critical_enabled"] is True


def test_update_yaml_field_rejects_empty_path(yaml_file: Path) -> None:
    with pytest.raises(ValueError):
        update_yaml_field(yaml_file, "", "x")


def test_update_yaml_fields_batch(yaml_file: Path) -> None:
    update_yaml_fields(
        yaml_file,
        [
            ("scheduler.enabled", True),
            ("scheduler.review_cron", "45 9 * * *"),
            ("runtime.dry_run", False),
        ],
    )
    data = load_yaml(yaml_file)
    assert data["scheduler"]["enabled"] is True
    assert data["scheduler"]["review_cron"] == "45 9 * * *"
    assert data["runtime"]["dry_run"] is False
    text = yaml_file.read_text(encoding="utf-8")
    assert "# 调度小节注释" in text


def test_update_yaml_fields_empty_iterable_is_noop(yaml_file: Path) -> None:
    original = yaml_file.read_text(encoding="utf-8")
    update_yaml_fields(yaml_file, [])
    assert yaml_file.read_text(encoding="utf-8") == original


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_yaml(tmp_path / "nope.yaml")


def test_load_non_mapping_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load_yaml(p)


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "config.yaml"
    save_yaml(target, {"key": "value"})
    assert target.exists()
    assert load_yaml(target)["key"] == "value"
