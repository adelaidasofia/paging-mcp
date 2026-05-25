# paging-mcp


<!-- mycelium-badges:start -->

<p>
  <a href="https://github.com/adelaidasofia/paging-mcp/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/adelaidasofia/paging-mcp?color=blue"></a>
  <a href="https://github.com/adelaidasofia/paging-mcp/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/adelaidasofia/paging-mcp?color=eab308"></a>
  <a href="https://github.com/adelaidasofia/paging-mcp/commits/main"><img alt="Last commit" src="https://img.shields.io/github/last-commit/adelaidasofia/paging-mcp"></a>
  <a href="https://github.com/adelaidasofia/paging-mcp/issues"><img alt="Open issues" src="https://img.shields.io/github/issues/adelaidasofia/paging-mcp"></a>
  <a href="https://pypi.org/project/adelaidasofia-paging-mcp/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/adelaidasofia-paging-mcp?color=blue&label=pypi"></a>
  <a href="https://pypi.org/project/adelaidasofia-paging-mcp/"><img alt="PyPI downloads" src="https://img.shields.io/pypi/dm/adelaidasofia-paging-mcp?color=blue&label=downloads"></a>
  <a href="https://myceliumai.co"><img alt="Built by Mycelium AI" src="https://img.shields.io/badge/built_by-Mycelium_AI-15B89A"></a>
</p>

<!-- mycelium-badges:end -->

FastMCP server that pages on-call via 100+ notification channels. Built on top of [Apprise](https://github.com/caronc/apprise), which speaks WhatsApp, ntfy, Pushover, Telegram, Discord, Slack, Signal, Matrix, Gotify, mailto, Twilio SMS, AWS SNS, and dozens more.

The point is **provider independence**. Wire one MCP, configure as many channels as you want, get redundant paging without rewriting integration code every time you swap a vendor.

## Why this exists

If you're building anything that needs to wake a human up — a synthetic monitor on a B2B pipeline, a long-running batch job, a security alert — you want:

1. Multiple channels in parallel so a single provider outage doesn't lose the page.
2. Channels that actually fit your operator (WhatsApp for LatAm on-calls, ntfy for self-hosters, Pushover for personal phones, Twilio SMS for the cases where it still fits).
3. Credentials redacted in tool responses so an agent reading the output doesn't leak them back into a transcript.
4. One MCP your agent can call from anywhere (Claude Code, Codex, Cursor, etc.) without having to ship a new MCP per channel.

Apprise solves channels 1-2 in Python. This server makes it MCP-callable with redaction and channel filtering on top.

## Install

Open Claude Code, paste:

    /plugin marketplace add adelaidasofia/paging-mcp
    /plugin install paging-mcp@paging-mcp

<details><summary>Legacy install</summary>

```bash
git clone https://github.com/adelaidasofia/paging-mcp ~/.claude/paging-mcp
cd ~/.claude/paging-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

</details>

## Configure

Set `APPRISE_URLS` to one or more [Apprise URLs](https://github.com/caronc/apprise/wiki). Newline, semicolon, or comma separated:

```bash
export APPRISE_URLS="
ntfys://ntfy.sh/<your-secret-topic>
whapi+https://gate.whapi.cloud/messages/text?token=<TOKEN>&to=+57...
pover://<USER_KEY>@<APP_TOKEN>
mailto://user:pass@smtp.example.com/?to=alerts@example.com
"
```

Apprise URL examples by provider:

| Provider | URL pattern |
|---|---|
| WhatsApp Business (Whapi) | `whapi+https://gate.whapi.cloud/messages/text?token=<TOKEN>&to=<+E164>` |
| ntfy | `ntfys://ntfy.sh/<topic>` |
| Pushover | `pover://<USER_KEY>@<APP_TOKEN>` |
| Telegram bot | `tgram://<BOT_TOKEN>/<CHAT_ID>` |
| Twilio SMS | `twilio://<SID>:<TOKEN>@<FROM>/<TO>` |
| Signal CLI | `signal://<from>@<api-host>/<to>` |
| Slack incoming webhook | `slack://<token-a>/<token-b>/<token-c>` |
| Discord webhook | `discord://<webhook-id>/<webhook-token>` |
| SMTP email | `mailto://<user>:<pass>@<host>:<port>/?to=<dest>` |
| AWS SNS | `sns://<KEY>/<SECRET>/<REGION>/<TOPIC>` |

Full list: https://github.com/caronc/apprise/wiki

## Register with Claude Code

Project-scoped (`<vault>/.mcp.json`):

```json
{
  "mcpServers": {
    "paging": {
      "command": "/Users/<you>/.claude/paging-mcp/.venv/bin/python",
      "args": ["/Users/<you>/.claude/paging-mcp/server.py"],
      "env": {
        "APPRISE_URLS": "ntfys://ntfy.sh/<your-secret-topic>"
      }
    }
  }
}
```

User-scoped:

```bash
claude mcp add -s user paging \
  /Users/<you>/.claude/paging-mcp/.venv/bin/python \
  /Users/<you>/.claude/paging-mcp/server.py \
  --env APPRISE_URLS="ntfys://ntfy.sh/<your-secret-topic>"
```

Restart Claude Code. Verify with `claude mcp list`.

## Tools

| Tool | What it does |
|---|---|
| `health_check` | Server self-check + Apprise version + configured channel count. |
| `list_configured_channels` | Lists the channels paging-mcp will dispatch to, credentials redacted. |
| `notify(title, body, severity, channels?)` | Sends a notification to all configured channels (or a subset filter on redacted URL). Returns per-channel results. |
| `notify_one(url, title, body, severity)` | Sends to a single ad-hoc Apprise URL (not persisted). |
| `dry_run(title, body, severity)` | Shows what `notify()` would send, without dispatching. |

`severity` is one of `info`, `success`, `warning`, `failure`. Channels that color or icon-tag use it.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -v
```

16 unit + integration tests covering URL redaction, multi-channel dispatch, partial failure, channel filtering, and end-to-end paging flow with credentials in URLs.

## Security notes

- Credentials are never echoed in tool responses. Path-as-secret schemes (ntfy, pushbullet, pover) get their last path segment masked too.
- `notify_one` accepts an ad-hoc URL; agents passing literal credentials are still responsible for not echoing them in conversation. Prefer `APPRISE_URLS` configured at server start.
- The server does not persist channels or messages; everything is in-process.


## Telemetry

This plugin sends a single anonymous install signal to `myceliumai.co` the first time it loads in a Claude Code session on a given machine.

**What is sent:**
- Plugin name (e.g. `slack-mcp`)
- Plugin version (e.g. `0.1.0`)

**What is NOT sent:**
- No user identifiers, names, emails, tokens, or API keys
- No file paths, message content, or anything from your work
- No IP address is stored after dedup processing

**Why:** Helps the maintainer know which plugins people actually install, so attention goes to the ones that get used.

**Opt out:** Set the environment variable `MYCELIUM_NO_PING=1` before launching Claude Code. The hook will skip the network call entirely. Already-pinged installs leave a sentinel at `~/.mycelium/onboarded-<plugin>` — delete it if you want to reset state.

## License

MIT — see `LICENSE`.

---

Built by [Mycelium AI](https://myceliumai.co). Full install or team version at [diazroa.com](https://diazroa.com).
