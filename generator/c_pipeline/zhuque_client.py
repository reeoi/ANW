"""朱雀 AI 检测客户端。

通过 CDP 复用本机 Chrome 自动化访问 https://matrix.tencent.com/ai-detect/
完成 AI 检测。朱雀没有公开 API，所以这是基于浏览器自动化的方案。

selector 是基于经验 + 多候选 fallback 设计的，朱雀页面改版后请运行
``tools/zhuque_probe.py`` 重新摸底，把新的稳定 selector 加到对应列表。

合规：
- 不绕过登录 / 验证码 / 滑块；任何异常都返回 anomaly，由上层人工介入
- 不存储朱雀账号 / 密码；用户在 Chrome 中自行登录
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from publisher.chrome_launcher import DEFAULT_CDP_PORT, ensure_chrome

logger = logging.getLogger(__name__)

ZHUQUE_URL = "https://matrix.tencent.com/ai-detect/"


class ZhuqueLabel(StrEnum):
    """朱雀检测返回的离散人工创作特征标签。"""

    SIGNIFICANT = "人工创作特征显著"
    AVERAGE = "人工创作特征一般"
    INSIGNIFICANT = "人工创作特征不显著"
    UNKNOWN = "unknown"


class ZhuqueAnomaly(StrEnum):
    """朱雀检测异常分类（不是"AI 率高"，是"朱雀本身不可用"）。"""

    PAGE_CHANGED = "page_changed"
    CAPTCHA = "captcha"
    NETWORK_TIMEOUT = "network_timeout"
    PARSE_FAILED = "parse_failed"
    OVER_LIMIT = "over_limit"
    NOT_LOGGED_IN = "not_logged_in"
    CHROME_UNAVAILABLE = "chrome_unavailable"


@dataclass(frozen=True)
class ZhuqueResult:
    """单次朱雀检测的结果。

    - 成功（``anomaly is None``）：``label`` / ``ai_probability`` 可信
    - 异常（``anomaly is not None``）：``label=UNKNOWN``，``message`` 描述问题
    """

    label: ZhuqueLabel
    ai_probability: float | None
    highlights: list[str]
    raw_text: str
    screenshot_path: Path | None
    anomaly: ZhuqueAnomaly | None
    message: str

    @property
    def passed(self) -> bool:
        """是否通过 gate（仅当 label = 人工创作特征显著 且无异常）。"""
        return self.anomaly is None and self.label == ZhuqueLabel.SIGNIFICANT

    @property
    def ok(self) -> bool:
        """检测本身是否成功（不一定通过 gate）。"""
        return self.anomaly is None


# selector 候选（按优先级排序，第一个 visible 命中即用）
TEXTAREA_SELECTORS = [
    "textarea[placeholder*='输入']",
    "textarea[placeholder*='检测']",
    "textarea[placeholder*='文本']",
    "textarea[placeholder*='粘贴']",
    ".ai-detect-textarea textarea",
    "[class*='textarea'] textarea",
    "textarea",
]

SUBMIT_SELECTORS = [
    "button:has-text('开始检测')",
    "button:has-text('立即检测')",
    "button:has-text('检测')",
    "button.detect-btn",
    "[class*='detect'] button.primary",
    "button[type='submit']",
]

LOGIN_SELECTORS = [
    "button:has-text('登录')",
    "button:has-text('立即登录')",
    "a:has-text('登录')",
]

CAPTCHA_SELECTORS = [
    "[class*='captcha']",
    "[class*='slider']",
    "[class*='verify']",
    "[class*='tcaptcha']",
]

# 等待结果出现的页面信号（一旦命中说明结果就绪）
RESULT_READY_SELECTORS = [
    ":text-matches(r'人工创作特征(显著|一般|不显著)')",
    ":text-matches(r'AI[创作]*特征(显著|一般|不显著)')",
    "[class*='result-card']",
    "[class*='detect-result']",
]


def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 1500) -> Any | None:
    """按优先级返回首个 visible 的 locator。"""
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0 and locator.is_visible(timeout=timeout_ms):
                return locator
        except Exception:
            continue
    return None


def _parse_label(raw_text: str) -> ZhuqueLabel:
    """从页面文字里抓"人工创作特征 X"标签。"""
    if "显著" in raw_text and "人工创作特征显著" in raw_text:
        return ZhuqueLabel.SIGNIFICANT
    if "不显著" in raw_text and "人工创作特征不显著" in raw_text:
        return ZhuqueLabel.INSIGNIFICANT
    if "一般" in raw_text and "人工创作特征一般" in raw_text:
        return ZhuqueLabel.AVERAGE
    # 兜底：松匹配（朱雀文案微调时不彻底崩）
    if "人工创作" in raw_text:
        if "显著" in raw_text:
            return ZhuqueLabel.SIGNIFICANT
        if "不显著" in raw_text:
            return ZhuqueLabel.INSIGNIFICANT
        if "一般" in raw_text:
            return ZhuqueLabel.AVERAGE
    return ZhuqueLabel.UNKNOWN


def _parse_ai_probability(raw_text: str) -> float | None:
    """抓 'AI 概率: 42%' 之类百分比。"""
    import re

    patterns = [
        r"AI[^0-9]{0,10}(\d{1,3}(?:\.\d+)?)\s*%",
        r"机器创作[^0-9]{0,10}(\d{1,3}(?:\.\d+)?)\s*%",
        r"AI率[^0-9]{0,10}(\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pat in patterns:
        m = re.search(pat, raw_text)
        if m:
            try:
                val = float(m.group(1))
                if 0 <= val <= 100:
                    return val / 100.0
            except ValueError:
                continue
    return None


class ZhuqueClient:
    """朱雀检测客户端。一次性创建即可复用，每次 detect 都会去找/复用浏览器 tab。"""

    DEFAULT_CHAR_LIMIT = 2000

    def __init__(
        self,
        cdp_port: int = DEFAULT_CDP_PORT,
        screenshot_dir: Path | str | None = None,
        char_limit: int = DEFAULT_CHAR_LIMIT,
        wait_result_seconds: float = 30.0,
    ) -> None:
        self.cdp_port = int(cdp_port)
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else Path("logs/zhuque")
        self.char_limit = int(char_limit)
        self.wait_result_seconds = float(wait_result_seconds)

    def detect(self, text: str, story_id: int | None = None) -> ZhuqueResult:
        """单次检测。文本超出字数上限时自动分段（最差段）。"""
        if not text:
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=None,
                anomaly=ZhuqueAnomaly.PARSE_FAILED,
                message="输入文本为空",
            )

        # 字数超限：分段取最差
        if len(text) > self.char_limit:
            return self._detect_long(text, story_id=story_id)

        endpoint = ensure_chrome(port=self.cdp_port)
        if endpoint is None:
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=None,
                anomaly=ZhuqueAnomaly.CHROME_UNAVAILABLE,
                message="无法连接或启动 Chrome（CDP 端口不可达）",
            )

        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(endpoint.http_url)
                contexts = browser.contexts
                ctx = contexts[0] if contexts else browser.new_context()
                page = self._find_or_open_zhuque(ctx)
                return self._detect_on_page(page, text, story_id=story_id)
        except Exception as exc:
            logger.exception("zhuque_detect_unexpected_error story_id=%s", story_id)
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=None,
                anomaly=ZhuqueAnomaly.NETWORK_TIMEOUT,
                message=f"{exc.__class__.__name__}: {exc}",
            )

    def _find_or_open_zhuque(self, ctx: Any) -> Page:
        for page in list(ctx.pages):
            try:
                if "matrix.tencent.com" in page.url:
                    page.bring_to_front()
                    return page
            except Exception:
                continue
        page = ctx.new_page()
        page.goto(ZHUQUE_URL, wait_until="networkidle", timeout=60_000)
        return page

    def _detect_on_page(
        self, page: Page, text: str, story_id: int | None
    ) -> ZhuqueResult:
        # 1. 登录检测
        login_locator = _first_visible(page, LOGIN_SELECTORS, timeout_ms=800)
        if login_locator is not None and self._looks_unauthenticated(page):
            shot = self._snapshot(page, story_id, "not_logged_in")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.NOT_LOGGED_IN,
                message="朱雀检测要求登录。请在 Chrome 中完成登录后重试。",
            )

        # 2. 验证码 / 滑块检测
        captcha_locator = _first_visible(page, CAPTCHA_SELECTORS, timeout_ms=500)
        if captcha_locator is not None:
            shot = self._snapshot(page, story_id, "captcha")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.CAPTCHA,
                message="检测到验证码 / 滑块。请在 Chrome 中完成验证后重试。",
            )

        # 3. 找输入框
        textarea = _first_visible(page, TEXTAREA_SELECTORS, timeout_ms=2000)
        if textarea is None:
            shot = self._snapshot(page, story_id, "no_textarea")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PAGE_CHANGED,
                message="未找到朱雀文本输入框（页面可能已改版）",
            )

        # 4. 清空 + 填入文本
        try:
            textarea.click(timeout=3000)
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
            textarea.fill(text)
        except Exception as exc:
            shot = self._snapshot(page, story_id, "fill_failed")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PAGE_CHANGED,
                message=f"填入文本失败：{exc}",
            )

        # 5. 找提交按钮
        submit = _first_visible(page, SUBMIT_SELECTORS, timeout_ms=2000)
        if submit is None:
            shot = self._snapshot(page, story_id, "no_submit")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PAGE_CHANGED,
                message="未找到朱雀检测提交按钮",
            )

        # 6. 点击提交，等结果
        try:
            submit.click(timeout=3000)
        except Exception as exc:
            shot = self._snapshot(page, story_id, "submit_click_failed")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PAGE_CHANGED,
                message=f"提交按钮点击失败：{exc}",
            )

        # 7. 等结果出现（朱雀通常 3-10 秒返回）
        result_locator = None
        deadline = time.time() + self.wait_result_seconds
        while time.time() < deadline:
            # 进度中可能再次出现验证码
            if _first_visible(page, CAPTCHA_SELECTORS, timeout_ms=200) is not None:
                shot = self._snapshot(page, story_id, "captcha_during_detect")
                return ZhuqueResult(
                    label=ZhuqueLabel.UNKNOWN,
                    ai_probability=None,
                    highlights=[],
                    raw_text="",
                    screenshot_path=shot,
                    anomaly=ZhuqueAnomaly.CAPTCHA,
                    message="检测过程中出现验证码 / 滑块",
                )
            result_locator = _first_visible(page, RESULT_READY_SELECTORS, timeout_ms=500)
            if result_locator is not None:
                break
            time.sleep(0.5)

        if result_locator is None:
            shot = self._snapshot(page, story_id, "result_timeout")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.NETWORK_TIMEOUT,
                message=f"等待朱雀结果超时（{self.wait_result_seconds}s）",
            )

        # 8. 抓结果文本
        try:
            body_text = page.locator("body").inner_text(timeout=5_000)
        except Exception as exc:
            shot = self._snapshot(page, story_id, "read_body_failed")
            return ZhuqueResult(
                label=ZhuqueLabel.UNKNOWN,
                ai_probability=None,
                highlights=[],
                raw_text="",
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PARSE_FAILED,
                message=f"读取页面文字失败：{exc}",
            )

        label = _parse_label(body_text)
        ai_prob = _parse_ai_probability(body_text)
        shot = self._snapshot(page, story_id, f"done_{label.name.lower()}")

        if label == ZhuqueLabel.UNKNOWN:
            return ZhuqueResult(
                label=label,
                ai_probability=ai_prob,
                highlights=[],
                raw_text=body_text[:3000],
                screenshot_path=shot,
                anomaly=ZhuqueAnomaly.PARSE_FAILED,
                message="结果文本中未匹配到任一标签（朱雀文案可能已变更）",
            )

        return ZhuqueResult(
            label=label,
            ai_probability=ai_prob,
            highlights=[],
            raw_text=body_text[:3000],
            screenshot_path=shot,
            anomaly=None,
            message=f"{label.value}（AI 率 {f'{ai_prob:.1%}' if ai_prob is not None else 'N/A'}）",
        )

    def _detect_long(self, text: str, story_id: int | None) -> ZhuqueResult:
        """字数超上限时分两段检测，取更差结果（按 label 优先级）。"""
        mid = len(text) // 2
        # 在最近的句子分隔符附近切，避免截在词中间
        for cut in range(mid, min(mid + 200, len(text))):
            if text[cut] in "。！？\n":
                mid = cut + 1
                break
        first_half = text[:mid]
        second_half = text[mid:]
        r1 = self.detect(first_half, story_id=story_id)
        r2 = self.detect(second_half, story_id=story_id)
        # 异常优先返回（让上层人工介入）
        if r1.anomaly is not None:
            return r1
        if r2.anomaly is not None:
            return r2
        # 选更差的（不显著 > 一般 > 显著）
        rank = {
            ZhuqueLabel.INSIGNIFICANT: 0,
            ZhuqueLabel.AVERAGE: 1,
            ZhuqueLabel.SIGNIFICANT: 2,
            ZhuqueLabel.UNKNOWN: -1,
        }
        worse = r1 if rank.get(r1.label, -1) <= rank.get(r2.label, -1) else r2
        return ZhuqueResult(
            label=worse.label,
            ai_probability=max(
                (r.ai_probability for r in (r1, r2) if r.ai_probability is not None),
                default=None,
            ),
            highlights=worse.highlights,
            raw_text=f"[分段1]\n{r1.raw_text}\n\n[分段2]\n{r2.raw_text}",
            screenshot_path=worse.screenshot_path,
            anomaly=None,
            message=f"分段检测：{r1.label.value} + {r2.label.value} → 取更差 {worse.label.value}",
        )

    def _looks_unauthenticated(self, page: Page) -> bool:
        """朱雀首页登录按钮在悬浮 header；登录后变成头像。靠提交按钮是否能跑判断。"""
        # 提交按钮可见 → 视为已登录（最稳妥的启发式）
        submit = _first_visible(page, SUBMIT_SELECTORS, timeout_ms=500)
        return submit is None

    def _snapshot(self, page: Page, story_id: int | None, tag: str) -> Path | None:
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            sid = f"story_{story_id}_" if story_id else ""
            path = self.screenshot_dir / f"{sid}{tag}_{ts}.png"
            page.screenshot(path=str(path), full_page=True)
            return path
        except Exception as exc:
            logger.debug("zhuque_snapshot_failed tag=%s error=%s", tag, exc)
            return None


__all__ = [
    "ZHUQUE_URL",
    "ZhuqueAnomaly",
    "ZhuqueClient",
    "ZhuqueLabel",
    "ZhuqueResult",
]
