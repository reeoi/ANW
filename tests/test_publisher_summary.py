"""Phase E publisher summary integration tests.

Verifies ``publisher.fansq.FansqPublisher`` consumes ``story.summary``
in addition to ``story.title`` (decision: publisher 读 final_title/summary):

- dry-run success path includes the summary character count in its
  message (or "无简介" when the field is empty).
- dry-run paused outcome still pauses regardless of summary state.
- Live path with a fake page: when the page exposes a 简介 selector,
  ``summary_box.fill(story.summary)`` is invoked and the pause message
  reports the simulated fill.
- Live path with summary but no 简介 selector: pause message asks the
  human to paste the summary.
- Live path with empty summary: no summary fill attempt is made and
  the pause message notes "无简介".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from publisher.base_publisher import PublishStatus
from publisher.fansq import FansqPublisher
from review_queue.models import Story


# ============================================================ helpers


def _config(
    tmp_path: Path,
    *,
    dry_run: bool = True,
    enabled: bool = True,
    login_state_present: bool = True,
) -> LoadedConfig:
    state_dir = tmp_path / "browser"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "fansq_state.json"
    if login_state_present:
        state_path.write_text("{}", encoding="utf-8")
    return LoadedConfig(
        data={
            "deepseek": {"mock": True, "api_key": ""},
            "runtime": {"dry_run": dry_run, "headless": True},
            "publisher": {
                "default_platform": "fansq",
                "fansq": {
                    "enabled": enabled,
                    "username": "test",
                    "login_state_path": str(state_path),
                    "draft_url": "https://fanqienovel.com/",
                    "min_publish_interval_minutes": 5,
                    "max_publish_interval_minutes": 15,
                    "pause_on_risk_control": True,
                },
            },
            "logging": {
                "level": "INFO",
                "file": str(tmp_path / "anp.log"),
                "screenshot_dir": str(tmp_path / "screens"),
            },
        },
        path=Path("fansq.yaml"),
    )


@dataclass
class FakeLocator:
    visible: bool = False
    text: str = ""
    fill_calls: list[str] = field(default_factory=list)
    type_calls: list[str] = field(default_factory=list)
    click_count: int = 0

    @property
    def first(self) -> "FakeLocator":
        return self

    def is_visible(self, timeout: float = 0.0) -> bool:
        return self.visible

    def fill(self, text: str) -> None:
        self.fill_calls.append(text)

    def click(self, timeout: float = 0.0) -> None:
        self.click_count += 1

    def type(self, text: str, delay: int = 0) -> None:
        self.type_calls.append(text)

    def inner_text(self, timeout: float = 0.0) -> str:
        return self.text


@dataclass
class FakePage:
    selectors: dict[str, FakeLocator] = field(default_factory=dict)
    body_text: str = "番茄发布编辑器"
    url: str = "https://fanqienovel.com/draft"
    goto_calls: list[str] = field(default_factory=list)
    screenshot_calls: list[str] = field(default_factory=list)

    def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        self.goto_calls.append(url)

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(visible=True, text=self.body_text)
        return self.selectors.get(selector, FakeLocator(visible=False))

    def screenshot(self, *, path: str | None = None, full_page: bool = False) -> None:
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"fake-screenshot")
            self.screenshot_calls.append(path)

    def wait_for_timeout(self, ms: int) -> None:
        """Playwright sync API: pause for ms milliseconds. No-op in tests."""
        return None

    def evaluate(self, expression: str, *args: object) -> None:
        """Playwright JS evaluate: no-op stub for tests."""
        return None

    @property
    def keyboard(self) -> "FakeKeyboard":
        if not hasattr(self, "_keyboard"):
            self._keyboard = FakeKeyboard()
        return self._keyboard


@dataclass
class FakeKeyboard:
    pressed: list[str] = field(default_factory=list)

    def press(self, key: str) -> None:
        self.pressed.append(key)


def _patch_browser(monkeypatch: pytest.MonkeyPatch, page: FakePage) -> None:
    monkeypatch.setattr(FansqPublisher, "start_browser", lambda self: page)
    monkeypatch.setattr(FansqPublisher, "close", lambda self: None)


def _approved_story(
    tmp_path: Path,
    *,
    summary: str | None = "丈夫给情人买学区房，三个月后我把房本写在自己名下。",
    body: str = "正文" * 1500,
) -> Story:
    final_path = tmp_path / "works" / "1" / "5_最终稿.md"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(body, encoding="utf-8")
    return Story(
        id=1,
        title="丈夫给情人买学区房，我连夜做了三件事",
        status="approved",
        current_phase="phase_5_done",
        work_dir=str(final_path.parent),
        final_content_path=str(final_path),
        summary=summary,
        emotion="意难平",
        target_length=10000,
    )


# ============================================================ dry-run path


def test_dry_run_success_message_includes_summary_chars(tmp_path: Path) -> None:
    cfg = _config(tmp_path, dry_run=True)
    story = _approved_story(tmp_path)
    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=True, dry_run_outcome="success")
    assert result.status == PublishStatus.PUBLISHED
    summary_chars = len((story.summary or "").strip())
    assert f"简介 {summary_chars} 字" in result.message
    assert story.title in result.message


def test_dry_run_success_message_handles_missing_summary(tmp_path: Path) -> None:
    cfg = _config(tmp_path, dry_run=True)
    story = _approved_story(tmp_path, summary=None)
    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=True, dry_run_outcome="success")
    assert result.status == PublishStatus.PUBLISHED
    assert "无简介" in result.message


def test_dry_run_paused_outcome_short_circuits_summary(tmp_path: Path) -> None:
    cfg = _config(tmp_path, dry_run=True)
    story = _approved_story(tmp_path)
    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=True, dry_run_outcome="paused")
    assert result.status == PublishStatus.PAUSED


# ============================================================ live path (mocked)


def test_live_path_fills_summary_into_简介_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, dry_run=False)
    title_loc = FakeLocator(visible=True)
    content_loc = FakeLocator(visible=True)
    summary_loc = FakeLocator(visible=True)
    page = FakePage(
        selectors={
            "textarea.byte-textarea.serial-textarea": title_loc,
            "div.ProseMirror": content_loc,
            "textarea[placeholder*='简介']": summary_loc,
        }
    )
    _patch_browser(monkeypatch, page)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
    story = _approved_story(tmp_path)

    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=False)
    # After successful fill + manual confirmation, status is PUBLISHED.
    assert result.status == PublishStatus.PUBLISHED
    assert title_loc.fill_calls == [story.title]
    assert summary_loc.fill_calls == [story.summary]


def test_live_path_summary_present_but_no_selector_asks_human_to_paste(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, dry_run=False)
    title_loc = FakeLocator(visible=True)
    content_loc = FakeLocator(visible=True)
    captured: dict[str, str] = {}

    def fake_input(prompt: str = "") -> str:
        captured["prompt"] = prompt
        return ""

    page = FakePage(
        selectors={
            "textarea.byte-textarea.serial-textarea": title_loc,
            "div.ProseMirror": content_loc,
        }
    )
    _patch_browser(monkeypatch, page)
    monkeypatch.setattr("builtins.input", fake_input)
    story = _approved_story(tmp_path)

    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=False)
    assert result.status == PublishStatus.PUBLISHED
    assert "请人工粘贴" in captured.get("prompt", "")


def test_live_path_no_summary_skips_summary_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, dry_run=False)
    title_loc = FakeLocator(visible=True)
    content_loc = FakeLocator(visible=True)
    summary_loc = FakeLocator(visible=True)
    captured: dict[str, str] = {}

    def fake_input(prompt: str = "") -> str:
        captured["prompt"] = prompt
        return ""

    page = FakePage(
        selectors={
            "textarea.byte-textarea.serial-textarea": title_loc,
            "div.ProseMirror": content_loc,
            "textarea[placeholder*='简介']": summary_loc,
        }
    )
    _patch_browser(monkeypatch, page)
    monkeypatch.setattr("builtins.input", fake_input)
    story = _approved_story(tmp_path, summary=None)

    publisher = FansqPublisher(cfg)
    result = publisher.publish_story(story, dry_run=False)
    assert result.status == PublishStatus.PUBLISHED
    # Summary selector should not have been touched when story.summary is empty.
    assert summary_loc.fill_calls == []
    assert "无简介" in captured.get("prompt", "")
