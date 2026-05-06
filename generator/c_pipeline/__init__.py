"""C-pipeline multi-phase short-story generation (PLAN §3.1, §4).

Six-phase orchestrator that turns one ``theme_pool.json`` item into an
8000-15000 character 番茄短篇 manuscript:

- Phase 0 选题  (pro)             → 0_选题.json
- Phase 1 框架  (pro+thinking)    → 1_设定.md
- Phase 2 大纲  (pro+thinking)    → 2_小节大纲.md
- Phase 3 逐节  (pro, full prior) → 3_正文_第 N 节.md ×N + 3_正文_合稿.md
- Phase 4 精修  (pro+thinking)    → 4_精修稿.md
- Phase 5 去 AI 味 (pro)         → 5_最终稿.md (= final_content_path)

All artifacts land in ``data/works/{story_id}/`` and the SQLite
``stories.current_phase`` advances after each phase. Per-section / per-phase
validators live in ``validators.py``; cost telemetry + budget-driven flash
downgrade live in ``cost_tracker.py``; K2 inter-pipeline concurrency lives in
``concurrency.py``.
"""

from __future__ import annotations

__all__: list[str] = []
