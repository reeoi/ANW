"""Tests for long-novel L0 outline splitting and backfill behaviour."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.long_novel.l0_book_setup import (
    _extract_volume_outlines,
    _parse_volume_plan,
    ensure_volume_outlines_split,
    run_l0_book_outline,
    run_l0_chapter_outlines,
    run_l0_extend_chapter_outlines,
    run_l0_outline,
    run_l0_volume_outline,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        prompt = str(messages[-1]["content"])
        self.prompts.append(prompt)
        if "第4到第5章" in prompt:
            return SimpleNamespace(text="## 第4章\n第四章细纲\n\n## 第5章\n第五章细纲")
        if "续写规划" in prompt:
            return SimpleNamespace(text="## 第4-5章续写规划\n承接第三章继续升级。")
        # New per-volume LLM call. Echo the requested volume so each call
        # produces a distinct file.
        m = re.search(r"【第([零〇一二两三四五六七八九十\d]+)卷】", prompt)
        if m:
            vol = m.group(1)
            return SimpleNamespace(text=f"## 第{vol}卷：测试卷\n- 章节范围：第1-3章")
        if "第1到第3章" in prompt:
            return SimpleNamespace(text="## 第1章\n第一章细纲")
        if "全书大纲" in prompt and "不要写章节细纲" in prompt:
            return SimpleNamespace(text="## 全书主线\n只写全书结构，不含章节细纲。")
        return SimpleNamespace(text="## 第2章\n第二章细纲\n\n## 第3章\n第三章细纲")


def test_outline_steps_write_separate_files(tmp_path: Path) -> None:
    client = FakeClient()
    (tmp_path / "设定" / "角色").mkdir(parents=True)
    (tmp_path / "设定" / "角色" / "角色设定.md").write_text(
        "## 主角：林晚\n- 身份：退役任务者\n## 反派：沈砚\n- 身份：前男友之一",
        encoding="utf-8",
    )

    run_l0_book_outline(client, tmp_path, "测试书", "都市", target_chapters=3, words_per_chapter=3000)
    run_l0_volume_outline(client, tmp_path, "测试书", "都市", target_chapters=3, words_per_chapter=3000)
    result = run_l0_chapter_outlines(client, tmp_path, "测试书", "都市", target_chapters=3, words_per_chapter=3000)

    assert (tmp_path / "大纲" / "大纲.md").read_text(encoding="utf-8").startswith("## 全书主线")
    assert "第一卷：测试卷" in (tmp_path / "大纲" / "卷纲_第一卷.md").read_text(encoding="utf-8")
    assert (tmp_path / "大纲" / "细纲_第001章.md").read_text(encoding="utf-8").startswith("第1章")
    assert (tmp_path / "大纲" / "细纲_第002章.md").read_text(encoding="utf-8").startswith("第2章")
    assert (tmp_path / "大纲" / "细纲_第003章.md").read_text(encoding="utf-8").startswith("第3章")
    assert result["chapters_generated"] == 3
    assert all("一致性硬约束" in prompt for prompt in client.prompts[:3])
    assert any("林晚" in prompt and "人物只能沿用" in prompt for prompt in client.prompts)


def test_run_l0_outline_compatibility_wrapper_runs_three_steps(tmp_path: Path) -> None:
    client = FakeClient()

    run_l0_outline(client, tmp_path, "测试书", "都市", target_chapters=3, words_per_chapter=3000)

    assert (tmp_path / "大纲" / "大纲.md").exists()
    assert (tmp_path / "大纲" / "卷纲_第一卷.md").exists()
    assert (tmp_path / "大纲" / "细纲_第003章.md").exists()
    assert client.calls == 4


def test_extend_chapter_outlines_only_generates_new_range(tmp_path: Path) -> None:
    client = FakeClient()
    (tmp_path / "设定" / "角色").mkdir(parents=True)
    (tmp_path / "设定" / "角色" / "角色设定.md").write_text("## 主角：林晚", encoding="utf-8")
    (tmp_path / "大纲").mkdir()
    (tmp_path / "大纲" / "大纲.md").write_text("全书主线：测试", encoding="utf-8")
    (tmp_path / "大纲" / "卷纲_第一卷.md").write_text("第一卷：测试卷", encoding="utf-8")
    (tmp_path / "大纲" / "细纲_第003章.md").write_text("## 第3章\n旧第三章", encoding="utf-8")

    result = run_l0_extend_chapter_outlines(
        client,
        tmp_path,
        "测试书",
        "都市",
        old_target_chapters=3,
        new_target_chapters=5,
        words_per_chapter=3000,
        additional_prompt="第二卷要进入反攻",
    )

    assert result["chapters_generated"] == 2
    assert "旧第三章" in (tmp_path / "大纲" / "细纲_第003章.md").read_text(encoding="utf-8")
    assert (tmp_path / "大纲" / "细纲_第004章.md").read_text(encoding="utf-8").startswith("第4章")
    assert (tmp_path / "大纲" / "细纲_第005章.md").read_text(encoding="utf-8").startswith("第5章")
    assert (tmp_path / "大纲" / "续写规划_第004-005章.md").exists()
    assert any("第二卷要进入反攻" in prompt for prompt in client.prompts)


def test_ensure_volume_outlines_split_migrates_legacy_combined_file(tmp_path: Path) -> None:
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    legacy = outline_dir / "卷纲.md"
    legacy.write_text(
        "# 全书卷纲\n\n"
        "## 第一卷：开端\n- 章节范围：第1-3章\n- 核心事件：相遇\n\n"
        "## 第二卷：升级\n- 章节范围：第4-6章\n- 核心事件：决裂\n",
        encoding="utf-8",
    )

    written = ensure_volume_outlines_split(tmp_path)

    v1 = outline_dir / "卷纲_第一卷.md"
    v2 = outline_dir / "卷纲_第二卷.md"
    assert v1.exists() and v2.exists()
    assert "第一卷：开端" in v1.read_text(encoding="utf-8")
    assert "第二卷：升级" in v2.read_text(encoding="utf-8")
    # legacy combined file should be removed after a successful split
    assert not legacy.exists()
    assert set(written) == {"大纲/卷纲_第一卷.md", "大纲/卷纲_第二卷.md"}

    # second call is a no-op (canonical files with single volume each)
    again = ensure_volume_outlines_split(tmp_path)
    assert again == []


def test_ensure_volume_outlines_split_leaves_canonical_single_volume_untouched(tmp_path: Path) -> None:
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    only = outline_dir / "卷纲_第一卷.md"
    only.write_text("## 第一卷：测试\n- 内容", encoding="utf-8")

    written = ensure_volume_outlines_split(tmp_path)

    assert written == []
    assert only.exists()
    assert only.read_text(encoding="utf-8").startswith("## 第一卷")


REAL_BOOK_OUTLINE_VOLPLAN = """
## 按卷划分建议

### 第一卷：「退休咸鱼的噩梦开端」
- **章数范围**：第1-6章（6章，约1.8万字）
- 核心任务：建立世界观

### 第二卷：「破壁者联盟正式开张」
- **章数范围**：第7-12章
- 核心任务：联盟成立

### 第三卷：「在雷区中央起舞」
- **章数范围**：第13-18章

### 第四卷：「修罗场内斗纪元」
- **章数范围**：第19-24章

### 第五卷：「审判日与救赎」
- **章数范围**：第25-30章
"""


def test_parse_volume_plan_extracts_five_volumes_from_real_outline() -> None:
    plan = _parse_volume_plan(REAL_BOOK_OUTLINE_VOLPLAN, target_chapters=30)
    assert [p["vol_num"] for p in plan] == [1, 2, 3, 4, 5]
    assert [(p["ch_start"], p["ch_end"]) for p in plan] == [
        (1, 6), (7, 12), (13, 18), (19, 24), (25, 30),
    ]
    assert "退休咸鱼" in plan[0]["title"]
    assert "审判日" in plan[4]["title"]


def test_parse_volume_plan_falls_back_when_no_plan_in_outline() -> None:
    plan = _parse_volume_plan("没有按卷划分的纯文字大纲。", target_chapters=15)
    # ~6 chapters/volume → 3 volumes covering 1..15
    assert len(plan) >= 2
    assert plan[0]["ch_start"] == 1
    assert plan[-1]["ch_end"] == 15


def test_run_l0_volume_outline_calls_llm_once_per_volume(tmp_path: Path) -> None:
    client = FakeClient()
    (tmp_path / "大纲").mkdir()
    (tmp_path / "大纲" / "大纲.md").write_text(REAL_BOOK_OUTLINE_VOLPLAN, encoding="utf-8")

    result = run_l0_volume_outline(
        client, tmp_path, "测试书", "都市",
        target_chapters=30, words_per_chapter=3000,
    )

    # One LLM call per volume in the plan.
    assert client.calls == 5
    # Each volume gets its own file with the correct canonical name.
    for n_cn in ["一", "二", "三", "四", "五"]:
        f = tmp_path / "大纲" / f"卷纲_第{n_cn}卷.md"
        assert f.exists(), f"missing {f.name}"
        assert f.read_text(encoding="utf-8").startswith(f"## 第{n_cn}卷")
    assert len(result["outputs"]) == 5
    assert result["plan"][0]["ch_start"] == 1
    assert result["plan"][-1]["ch_end"] == 30
    # Each prompt should reference its own volume number, not just volume 1.
    vol_marker_counts = {
        cn: sum(1 for p in client.prompts if f"【第{cn}卷】" in p)
        for cn in ["一", "二", "三", "四", "五"]
    }
    assert all(v == 1 for v in vol_marker_counts.values()), vol_marker_counts


def test_extract_volume_outlines_splits_h1_multi_volume_dump() -> None:
    """AI sometimes ignores '## 第N卷' and dumps all volumes under H1.

    The splitter must still recognise '# 卷纲·第N卷：...' as a real volume
    heading while ignoring generic doc titles like '# 全书卷纲' and pseudo
    file-name titles like '# 大纲/卷纲_第一卷.md'.
    """
    text = (
        "# 全书卷纲\n\n"
        "# 大纲/卷纲_第一卷.md\n"
        "# 卷纲·第一卷：「开端」\n- 内容A\n\n"
        "# 卷纲·第二卷：「中段」\n- 内容B\n\n"
        "# 卷纲·第三卷：「收官」\n- 内容C\n"
    )
    volumes = _extract_volume_outlines(text)
    assert [v[0] for v in volumes] == [1, 2, 3]
    assert "内容A" in volumes[0][1]
    assert "内容B" in volumes[1][1]
    assert "内容C" in volumes[2][1]
    # Preface lines (全书卷纲, 大纲/卷纲_第一卷.md) should be attached to v1.
    assert "全书卷纲" in volumes[0][1]
    assert "大纲/卷纲_第一卷.md" in volumes[0][1]
