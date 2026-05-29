"""Tests for generator/c_pipeline/validators.py (Phase C.1).

Pure-function checks — no LLM, no I/O except a temp blacklist file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.c_pipeline.validators import (
    ValidationResult,
    check_ai_slop,
    check_outline_section_count,
    check_outline_section_words,
    check_paragraph_length,
    check_section_count_conservation,
    check_section_word_count,
    check_total_word_count,
    count_chinese_chars,
    load_ai_slop_blacklist,
    split_paragraphs,
    summarize_section_validations,
)

# ------------------------------------------------------------ count_chinese_chars


def test_count_chinese_chars_handles_pure_chinese() -> None:
    assert count_chinese_chars("这是一段中文") == 6


def test_count_chinese_chars_ignores_ascii_and_punct() -> None:
    assert count_chinese_chars("中文a1!?,。 中文") == 4


def test_count_chinese_chars_none_and_empty() -> None:
    assert count_chinese_chars(None) == 0
    assert count_chinese_chars("") == 0
    assert count_chinese_chars("ABC123") == 0


# ------------------------------------------------------------ split_paragraphs


def test_split_paragraphs_handles_single_newlines() -> None:
    text = "第一段。\n第二段。\n第三段。"
    assert split_paragraphs(text) == ["第一段。", "第二段。", "第三段。"]


def test_split_paragraphs_drops_blank_lines_and_crlf() -> None:
    text = "段一\r\n\r\n段二\n\n  \n段三"
    assert split_paragraphs(text) == ["段一", "段二", "段三"]


def test_split_paragraphs_empty() -> None:
    assert split_paragraphs("") == []
    assert split_paragraphs(None) == []
    assert split_paragraphs("   \n  \n") == []


# ------------------------------------------------------------ check_section_word_count


def test_check_section_word_count_pass_at_floor() -> None:
    text = "字" * 800
    r = check_section_word_count(text)
    assert r.ok and "ok" in r.message


def test_check_section_word_count_too_short() -> None:
    text = "字" * 799
    r = check_section_word_count(text)
    assert not r.ok
    assert "too short" in r.message
    assert any("799" in d for d in r.details)


def test_check_section_word_count_too_long() -> None:
    text = "字" * 1501
    r = check_section_word_count(text)
    assert not r.ok
    assert "too long" in r.message


def test_check_section_word_count_disable_max_with_zero() -> None:
    text = "字" * 5000
    r = check_section_word_count(text, max_chars=0)
    assert r.ok


# ------------------------------------------------------------ check_paragraph_length


def test_check_paragraph_length_all_under_limit() -> None:
    body = "\n".join(["短句一段。"] * 5)
    r = check_paragraph_length(body, max_chars=60)
    assert r.ok
    assert "5" in r.message


def test_check_paragraph_length_one_over_limit() -> None:
    long = "字" * 70  # 70 chinese chars > 60
    body = "正常段。\n" + long + "\n再来一段。"
    r = check_paragraph_length(body, max_chars=60)
    assert not r.ok
    assert "1" in r.message
    assert any("para#2" in d and "chars=70" in d for d in r.details)


def test_check_paragraph_length_empty_text() -> None:
    r = check_paragraph_length("")
    assert not r.ok
    assert "no non-empty" in r.message


def test_check_paragraph_length_counts_only_chinese() -> None:
    # An ASCII-heavy paragraph still passes when CJK chars ≤ 60.
    body = "中文短。\n" + "abc " * 100
    r = check_paragraph_length(body, max_chars=60)
    assert r.ok


# ------------------------------------------------------------ check_ai_slop


def test_check_ai_slop_no_hits() -> None:
    blacklist = ["不禁", "顿时", "心如刀绞"]
    r = check_ai_slop("这是一段没有违禁词的稿件。", blacklist)
    assert r.ok


def test_check_ai_slop_counts_repeated_hits() -> None:
    blacklist = ["顿时", "瞬间"]
    text = "她顿时愣住,顿时又笑了。瞬间想明白了一切。"
    r = check_ai_slop(text, blacklist)
    assert not r.ok
    assert "2 term(s)" in r.message
    joined = " ".join(r.details)
    assert "'顿时' x2" in joined
    assert "'瞬间' x1" in joined


def test_check_ai_slop_skips_empty_blacklist_entries() -> None:
    r = check_ai_slop("任何文本", ["", "  ", None])  # type: ignore[arg-type]
    assert r.ok


def test_check_ai_slop_empty_text_passes() -> None:
    r = check_ai_slop("", ["顿时"])
    assert r.ok


# ------------------------------------------------------------ outline checks


def test_check_outline_section_count_in_range() -> None:
    assert check_outline_section_count(8).ok
    assert check_outline_section_count(15).ok
    assert check_outline_section_count(11).ok


def test_check_outline_section_count_too_few() -> None:
    r = check_outline_section_count(7)
    assert not r.ok
    assert "min 8" in r.message


def test_check_outline_section_count_too_many() -> None:
    r = check_outline_section_count(16)
    assert not r.ok
    assert "max 15" in r.message


def test_check_outline_section_words_all_pass() -> None:
    r = check_outline_section_words([800, 1000, 1500, 1200])
    assert r.ok


def test_check_outline_section_words_some_fail() -> None:
    r = check_outline_section_words([799, 1000, 1501, 1200])
    assert not r.ok
    assert "2 outline" in r.message
    joined = " ".join(r.details)
    assert "section#1" in joined
    assert "section#3" in joined


# ------------------------------------------------------------ check_total_word_count


def test_check_total_word_count_within_tolerance() -> None:
    # target 10000, tolerance 10% → [9000, 11000]
    assert check_total_word_count(9000, target=10000).ok
    assert check_total_word_count(11000, target=10000).ok
    assert check_total_word_count(10000, target=10000).ok


def test_check_total_word_count_too_low() -> None:
    r = check_total_word_count(8500, target=10000)
    assert not r.ok
    assert "below" in r.message


def test_check_total_word_count_too_high() -> None:
    r = check_total_word_count(11500, target=10000)
    assert not r.ok
    assert "above" in r.message


def test_check_total_word_count_invalid_target() -> None:
    r = check_total_word_count(10000, target=0)
    assert not r.ok


def test_check_total_word_count_custom_tolerance() -> None:
    r = check_total_word_count(8000, target=10000, tolerance=0.25)
    assert r.ok  # ±25% → [7500, 12500]


# ------------------------------------------------------------ section count conservation


def test_section_count_conservation_match() -> None:
    assert check_section_count_conservation(10, 10).ok


def test_section_count_conservation_mismatch() -> None:
    r = check_section_count_conservation(10, 9)
    assert not r.ok
    assert "expected 10" in r.message
    assert "got 9" in r.message


# ------------------------------------------------------------ load_ai_slop_blacklist


def test_load_ai_slop_blacklist_flat_array(tmp_path: Path) -> None:
    p = tmp_path / "blacklist.json"
    p.write_text(json.dumps(["顿时", "瞬间", ""], ensure_ascii=False), encoding="utf-8")
    words = load_ai_slop_blacklist(p)
    assert "顿时" in words
    assert "瞬间" in words
    # empty entries dropped
    assert "" not in words


def test_load_ai_slop_blacklist_category_dict(tmp_path: Path) -> None:
    p = tmp_path / "blacklist.json"
    p.write_text(
        json.dumps(
            {
                "version": "1.0",
                "categories": {
                    "陈词滥调": ["顿时", "瞬间"],
                    "AI 比喻": ["如同潮水般"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    words = load_ai_slop_blacklist(p)
    assert set(words) == {"顿时", "瞬间", "如同潮水般"}


def test_load_ai_slop_blacklist_words_key(tmp_path: Path) -> None:
    p = tmp_path / "blacklist.json"
    p.write_text(json.dumps({"words": ["A", "B"]}), encoding="utf-8")
    words = load_ai_slop_blacklist(p)
    assert words == ["A", "B"]


def test_load_ai_slop_blacklist_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_ai_slop_blacklist(tmp_path / "nope.json") == []


def test_load_ai_slop_blacklist_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_ai_slop_blacklist(p) == []


# ------------------------------------------------------------ summarize_section_validations


def test_summarize_all_pass() -> None:
    r = summarize_section_validations(
        {
            "length": ValidationResult(ok=True, message="ok"),
            "paragraph": ValidationResult(ok=True, message="ok"),
            "slop": ValidationResult(ok=True, message="ok"),
        }
    )
    assert r.ok
    assert "all section checks passed" in r.message


def test_summarize_lists_failed_names_and_details() -> None:
    r = summarize_section_validations(
        {
            "length": ValidationResult(ok=True, message="ok"),
            "paragraph": ValidationResult(
                ok=False, message="fail-para", details=["para#2 too long"]
            ),
            "slop": ValidationResult(
                ok=False, message="fail-slop", details=["'顿时' x1"]
            ),
        }
    )
    assert not r.ok
    assert "paragraph" in r.message
    assert "slop" in r.message
    text = "\n".join(r.details)
    assert "[paragraph]" in text
    assert "para#2 too long" in text
    assert "[slop]" in text
    assert "'顿时' x1" in text


def test_validation_result_truthy() -> None:
    assert bool(ValidationResult(ok=True, message=""))
    assert not bool(ValidationResult(ok=False, message=""))
