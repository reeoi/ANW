"""Long novel (长篇小说) multi-phase writing system.

Parallel to ``generator.c_pipeline`` for short stories. Provides:

- Database schema for books/volumes/chapters
- L0 book creation pipeline (benchmark → premise → world → characters → outline)
- L1 volume planning
- L2 chapter writing loop with context assembly
- L3 chapter rewrite/backfill with cascade consistency check
- L4 4-dimension AI review
"""
