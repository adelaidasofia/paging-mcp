"""paging-mcp — FastMCP server that pages on-call via 100+ notification channels.

Wraps the Apprise notification library (https://github.com/caronc/apprise) so any
MCP client (Claude Code, Codex, Cursor) can dispatch a notification to whatever
combination of channels the operator has configured.

Why this exists (not "yet another Twilio wrapper"):
- SMS is US-centric. For on-calls outside North America the most reliable
  channel is often WhatsApp, Telegram, or ntfy push — not SMS. Pagers need
  redundancy, and single-vendor lock-in breaks that.
- Apprise covers WhatsApp Business / ntfy / Pushover / Telegram / Discord /
  Slack / Signal CLI / Matrix / Gotify / mailto / Twilio SMS / AWS SNS / etc.
  with one config string per channel.

Config: every channel is an Apprise URL string in env var `APPRISE_URLS`
(newline OR `;` OR `,` separated). Examples:

  whapi+https://gate.whapi.cloud/messages/text?token=...&to=...
  ntfys://ntfy.sh/<your-secret-topic>
  pover://user@token  (Pushover)
  tgram://bottoken/chat_id  (Telegram)
  mailto://user:pass@smtp.example.com/?to=alerts@example.com
  twilio://sid:token@from/to  (optional — Twilio still supported, not required)

Tools:
  health_check               server self-check + channel inventory
  list_configured_channels   redacted Apprise URLs the server will dispatch to
  notify                     send a message to all configured channels (or a subset)
  notify_one                 send to a single ad-hoc URL (not stored)
  dry_run                    show what would be sent without dispatching

Design notes:
- The Apprise library handles parallel send + per-provider retries internally;
  this server is a thin async wrapper that returns a structured result.
- URLs are redacted in responses (credentials masked) so logs don't leak.
- `severity` is informational; channel SLAs (delivery, retry) live in Apprise.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import apprise
from fastmcp import FastMCP

logger = logging.getLogger("paging-mcp")
logging.basicConfig(level=os.environ.get("PAGING_LOG_LEVEL", "INFO"))

mcp = FastMCP("paging-mcp")

# How operators configure channels. Newline / semicolon / comma all work because
# operators put these in shell env files differently.
_URL_SPLIT_RE = re.compile(r"[\n;,]")

_SEVERITIES = {"info", "success", "warning", "failure"}


@dataclass
class ChannelResult:
    """One channel's send outcome."""

    url_redacted: str
    ok: bool
    error: str | None = None


def _load_urls() -> list[str]:
    raw = os.environ.get("APPRISE_URLS", "")
    return [u.strip() for u in _URL_SPLIT_RE.split(raw) if u.strip()]


def _redact(url: str) -> str:
    """Mask credentials in an Apprise URL so logs / API responses don't leak.

    Covers three classes of secret leakage:
      1. `scheme://user:pass@host` — basic-auth credentials
      2. `?token=…&apikey=…` — query-string tokens
      3. For schemes where the path IS the secret (ntfy topic, pushbullet
         token), mask the last path segment too.
    """
    redacted = re.sub(r"(://)[^@/]+@", r"\1***@", url)
    redacted = re.sub(
        r"(token|apikey|api_key|key|password|pwd|secret)=([^&\s]+)",
        lambda m: f"{m.group(1)}=***",
        redacted,
        flags=re.IGNORECASE,
    )
    # Path-as-secret schemes — ntfy topics, pushbullet tokens, pover tokens
    # encoded in path. Mask the final non-empty path segment.
    path_secret_schemes = ("ntfys", "ntfy", "pbul", "pover")
    for scheme in path_secret_schemes:
        prefix = f"{scheme}://"
        if redacted.startswith(prefix):
            head, _, tail = redacted.rpartition("/")
            if tail and head != prefix.rstrip("/"):
                redacted = f"{head}/***"
            break
    return redacted


def _build_apprise(urls: list[str]) -> apprise.Apprise:
    """Build a fresh Apprise object loaded with the supplied URLs."""
    a = apprise.Apprise()
    for url in urls:
        if not a.add(url):
            logger.warning("apprise rejected URL: %s", _redact(url))
    return a


async def _notify_async(
    urls: list[str],
    title: str,
    body: str,
    severity: str,
) -> list[ChannelResult]:
    """Dispatch to all URLs in parallel; return per-channel results."""
    if not urls:
        return []
    # Apprise's `async_notify` returns True if ALL channels succeeded; for
    # per-channel detail we call each URL separately. Trade-off: slightly more
    # network chatter, but the trust layer needs per-channel receipts.
    results: list[ChannelResult] = []

    async def _one(url: str) -> ChannelResult:
        try:
            a = apprise.Apprise()
            if not a.add(url):
                return ChannelResult(url_redacted=_redact(url), ok=False, error="invalid_url")
            ok = await a.async_notify(title=title, body=body, notify_type=severity)
            return ChannelResult(url_redacted=_redact(url), ok=bool(ok), error=None if ok else "send_failed")
        except Exception as exc:
            logger.exception("apprise send failed for %s", _redact(url))
            return ChannelResult(url_redacted=_redact(url), ok=False, error=f"{exc.__class__.__name__}: {exc}")

    results = await asyncio.gather(*(_one(u) for u in urls))
    return list(results)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Server self-check + channel inventory."""
    urls = _load_urls()
    a = _build_apprise(urls)
    return {
        "ok": True,
        "apprise_version": apprise.__version__,
        "configured_urls": len(urls),
        "loaded_channels": len(a),
        "severity_levels": sorted(_SEVERITIES),
    }


@mcp.tool()
def list_configured_channels() -> list[dict[str, Any]]:
    """List the channels paging-mcp will dispatch to, with credentials redacted."""
    urls = _load_urls()
    out = []
    for url in urls:
        a = apprise.Apprise()
        added = a.add(url)
        out.append(
            {
                "url_redacted": _redact(url),
                "loaded": bool(added),
            }
        )
    return out


@mcp.tool()
async def notify(
    title: str,
    body: str,
    severity: str = "warning",
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """Send a notification to all configured channels.

    Args:
        title: Short subject line shown by channels that support titles.
        body: Message body. Most channels accept plain text or markdown.
        severity: "info" | "success" | "warning" | "failure". Some channels
            color or icon-tag based on this; default "warning".
        channels: Optional subset of redacted URL prefixes to filter to. If
            omitted, dispatches to every configured channel.

    Returns:
        {"sent": N, "ok_count": M, "results": [{url_redacted, ok, error}, ...]}
    """
    severity = severity if severity in _SEVERITIES else "warning"
    urls = _load_urls()
    if channels:
        # Filter by redacted-URL prefix or substring match, so the agent can
        # say "only WhatsApp" without leaking secrets back to itself.
        urls = [u for u in urls if any(c in _redact(u) for c in channels)]
    results = await _notify_async(urls, title=title, body=body, severity=severity)
    return {
        "sent": len(results),
        "ok_count": sum(1 for r in results if r.ok),
        "results": [asdict(r) for r in results],
    }


@mcp.tool()
async def notify_one(url: str, title: str, body: str, severity: str = "warning") -> dict[str, Any]:
    """Send a notification to a single ad-hoc Apprise URL (not persisted).

    Useful for one-off pages where the channel isn't in `APPRISE_URLS`.
    """
    severity = severity if severity in _SEVERITIES else "warning"
    results = await _notify_async([url], title=title, body=body, severity=severity)
    return {
        "sent": len(results),
        "ok_count": sum(1 for r in results if r.ok),
        "results": [asdict(r) for r in results],
    }


@mcp.tool()
def dry_run(title: str, body: str, severity: str = "warning") -> dict[str, Any]:
    """Show what `notify()` would send, without dispatching."""
    severity = severity if severity in _SEVERITIES else "warning"
    urls = _load_urls()
    return {
        "would_send_to": [_redact(u) for u in urls],
        "title": title,
        "body": body,
        "severity": severity,
        "channel_count": len(urls),
    }


if __name__ == "__main__":
    mcp.run()
