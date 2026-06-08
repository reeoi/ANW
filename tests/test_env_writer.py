"""测试 ``review_queue.env_writer`` 在写回 ``.env`` 时保留注释 / 空行 / 顺序。"""

from __future__ import annotations

from pathlib import Path

import pytest

from review_queue.env_writer import (
    read_env,
    write_env_field,
    write_env_fields,
)

SAMPLE_ENV = """\
# 顶部注释 - 必须保留

# DeepSeek 凭据
DEEPSEEK_API_KEY=sk-old-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

# 番茄账号 (可选)
FANSQ_USERNAME=
"""


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    p = tmp_path / ".env"
    p.write_text(SAMPLE_ENV, encoding="utf-8")
    return p


def test_read_env_extracts_keys(env_file: Path) -> None:
    data = read_env(env_file)
    assert data["DEEPSEEK_API_KEY"] == "sk-old-key"
    assert data["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
    assert data["FANSQ_USERNAME"] == ""


def test_read_env_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_env(tmp_path / "missing.env") == {}


def test_read_env_strips_quotes(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=\"sk-quoted\"\nNAME='single'\n", encoding="utf-8")
    data = read_env(p)
    assert data["DEEPSEEK_API_KEY"] == "sk-quoted"
    assert data["NAME"] == "single"


def test_read_env_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# comment\n\nKEY=value\n   # indented comment\n", encoding="utf-8")
    assert read_env(p) == {"KEY": "value"}


def test_read_env_strips_inline_comment_for_unquoted(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text('KEY=value #trailing\n', encoding="utf-8")
    assert read_env(p) == {"KEY": "value"}


def test_read_env_keeps_quoted_inline_hash(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text('NOTE="hello # world"\n', encoding="utf-8")
    assert read_env(p) == {"NOTE": "hello # world"}


def test_write_env_field_replaces_inline(env_file: Path) -> None:
    write_env_field(env_file, "DEEPSEEK_API_KEY", "sk-new-key")
    data = read_env(env_file)
    assert data["DEEPSEEK_API_KEY"] == "sk-new-key"
    text = env_file.read_text(encoding="utf-8")
    assert "# 顶部注释 - 必须保留" in text
    assert "# DeepSeek 凭据" in text
    assert "# 番茄账号 (可选)" in text


def test_write_env_field_preserves_order(env_file: Path) -> None:
    write_env_field(env_file, "DEEPSEEK_BASE_URL", "https://example.test/v1")
    text = env_file.read_text(encoding="utf-8")
    api_pos = text.find("DEEPSEEK_API_KEY=")
    base_pos = text.find("DEEPSEEK_BASE_URL=")
    model_pos = text.find("DEEPSEEK_MODEL=")
    assert 0 <= api_pos < base_pos < model_pos


def test_write_env_field_appends_unknown_key(env_file: Path) -> None:
    write_env_field(env_file, "NEW_FLAG", "yes")
    text = env_file.read_text(encoding="utf-8")
    assert "NEW_FLAG=yes" in text
    # 既有键不动
    assert "DEEPSEEK_API_KEY=sk-old-key" in text


def test_write_env_field_quotes_when_value_has_spaces(env_file: Path) -> None:
    write_env_field(env_file, "ANW_NOTE", "hello world #with hash")
    text = env_file.read_text(encoding="utf-8")
    assert 'ANW_NOTE="hello world #with hash"' in text
    # 读回时应能解析回原值
    assert read_env(env_file)["ANW_NOTE"] == "hello world #with hash"


def test_write_env_field_quotes_empty_string(env_file: Path) -> None:
    write_env_field(env_file, "FANSQ_PASSWORD", "")
    text = env_file.read_text(encoding="utf-8")
    assert 'FANSQ_PASSWORD=""' in text
    assert read_env(env_file)["FANSQ_PASSWORD"] == ""


def test_write_env_field_creates_new_file(tmp_path: Path) -> None:
    target = tmp_path / "fresh.env"
    write_env_field(target, "API_KEY", "abc")
    assert target.exists()
    assert read_env(target) == {"API_KEY": "abc"}


def test_write_env_field_atomic_no_tmp_left(env_file: Path) -> None:
    write_env_field(env_file, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    leftover = list(env_file.parent.glob("*.tmp"))
    assert leftover == []


def test_write_env_field_invalid_key_raises(env_file: Path) -> None:
    with pytest.raises(ValueError):
        write_env_field(env_file, "1INVALID", "x")
    with pytest.raises(ValueError):
        write_env_field(env_file, "WITH-DASH", "x")


def test_write_env_fields_batch(env_file: Path) -> None:
    write_env_fields(
        env_file,
        [
            ("DEEPSEEK_API_KEY", "sk-batch-key"),
            ("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            ("BRAND_NEW", "1"),
        ],
    )
    data = read_env(env_file)
    assert data["DEEPSEEK_API_KEY"] == "sk-batch-key"
    assert data["DEEPSEEK_MODEL"] == "deepseek-v4-flash"
    assert data["BRAND_NEW"] == "1"
    text = env_file.read_text(encoding="utf-8")
    assert "# 顶部注释 - 必须保留" in text


def test_write_env_fields_empty_is_noop(env_file: Path) -> None:
    original = env_file.read_text(encoding="utf-8")
    write_env_fields(env_file, [])
    assert env_file.read_text(encoding="utf-8") == original


def test_write_env_field_does_not_corrupt_unknown_lines(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("garbled this line not = standard\nVALID=1\n", encoding="utf-8")
    write_env_field(p, "VALID", "2")
    text = p.read_text(encoding="utf-8")
    assert "garbled this line not = standard" in text
    assert "VALID=2" in text
