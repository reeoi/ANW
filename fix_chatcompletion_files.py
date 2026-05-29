"""一次性修复 long_novel 模块 .content/.text bug 写出的损坏 .md 文件。

两种损坏形态：
1. 整文件是 `ChatCompletion(text='...\n...\n', reasoning=...)` repr (被截断后可能没有闭合 ')
2. 普通文本但所有 \n 都是字面 backslash+n 字符 (没有真换行)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


CHAT_PREFIXES = (
    "ChatCompletion(text='",
    'ChatCompletion(text="',
)


def _strip_chat_repr(raw: str) -> str:
    """Strip ChatCompletion(text='...' wrapper, returning the inner body."""
    for prefix in CHAT_PREFIXES:
        if not raw.startswith(prefix):
            continue
        body = raw[len(prefix):]
        quote = prefix[-1]
        for tail in (
            quote + ", reasoning=",
            quote + ", model=",
            quote + ", usage=",
            quote + ")",
        ):
            i = body.rfind(tail)
            if i >= 0:
                return body[:i]
        return body  # truncated repr, take everything we have
    return raw


def _unescape(s: str) -> str:
    """Convert Python-string-literal style escapes back to real characters.

    Handles: backslash-n, backslash-r, backslash-t, backslash-quote, backslash-backslash.
    Uses placeholder so double-backslash never collides with subsequent rules.
    """
    placeholder = "\x00"
    s = s.replace("\\" + "\\", placeholder)
    s = s.replace("\\" + "n", "\n")
    s = s.replace("\\" + "r", "\r")
    s = s.replace("\\" + "t", "\t")
    s = s.replace("\\" + "'", "'")
    s = s.replace("\\" + '"', '"')
    s = s.replace(placeholder, "\\")
    return s


def restore(raw: str) -> tuple[str, str]:
    """Return (fixed_text, reason). reason in {'chat_repr','escape_only','noop'}."""
    if any(raw.startswith(p) for p in CHAT_PREFIXES):
        body = _strip_chat_repr(raw)
        return _unescape(body), "chat_repr"
    # No real newlines but literal backslash-n present
    if "\n" not in raw and ("\\" + "n") in raw:
        return _unescape(raw), "escape_only"
    return raw, "noop"


def main(book_dir: Path, apply: bool) -> int:
    if not book_dir.exists():
        print(f"book dir not found: {book_dir}")
        return 1
    counts = {"chat_repr": 0, "escape_only": 0, "noop": 0, "error": 0}
    backup_root = book_dir.parent / (book_dir.name + ".restore-backup")
    for p in sorted(book_dir.rglob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"  ERR read {p}: {exc}")
            counts["error"] += 1
            continue
        fixed, reason = restore(raw)
        counts[reason] += 1
        if reason == "noop":
            continue
        rel = p.relative_to(book_dir)
        print(f"  [{reason}] {rel}  ({len(raw)}B -> {len(fixed)}B, newlines {raw.count(chr(10))} -> {fixed.count(chr(10))})")
        if apply:
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup_path)
            p.write_text(fixed, encoding="utf-8")
    print("summary:", counts)
    if not apply:
        print("(dry-run; pass --apply to write)")
    elif sum(counts[k] for k in ("chat_repr", "escape_only")) > 0:
        print(f"backups under: {backup_root}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    apply = "--apply" in args
    paths = [Path(a) for a in args if not a.startswith("--")]
    if not paths:
        paths = [Path("data/books/段宴不知，他的白月光是江媃")]
    rc = 0
    for p in paths:
        print(f"== {p} ==")
        rc = max(rc, main(p, apply))
    sys.exit(rc)
