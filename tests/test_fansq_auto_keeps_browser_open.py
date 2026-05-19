"""TDD: publisher/fansq_auto.py must NOT auto-close the Playwright browser.

User feedback: "发布时不确定有没有发布成功，页面就自动关闭了" — the
``finally`` block previously called ``self.browser.close()`` and
``self.playwright.stop()`` unconditionally, leaving the user no chance to
verify on Fanqie's page whether the post actually went out.

Q4=A in the design grilling: keep browser open after publish, user closes
the window themselves. These tests assert that contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from publisher.fansq_auto import FansqAutoPublisher, PublishConfig


@pytest.fixture()
def cfg() -> PublishConfig:
    return PublishConfig(
        story_id=1,
        title="测试标题",
        content="测试正文" * 20,
        summary="测试简介",
    )


def _mocked_publisher() -> FansqAutoPublisher:
    """Build a FansqAutoPublisher with all browser-side methods stubbed."""
    pub = FansqAutoPublisher(headless=True)
    pub.start_browser = MagicMock()  # type: ignore[method-assign]
    pub.close_tutorials = MagicMock()  # type: ignore[method-assign]
    pub.fill_title = MagicMock()  # type: ignore[method-assign]
    pub.fill_content = MagicMock()  # type: ignore[method-assign]
    pub.generate_cover = MagicMock(return_value=None)  # type: ignore[method-assign]
    pub.upload_cover = MagicMock()  # type: ignore[method-assign]
    pub.scroll_to_bottom = MagicMock()  # type: ignore[method-assign]
    pub.set_use_ai = MagicMock()  # type: ignore[method-assign]
    pub.set_category = MagicMock()  # type: ignore[method-assign]
    pub.set_trial_ratio = MagicMock()  # type: ignore[method-assign]
    pub.check_publish_agreement = MagicMock()  # type: ignore[method-assign]
    pub.click_publish = MagicMock(return_value=True)  # type: ignore[method-assign]
    # 朱雀 gate stub —— 单测不调真实朱雀页面
    pub._run_zhuque_gate = MagicMock(return_value={"ok": True, "message": "test stub"})  # type: ignore[method-assign]
    # Browser / playwright stubs that track close/stop calls
    pub.browser = MagicMock()
    pub.playwright = MagicMock()
    pub.page = MagicMock()
    # Mock locator chain used in step 1 (查草稿)
    pub.page.locator = MagicMock(return_value=MagicMock(count=lambda: 0))
    return pub


def test_publish_does_not_close_browser_on_success(cfg: PublishConfig) -> None:
    pub = _mocked_publisher()
    pub.publish(cfg)
    assert pub.browser.close.call_count == 0, "浏览器不能自动关闭：用户需要在番茄页面确认发布结果"
    assert pub.playwright.stop.call_count == 0, "Playwright 不能自动 stop()：会强制关闭浏览器"


def test_publish_does_not_close_browser_on_exception(cfg: PublishConfig) -> None:
    pub = _mocked_publisher()
    pub.fill_title = MagicMock(side_effect=RuntimeError("模拟填标题失败"))  # type: ignore[method-assign]
    pub.publish(cfg)  # publish 本身吞掉异常，进 finally
    assert pub.browser.close.call_count == 0, "失败也不能关浏览器：用户需要在番茄页面看具体哪步出错"
    assert pub.playwright.stop.call_count == 0
