"""Unified ANP entrypoint."""

from __future__ import annotations

import argparse

from config_loader import ConfigError, load_from_environment
from queue.db import initialize_database


def main() -> int:
    """Load config, bootstrap local infrastructure, and report runtime mode."""
    parser = argparse.ArgumentParser(description="ANP local automation pipeline")
    parser.add_argument("--mode", choices=["auto", "semi-auto"], help="Override runtime mode")
    args = parser.parse_args()

    try:
        config = load_from_environment()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if args.mode:
        config.data.setdefault("runtime", {})["mode"] = args.mode

    for warning in config.warnings:
        print(f"[config] {warning}")

    db_path = initialize_database(config)
    mode = config.data.get("runtime", {}).get("mode", "semi-auto")
    print(f"ANP ready: mode={mode}, dry_run={config.is_dry_run}, sqlite={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
