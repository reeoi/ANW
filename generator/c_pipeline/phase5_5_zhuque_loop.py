"""Phase 5.5 — 朱雀 AI 检测闭环。

在 phase5（规则去 slop）之后、phase6（章节切分）之前插入。流程：

1. 朱雀检测当前 5_最终稿.md
2. label == "人工创作特征显著" → 通过，写出 ``5_5_朱雀通过稿.md``，结束
3. 朱雀异常（page_changed / captcha / network_timeout / parse_failed / ...）
   → 抛 ``ZhuqueAnomalyError``，由 orchestrator 标 ``paused_zhuque_anomaly``
4. label != 显著 → LLM 整篇重写（同 phase4 模型），喂入上一轮检测结果作为
   重点指令 → 重新检测
5. 最多 3 轮，3 轮后仍不显著 → 抛 ``ZhuqueRejectedError``，由 orchestrator
   标 ``rejected_ai``

每轮检测落 ``logs/zhuque/<story_id>_round_<N>.json``。
"""

from __future__ import annotations

import json
import logging
import string
import time
from dataclasses import dataclass, field
from pathlib import Path

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, DeepSeekClient
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.validators import count_chinese_chars
from generator.c_pipeline.zhuque_client import (
    ZhuqueAnomaly,
    ZhuqueClient,
    ZhuqueLabel,
    ZhuqueResult,
)

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).parent / "prompts" / "phase5_5_zhuque_rewrite.txt"

DEFAULT_MAX_ROUNDS = 3


class Phase5_5Error(RuntimeError):
    """Phase 5.5 基类异常。"""


class ZhuqueAnomalyError(Phase5_5Error):
    """朱雀本身用不了（页面改版 / 验证码 / 超时等）。需要人工介入。"""

    def __init__(self, anomaly: ZhuqueAnomaly, message: str, screenshot: Path | None = None):
        super().__init__(message)
        self.anomaly = anomaly
        self.screenshot = screenshot


class ZhuqueRejectedError(Phase5_5Error):
    """重试 N 轮后仍未通过 gate。视为弃稿（rejected_ai）。"""

    def __init__(self, rounds: int, last_label: ZhuqueLabel):
        super().__init__(f"朱雀检测 {rounds} 轮后仍未达「人工创作特征显著」，最后一轮 = {last_label.value}")
        self.rounds = rounds
        self.last_label = last_label


@dataclass(frozen=True)
class Phase5_5Round:
    """单轮检测 + 重写记录。"""

    round_index: int
    detect_result: ZhuqueResult
    rewrite_completion: ChatCompletion | None
    input_chars: int
    input_hash: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "round": self.round_index,
            "ts": self.timestamp,
            "label": self.detect_result.label.value,
            "ai_prob": self.detect_result.ai_probability,
            "anomaly": (self.detect_result.anomaly.value if self.detect_result.anomaly else None),
            "message": self.detect_result.message,
            "input_chars": self.input_chars,
            "input_hash": self.input_hash,
            "screenshot": (
                str(self.detect_result.screenshot_path)
                if self.detect_result.screenshot_path else None
            ),
            "rewrote": self.rewrite_completion is not None,
        }


@dataclass(frozen=True)
class Phase5_5Result:
    """Phase 5.5 完整产物。"""

    final_md: str
    final_path: Path
    char_count: int
    rounds: list[Phase5_5Round] = field(default_factory=list)
    passed: bool = False


def _input_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _build_rewrite_messages(
    *,
    last_md: str,
    last_result: ZhuqueResult,
) -> list[dict[str, str]]:
    template = string.Template(_PROMPT_FILE.read_text(encoding="utf-8"))
    ai_prob_hint = ""
    if last_result.ai_probability is not None:
        ai_prob_hint = f"上一稿朱雀返回 AI 率约 {last_result.ai_probability:.1%}，目标是把这个数字压下去。"
    user_text = template.safe_substitute(
        last_label=last_result.label.value,
        ai_prob_hint=ai_prob_hint,
        last_md=last_md,
    )
    return [
        {
            "role": "system",
            "content": (
                "你是中文短篇网文资深'去 AI 味'编辑。"
                "重写时只调整语言风格、句法节奏、感官细节，严禁增删情节段落、改人物名/物件名/数字。"
                "不要输出任何元说明，直接产出最终稿全文。"
            ),
        },
        {"role": "user", "content": user_text},
    ]


def run_zhuque_loop(
    config: LoadedConfig,
    *,
    work_dir: Path,
    story_id: int | None,
    polished_path: Path | None = None,
    client: DeepSeekClient | None = None,
    cost_tracker: CostTracker | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    zhuque_client: ZhuqueClient | None = None,
) -> Phase5_5Result:
    """运行朱雀检测闭环。

    Raises:
        ZhuqueAnomalyError: 朱雀检测异常，需要人工介入。
        ZhuqueRejectedError: ``max_rounds`` 轮后仍未达「显著」。
    """
    work_dir = Path(work_dir)
    polished_path = Path(polished_path) if polished_path else work_dir / "5_最终稿.md"
    if not polished_path.exists():
        raise Phase5_5Error(
            f"Phase 5.5 需要 Phase 5 输出但 5_最终稿.md 缺失：{polished_path}"
        )

    current_md = polished_path.read_text(encoding="utf-8")

    if zhuque_client is None:
        zhuque_client = ZhuqueClient(
            screenshot_dir=Path("logs/zhuque/screenshots"),
        )

    if client is None:
        client = DeepSeekClient(config)

    log_dir = Path("logs/zhuque")
    log_dir.mkdir(parents=True, exist_ok=True)

    rounds: list[Phase5_5Round] = []

    for round_index in range(1, max_rounds + 1):
        logger.info(
            "phase5_5_round story_id=%s round=%s chars=%s",
            story_id, round_index, count_chinese_chars(current_md),
        )

        detect_result = zhuque_client.detect(current_md, story_id=story_id)
        round_record = Phase5_5Round(
            round_index=round_index,
            detect_result=detect_result,
            rewrite_completion=None,
            input_chars=count_chinese_chars(current_md),
            input_hash=_input_hash(current_md),
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        rounds.append(round_record)

        # 落盘当前轮记录
        record_path = log_dir / (
            f"story_{story_id}_round_{round_index}.json"
            if story_id is not None
            else f"round_{round_index}_{int(time.time())}.json"
        )
        record_path.write_text(
            json.dumps(round_record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 1. 异常 → 抛 ZhuqueAnomalyError
        if detect_result.anomaly is not None:
            raise ZhuqueAnomalyError(
                detect_result.anomaly,
                detect_result.message,
                screenshot=detect_result.screenshot_path,
            )

        # 2. 通过 → 写最终稿 + 返回
        if detect_result.passed:
            final_path = work_dir / "5_5_朱雀通过稿.md"
            final_path.write_text(current_md, encoding="utf-8")
            logger.info(
                "phase5_5_passed story_id=%s rounds=%s label=%s",
                story_id, round_index, detect_result.label.value,
            )
            return Phase5_5Result(
                final_md=current_md,
                final_path=final_path,
                char_count=count_chinese_chars(current_md),
                rounds=rounds,
                passed=True,
            )

        # 3. 未通过 → 重写（除非已达 max_rounds，那就跳出循环抛 rejected）
        if round_index == max_rounds:
            break

        messages = _build_rewrite_messages(last_md=current_md, last_result=detect_result)
        completion = client.chat_completion(
            messages,
            thinking_mode=False,
            purpose="phase_5_5_rewrite",
        )
        if cost_tracker is not None:
            cost_tracker.record_completion(
                story_id=story_id or 0,
                phase="phase_5_5",
                completion=completion,
            )
        rewritten = (completion.text or "").strip()
        if not rewritten:
            logger.warning(
                "phase5_5_rewrite_empty story_id=%s round=%s", story_id, round_index
            )
            # 空响应 → 直接抛 anomaly（认为是 LLM 异常，转人工）
            raise ZhuqueAnomalyError(
                ZhuqueAnomaly.PARSE_FAILED,
                f"第 {round_index} 轮 LLM 重写返回空响应",
            )

        # 用重写产物更新最新记录的 rewrite_completion
        rounds[-1] = Phase5_5Round(
            round_index=round_record.round_index,
            detect_result=round_record.detect_result,
            rewrite_completion=completion,
            input_chars=round_record.input_chars,
            input_hash=round_record.input_hash,
            timestamp=round_record.timestamp,
        )
        record_path.write_text(
            json.dumps(rounds[-1].to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        current_md = rewritten

    # 用尽轮数仍不通过
    last_label = rounds[-1].detect_result.label if rounds else ZhuqueLabel.UNKNOWN
    logger.warning(
        "phase5_5_rejected story_id=%s rounds=%s last_label=%s",
        story_id, max_rounds, last_label.value,
    )
    raise ZhuqueRejectedError(rounds=max_rounds, last_label=last_label)


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "Phase5_5Error",
    "Phase5_5Result",
    "Phase5_5Round",
    "ZhuqueAnomalyError",
    "ZhuqueRejectedError",
    "run_zhuque_loop",
]
