"""HTTP request action — external API calls from custom steps."""

from __future__ import annotations

import logging
from typing import Any

from generator.c_pipeline.actions.base import ActionContext, ActionResult, BaseAction

logger = logging.getLogger(__name__)


class HttpRequestAction(BaseAction):
    action_name = "http_request"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        url = ctx.resolve_v2(str(params.get("url") or ""))
        method = str(params.get("method") or "GET").upper()
        headers_raw = params.get("headers") or {}
        body_raw = params.get("body") or ""
        output_var = str(params.get("output_var") or "http_response")

        resolved_headers = {k: ctx.resolve_v2(str(v)) for k, v in headers_raw.items()} if isinstance(headers_raw, dict) else {}
        resolved_body = ctx.resolve_v2(str(body_raw)) if body_raw else None

        import json
        import urllib.error
        import urllib.request

        try:
            data_bytes = json.dumps(json.loads(resolved_body) if isinstance(resolved_body, str) and resolved_body.strip().startswith("{") else resolved_body or "").encode("utf-8") if resolved_body else None
            # Simpler approach: just use plain request
            if data_bytes and not resolved_headers.get("Content-Type"):
                resolved_headers["Content-Type"] = "application/json"

            req = urllib.request.Request(url, data=data_bytes, headers=resolved_headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
            resp_data = {"status": resp.status, "body": resp_body}
            ctx.set(output_var, resp_data)
            return ActionResult(ok=True, outputs={output_var: resp_data}, message=f"HTTP {method} {url} → {resp.status}")
        except urllib.error.HTTPError as exc:
            logger.warning("http_request HTTP %s %s", exc.code, url)
            return ActionResult(ok=False, message=f"http_request: HTTP {exc.code} from {url}")
        except Exception as exc:
            logger.exception("http_request failed")
            return ActionResult(ok=False, message=f"http_request: {exc}")


__all__ = ["HttpRequestAction"]
