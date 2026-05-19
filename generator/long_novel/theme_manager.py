"""Shared theme pool manager — works for both short and long novels.

Data sources (weekly refresh):
1. scan_seeds.yaml → seed_evolver → theme_pool.json (existing pipeline)
2. FanqieRankTracker → trending rankings JSON (new importer)
3. Historical performance → which themes performed well (future)

Public API:
- get_trending_themes() → list of hot themes from the pool
- suggest_books() → AI-generated book suggestions based on themes
- import_fanqie_trends() → fetch and parse FanqieRankTracker data
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_THEME_POOL_PATH = _DATA_DIR / "theme_pool.json"
_FANQIE_CACHE_PATH = _DATA_DIR / "fanqie_trends.json"


# ── Theme Pool ────────────────────────────────────────────────────────


def get_theme_pool() -> list[dict[str, Any]]:
    """Load the current theme pool."""
    if not _THEME_POOL_PATH.exists():
        return []
    try:
        data = json.loads(_THEME_POOL_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", [])
    except (json.JSONDecodeError, OSError):
        return []
    return []


def get_trending_genres(top_n: int = 10) -> list[dict[str, Any]]:
    """Get trending genres with counts from the theme pool."""
    items = get_theme_pool()
    genre_counts: dict[str, int] = {}
    for item in items:
        g = item.get("genre", "unknown")
        genre_counts[g] = genre_counts.get(g, 0) + 1

    sorted_genres = sorted(genre_counts.items(), key=lambda x: -x[1])
    return [{"genre": g, "count": c, "label": _genre_label(g)} for g, c in sorted_genres[:top_n]]


def get_trending_emotions(top_n: int = 8) -> list[dict[str, Any]]:
    """Get trending emotions from the theme pool."""
    items = get_theme_pool()
    emotion_counts: dict[str, int] = {}
    for item in items:
        e = item.get("emotion", "unknown")
        emotion_counts[e] = emotion_counts.get(e, 0) + 1

    sorted_emotions = sorted(emotion_counts.items(), key=lambda x: -x[1])
    return [{"emotion": e, "count": c, "label": _emotion_label(e)} for e, c in sorted_emotions[:top_n]]


def get_hot_themes(limit: int = 20) -> list[dict[str, Any]]:
    """Get unconsumed themes sorted by recency."""
    items = get_theme_pool()
    unused = [i for i in items if i.get("consumed_count", 0) == 0]
    unused.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return unused[:limit]


# ── AI Book Suggestion ────────────────────────────────────────────────


def suggest_books(
    client: Any,  # DeepSeekClient
    target_type: str = "long",  # "short" or "long"
    count: int = 5,
) -> list[dict[str, Any]]:
    """Generate book suggestions based on trending themes in the pool.

    Args:
        client: DeepSeekClient instance
        target_type: "short" (短篇) or "long" (长篇)
        count: number of suggestions
    """
    themes = get_hot_themes(30)
    genres = get_trending_genres(6)
    emotions = get_trending_emotions(4)

    # Build context from pool
    theme_samples = []
    for t in themes[:15]:
        theme_samples.append(
            f"- {t.get('theme', '')} [{_genre_label(t.get('genre', ''))}] "
            f"情绪={_emotion_label(t.get('emotion', ''))} "
            f"标题参考={t.get('hint_title', '')}"
        )

    genre_summary = ", ".join(f"{g['label']}({g['count']})" for g in genres)
    emotion_summary = ", ".join(f"{e['label']}({e['count']})" for e in emotions)

    if target_type == "long":
        word_range = "每章2500-3500字，计划30章以上，总字数10万+"
        platforms = "番茄小说 / 起点中文网"
    else:
        word_range = "全文8000-15000字，7-12章"
        platforms = "番茄小说短篇"

    system = (
        "你是一位资深网文编辑和选题策划人。根据当前热门题材趋势，为作者推荐有爆款潜力的选题。"
        "输出必须是严格的JSON数组格式。"
    )
    user = f"""根据以下热门题材数据，推荐{count}个有潜力的{("长篇" if target_type == "long" else "短篇")}小说选题。

## 当前热门题材趋势
热门类型：{genre_summary}
热门情绪：{emotion_summary}
目标平台：{platforms}
字数要求：{word_range}

## 题材池热门样本
{chr(10).join(theme_samples[:12])}

## 输出要求
为每个推荐输出以下字段（严格JSON数组）：
[
  {{
    "title": "推荐书名（吸引人的网文书名）",
    "genre": "题材类型（如：玄幻/都市/仙侠/重生/悬疑等）",
    "premise": "一句话梗概（30-80字，突出核心卖点）",
    "emotion": "目标情绪（爽感释放/意难平/反转震撼/治愈温暖/细思极恐/共鸣感动）",
    "target_audience": "目标读者（如：男频25-35/女频20-30）",
    "trend_reason": "为什么现在适合写这个（30字内）",
    "difficulty": "easy/medium/hard"
  }}
]

只输出JSON数组，不要任何其他内容。"""

    suggestions: list[dict[str, Any]] = []
    try:
        from generator.api_client import DeepSeekClient, DeepSeekClientError
        completion = client.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            thinking_mode=True,
        )
        text = completion.text if hasattr(completion, "text") else str(completion)

        # Parse JSON
        try:
            parsed = json.loads(text.strip())
            if isinstance(parsed, list):
                suggestions = parsed
        except json.JSONDecodeError:
            pass

        # Try extracting JSON array
        if not suggestions:
            try:
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    suggestions = json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    except (DeepSeekClientError, Exception) as exc:
        logger.warning("AI suggest_books failed: %s", exc)

    if suggestions:
        return suggestions[:count]

    # Fallback: pick unconsumed themes directly from the pool
    logger.info("AI suggest returned empty — falling back to theme pool")
    unused = [t for t in themes if t.get("consumed_count", 0) == 0]
    fallback: list[dict[str, Any]] = []
    for t in unused[:count]:
        fallback.append({
            "title": t.get("hint_title") or t.get("theme", "")[:30],
            "genre": _genre_label(t.get("genre", "")),
            "premise": t.get("theme", ""),
            "emotion": _emotion_label(t.get("emotion", "")),
            "target_audience": t.get("expected_audience", "全年龄段"),
            "trend_reason": "题材库热门选题",
            "difficulty": "medium" if t.get("formula_used") == "dry-run-formula" else "easy",
        })
    return fallback


# ── FanqieRankTracker Importer ────────────────────────────────────────


def import_fanqie_trends(date_str: str | None = None) -> dict[str, Any]:
    """Import trending data from FanqieRankTracker (GitHub raw).

    Data repo: https://github.com/reeoi/FanqieRankTracker
    File pattern: fanqie_female_new_ranks_YYYYMMDD.json
    Raw URL: https://raw.githubusercontent.com/reeoi/FanqieRankTracker/main/data/{filename}

    Args:
        date_str: YYYY-MM-DD or YYYYMMDD. Defaults to today.
    """
    import json as _json
    from datetime import date as _date

    if date_str:
        date_str = date_str.replace("-", "")
    else:
        today = _date.today()
        date_str = today.strftime("%Y%m%d")

    filename = f"fanqie_female_new_ranks_{date_str}.json"
    raw_url = f"https://raw.githubusercontent.com/reeoi/FanqieRankTracker/main/data/{filename}"

    # Also try GitHub Pages (alternative CDN)
    gh_pages_url = f"https://reeoi.github.io/FanqieRankTracker/data/{filename}"

    last_error = ""
    for url in [raw_url, gh_pages_url]:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "ANP/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            # Cache locally
            _FANQIE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _FANQIE_CACHE_PATH.write_text(
                _json.dumps({"fetched_at": date_str, "source": url, "data": data}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Count books across all categories
            total_books = 0
            cats = data.get("categories", []) if isinstance(data, dict) else []
            for cat in cats:
                total_books += len(cat.get("books", []))
            logger.info("Imported Fanqie trends: %s (%d books across %d categories)", url, total_books, len(cats))
            return {"ok": True, "source": "live", "url": url, "date": date_str,
                    "books": total_books, "categories": len(cats)}
        except Exception as e:
            last_error = str(e)
            continue

    logger.warning("Failed to fetch FanqieRankTracker: %s", last_error)
    if _FANQIE_CACHE_PATH.exists():
        cached = _json.loads(_FANQIE_CACHE_PATH.read_text(encoding="utf-8"))
        return {"ok": True, "source": "cache", "date": cached.get("fetched_at", "?"), "count": "?"}
    return {"ok": False, "error": last_error}


def get_fanqie_trending_keywords(top_n: int = 20) -> list[dict[str, Any]]:
    """Extract trending keywords from cached Fanqie ranking data.

    Parses book titles and intros for recurring genre/theme keywords.
    """
    if not _FANQIE_CACHE_PATH.exists():
        return []

    try:
        data = json.loads(_FANQIE_CACHE_PATH.read_text(encoding="utf-8"))
        rankings_data = data.get("data", data)

        # Known genre/category patterns to detect
        genre_patterns = {
            "重生": ["重生"], "穿越": ["穿越"], "系统": ["系统"],
            "虐恋": ["虐恋", "虐文", "渣男"], "复仇": ["复仇", "打脸", "反杀"],
            "总裁": ["总裁", "豪门"], "宫斗": ["宫斗", "宅斗", "妃"],
            "玄幻": ["玄幻", "修仙", "修真", "仙侠"], "都市": ["都市"],
            "悬疑": ["悬疑", "推理", "诡异"], "灵异": ["灵异", "鬼"],
            "甜宠": ["甜宠", "甜文", "宠文"], "女强": ["女强", "逆袭"],
            "种田": ["种田", "基建"], "快穿": ["快穿"],
            "年代": ["年代", "七零", "八零", "九零"],
            "星际": ["星际", "机甲", "末世"],
        }

        keyword_counts: dict[str, int] = {}
        categories = rankings_data.get("categories", []) if isinstance(rankings_data, dict) else []

        for cat in categories:
            cat_name = cat.get("name", "")
            books = cat.get("books", [])
            for book in books:
                title = book.get("title", "")
                intro = book.get("intro", "")
                text = title + intro
                for genre, patterns in genre_patterns.items():
                    for pat in patterns:
                        if pat in text:
                            keyword_counts[genre] = keyword_counts.get(genre, 0) + 1

        return [{"keyword": k, "count": v} for k, v in
                sorted(keyword_counts.items(), key=lambda x: -x[1])[:top_n]]
    except Exception:
        return []


def get_fanqie_dates() -> list[str]:
    """Get list of available dates from FanqieRankTracker."""
    try:
        import urllib.request, json as _json
        url = "https://raw.githubusercontent.com/reeoi/FanqieRankTracker/main/data/dates.json"
        req = urllib.request.Request(url, headers={"User-Agent": "ANP/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            dates_data = _json.loads(resp.read().decode())
        return dates_data.get("dates", [])
    except Exception:
        return []


# ── Category Trend Analysis ───────────────────────────────────────────


def _parse_reads(reads_str: str) -> int:
    """Parse '11.5万' -> 115000."""
    try:
        s = str(reads_str).strip()
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        return int(float(s)) if s else 0
    except (ValueError, TypeError):
        return 0


def get_category_trend_analysis() -> list[dict[str, Any]]:
    """Analyze trends per category from the SQLite themes DB.

    Returns a list sorted by total reads (hottest first), with:
    - genre: category name
    - total_reads: sum of all book reads in this category
    - book_count: number of books
    - avg_reads: average reads per book
    - hotness_score: normalized 0-100 score
    - top_titles: top 3 books by reads
    - trending_keywords: extracted from book intros
    - trend_direction: "rising" if most books are recent
    """
    import sqlite3
    from pathlib import Path as _Path

    db_path = _Path(__file__).resolve().parents[2] / "data" / "anp.sqlite3"
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT genre, raw_json FROM themes WHERE source='fanqie'"
    ).fetchall()
    conn.close()

    # Aggregate per category
    cat_data: dict[str, dict[str, Any]] = {}
    for r in rows:
        genre = r["genre"]
        book = json.loads(r["raw_json"])
        reads = _parse_reads(book.get("reads", "0"))
        title = book.get("title", "")
        intro = book.get("intro", "")

        if genre not in cat_data:
            cat_data[genre] = {
                "total_reads": 0, "books": [],
            }
        cat_data[genre]["total_reads"] += reads
        cat_data[genre]["books"].append({"title": title, "reads": reads, "intro": intro})

    if not cat_data:
        return []

    max_reads = max(d["total_reads"] for d in cat_data.values())

    # Keyword extraction per category
    keyword_stopwords = {"的", "了", "是", "在", "她", "他", "我", "你", "和", "也",
                         "都", "就", "要", "会", "不", "着", "被", "把", "让", "从",
                         "与", "而", "之", "但", "却", "所", "等", "这", "那", "还",
                         "有", "又", "能", "去", "来", "到", "说", "看", "想", "一个"}

    result = []
    for genre, d in cat_data.items():
        d["books"].sort(key=lambda b: -b["reads"])
        top3 = d["books"][:3]

        # Extract keywords from top3 intros
        keyword_count: dict[str, int] = {}
        for b in top3:
            text = b.get("intro", "")
            # Simple Chinese bigram extraction
            for i in range(len(text) - 1):
                bigram = text[i:i + 2]
                if all("一" <= c <= "鿿" for c in bigram):
                    if bigram not in keyword_stopwords:
                        keyword_count[bigram] = keyword_count.get(bigram, 0) + 1

        keywords = [k for k, _ in sorted(keyword_count.items(), key=lambda x: -x[1])[:8]]

        result.append({
            "genre": genre,
            "total_reads": d["total_reads"],
            "book_count": len(d["books"]),
            "avg_reads": d["total_reads"] // max(len(d["books"]), 1),
            "hotness_score": round(d["total_reads"] / max(max_reads, 1) * 100, 1),
            "top_titles": [{"title": b["title"], "reads": b["reads"]} for b in top3],
            "trending_keywords": keywords,
            "trend_direction": "hot" if d["total_reads"] > max_reads * 0.3 else "warm",
        })

    result.sort(key=lambda x: -x["total_reads"])
    return result


def suggest_hot_opening(
    client: Any,
    target_type: str = "long",
) -> dict[str, Any]:
    """Generate a single compelling book opening based on the hottest category.

    Returns a dict with title, genre, premise, and trend_context for user confirmation.
    Falls back to the top category data if AI fails.
    """
    trends = get_category_trend_analysis()
    if not trends:
        return {"title": "", "genre": "", "premise": "", "trend_context": "题材库为空，请先拉取番茄榜单"}

    top_cat = trends[0]
    top_books = top_cat["top_titles"]
    keywords = top_cat["trending_keywords"]

    book_samples = "\n".join(
        f"- {b['title']} ({b['reads']:,} 阅读)" for b in top_books
    )

    system = "你是一位资深网文编辑和开篇策划人。根据当前番茄小说女频热门趋势，为一个新书项目生成有爆款潜力的开篇方案。"
    user = f"""根据以下番茄小说女频 {target_type} 篇最热趋势，生成一个新书开篇方案。

## 最热门分类
**{top_cat['genre']}** — 总阅读 {top_cat['total_reads']:,}，热度评分 {top_cat['hotness_score']}

## 该分类TOP3爆款
{book_samples}

## 趋势关键词
{', '.join(keywords[:6])}

## 要求
生成一个完整的新书开篇方案，包含：
1. **书名**：吸引人的网文书名（8-15字以内）
2. **题材标签**：细分题材标签（如：古言重生、豪门逆袭等）
3. **一句话梗概**：30-80字，突出核心卖点和爽点
4. **目标读者**：女频/男频 + 年龄段
5. **差异亮点**：与当前热门榜单相比，这本书有什么独特之处（20-40字）

直接输出以下格式（不要其他内容）：
书名：xxx
题材：xxx
梗概：xxx
读者：xxx
亮点：xxx"""

    try:
        completion = client.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            thinking_mode=True,
        )
        raw_text = completion.text if hasattr(completion, "text") else str(completion)

        # Strip thinking-mode XML wrappers from DeepSeek response.
        # Format: <think>...</think> <response>actual content</response>
        text = raw_text
        for sentinel in ("</response>", "<response "):
            if sentinel in text:
                text = text.rsplit(sentinel, 1)[-1]
                break
        import re as _re
        text = _re.sub(r'</? *(think|thinking|response)\s*/?>', '', text)
        text = text.strip()

        logger.info("Hot opening AI returned %d chars, parsing...", len(text))

        # Try parsing structured format
        result: dict[str, str] = {}
        for line in text.split("\n"):
            line = line.strip().lstrip("-*#> ").strip()
            for prefix, key in [("书名：", "title"), ("书名:", "title"),
                                ("题材：", "genre"), ("题材:", "genre"),
                                ("梗概：", "premise"), ("梗概:", "premise"),
                                ("读者：", "audience"), ("读者:", "audience"),
                                ("亮点：", "highlight"), ("亮点:", "highlight")]:
                if line.startswith(prefix):
                    result[key] = line.replace(prefix, "", 1).strip()
                    break

        if result.get("title") and result.get("premise"):
            result["trend_context"] = (
                f"基于「{top_cat['genre']}」分类热门趋势（总阅读 {top_cat['total_reads']:,}），"
                f"参考 TOP3 爆款生成"
            )
            result["category"] = top_cat["genre"]
            return result

        # Fallback 1: try JSON
        json_match = _re.search(r'\{[^}]+\}', text)
        if json_match:
            try:
                j = json.loads(json_match.group())
                title = j.get("title") or j.get("书名") or ""
                premise = j.get("premise") or j.get("梗概") or j.get("intro") or ""
                if title and premise:
                    return {
                        "title": title,
                        "genre": j.get("genre") or j.get("题材") or top_cat["genre"],
                        "premise": premise,
                        "trend_context": f"基于「{top_cat['genre']}」分类热门趋势（总阅读 {top_cat['total_reads']:,}），AI生成",
                        "category": top_cat["genre"],
                    }
            except json.JSONDecodeError:
                pass

        # Fallback 2: heuristic line extraction
        raw_lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 6 and not l.strip().startswith(("#", "```", "**", ">"))]
        if len(raw_lines) >= 2:
            logger.info("Hot opening using heuristic parse: %d lines", len(raw_lines))
            return {
                "title": raw_lines[0][:60],
                "genre": top_cat["genre"],
                "premise": raw_lines[1][:200],
                "trend_context": f"基于「{top_cat['genre']}」分类热门趋势（总阅读 {top_cat['total_reads']:,}），AI生成（自动提取）",
                "category": top_cat["genre"],
            }

        logger.warning("Hot opening parsing exhausted, raw=%s", text[:200])
    except Exception as e:
        logger.warning("AI hot_opening exception: %s, falling back", e)

    # Ultimate fallback
    top_book = top_books[0] if top_books else {"title": "", "reads": 0}
    return {
        "title": f"新·{top_book['title'][:10]}",
        "genre": top_cat["genre"],
        "premise": f"在{top_cat['genre']}热门赛道，结合{', '.join(keywords[:3])}等趋势元素，打造差异化爆款。",
        "trend_context": f"基于「{top_cat['genre']}」分类热门趋势（总阅读 {top_cat['total_reads']:,}），使用模板建议",
        "category": top_cat["genre"],
    }

# ── Helpers ───────────────────────────────────────────────────────────


def _genre_label(genre_id: str) -> str:
    mapping = {
        "xian_dai_fu_chou": "现代复仇", "zong_cai_hao_men": "总裁豪门",
        "chong_sheng_fan_sha": "重生反杀", "xuan_yi_fan_zhuan": "悬疑反转",
        "ling_hun_shi_jiao": "灵魂视角", "xian_shi_sheng_huo": "现实生活",
        "xuan_huan": "玄幻", "xian_xia": "仙侠", "du_shi": "都市",
        "ke_huan": "科幻", "li_shi": "历史", "yan_qing": "言情",
        "mo_shi": "末世", "wu_xian_liu": "无限流", "xi_tong_wen": "系统文",
        "chuan_yue": "穿越", "zhong_tian_wen": "种田文",
        "xuan_huan_xiu_xian": "玄幻修仙", "du_shi_shuang_wen": "都市爽文",
    }
    return mapping.get(genre_id, genre_id)


def _emotion_label(emotion_id: str) -> str:
    mapping = {
        "yi_nan_ping": "意难平", "fan_zhuan_zhen_han": "反转震撼",
        "shuang_gan_shi_fang": "爽感释放", "zhi_yu_wen_nuan": "治愈温暖",
        "xi_si_ji_kong": "细思极恐", "gong_ming_gan_dong": "共鸣感动",
    }
    return mapping.get(emotion_id, emotion_id)


__all__ = [
    "get_theme_pool",
    "get_trending_genres",
    "get_trending_emotions",
    "get_hot_themes",
    "suggest_books",
    "suggest_hot_opening",
    "get_category_trend_analysis",
    "import_fanqie_trends",
    "get_fanqie_trending_keywords",
]
