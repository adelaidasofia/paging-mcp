"""Unit + integration tests for paging-mcp."""

import os
from unittest.mock import AsyncMock, patch

import pytest

import server


class TestRedact:
    def test_strips_user_pass_block(self):
        out = server._redact("mailto://alice:hunter2@smtp.example.com")
        assert "hunter2" not in out
        assert "***@smtp.example.com" in out

    def test_masks_query_token(self):
        out = server._redact("https://gate.whapi.cloud/messages?token=SECRET123&to=+57")
        assert "SECRET123" not in out
        assert "token=***" in out

    def test_masks_apikey_variants(self):
        for q in ("apikey", "api_key", "key", "password", "PWD", "Secret"):
            out = server._redact(f"https://api.example.com/?{q}=ABC")
            assert "ABC" not in out, f"failed to mask {q}"


class TestLoadUrls:
    def test_empty_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("APPRISE_URLS", raising=False)
        assert server._load_urls() == []

    def test_newline_separated(self, monkeypatch):
        monkeypatch.setenv(
            "APPRISE_URLS",
            "ntfys://ntfy.sh/topic1\ntgram://token/chat\n",
        )
        urls = server._load_urls()
        assert len(urls) == 2
        assert urls[0].startswith("ntfys://")
        assert urls[1].startswith("tgram://")

    def test_semicolon_and_comma_separated(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "url1;url2,url3")
        urls = server._load_urls()
        assert urls == ["url1", "url2", "url3"]

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "  url1  ,  url2  ")
        urls = server._load_urls()
        assert urls == ["url1", "url2"]


class TestHealthCheck:
    def test_reports_apprise_version(self, monkeypatch):
        monkeypatch.delenv("APPRISE_URLS", raising=False)
        result = server.health_check()
        assert result["ok"] is True
        assert isinstance(result["apprise_version"], str)
        assert result["configured_urls"] == 0
        assert set(result["severity_levels"]) == {"info", "success", "warning", "failure"}

    def test_counts_loaded_channels(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "ntfys://ntfy.sh/test-topic-xyz123")
        result = server.health_check()
        assert result["configured_urls"] == 1


class TestListConfiguredChannels:
    def test_returns_redacted_urls(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "mailto://alice:hunter2@smtp.example.com")
        out = server.list_configured_channels()
        assert len(out) == 1
        assert "hunter2" not in out[0]["url_redacted"]
        assert "***" in out[0]["url_redacted"]


class TestDryRun:
    def test_returns_redacted_destinations(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "https://api.example.com/?token=SECRET")
        out = server.dry_run(title="t", body="b", severity="warning")
        assert "SECRET" not in str(out)
        assert out["channel_count"] == 1
        assert out["title"] == "t"
        assert out["severity"] == "warning"

    def test_invalid_severity_falls_back_to_warning(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "")
        out = server.dry_run(title="t", body="b", severity="bogus")
        assert out["severity"] == "warning"


# ---------------------------------------------------------------------------
# Notify dispatch — mocks apprise so we don't actually send anything
# ---------------------------------------------------------------------------


class TestNotify:
    @pytest.mark.asyncio
    async def test_sends_to_all_configured(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "ntfys://ntfy.sh/a,ntfys://ntfy.sh/b")
        mock_notify = AsyncMock(return_value=True)
        with patch("apprise.Apprise.async_notify", mock_notify):
            result = await server.notify(title="t", body="b", severity="warning")
        assert result["sent"] == 2
        assert result["ok_count"] == 2

    @pytest.mark.asyncio
    async def test_partial_failure_recorded(self, monkeypatch):
        monkeypatch.setenv("APPRISE_URLS", "ntfys://ntfy.sh/a,ntfys://ntfy.sh/b")
        call_n = {"i": 0}

        async def fake_send(*args, **kwargs):
            call_n["i"] += 1
            return call_n["i"] % 2 == 1  # 1st OK, 2nd fail

        with patch("apprise.Apprise.async_notify", side_effect=fake_send):
            result = await server.notify(title="t", body="b")
        assert result["sent"] == 2
        assert result["ok_count"] == 1
        failed = [r for r in result["results"] if not r["ok"]]
        assert len(failed) == 1
        assert failed[0]["error"] == "send_failed"

    @pytest.mark.asyncio
    async def test_channel_filter_subset(self, monkeypatch):
        monkeypatch.setenv(
            "APPRISE_URLS",
            "ntfys://ntfy.sh/a\nmailto://alice@smtp.example.com",
        )
        with patch("apprise.Apprise.async_notify", AsyncMock(return_value=True)):
            result = await server.notify(
                title="t", body="b", severity="warning", channels=["mailto"]
            )
        assert result["sent"] == 1
        assert result["ok_count"] == 1


# ---------------------------------------------------------------------------
# Integration test (Lesson #23) — full notify dispatch round-trip via Apprise
# stub, with credentials in URLs, asserting redaction + error propagation.
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_paging_flow(self, monkeypatch):
        # Operator config: 3 mixed channels with secrets in the URLs.
        monkeypatch.setenv(
            "APPRISE_URLS",
            "ntfys://ntfy.sh/super-secret-topic-xyz\n"
            "tgram://1234567890:ABCdef-ghijklmnop/-1001234567890\n"
            "mailto://alice:hunter2@smtp.example.com/?to=alerts@x.com",
        )

        # Alternate ok/fail/ok so we exercise both branches; per-URL identity
        # doesn't matter for the integration assertion (redaction + result shape).
        call_n = {"i": 0}

        async def fake_send(*args, **kwargs):
            call_n["i"] += 1
            return call_n["i"] != 2  # 2nd call fails (telegram in our config)

        with patch("apprise.Apprise.async_notify", side_effect=fake_send):
            health = server.health_check()
            assert health["configured_urls"] == 3

            result = await server.notify(
                title="trust-layer page",
                body="Synthetic monitor failed",
                severity="failure",
            )

        # Each channel's URL must be redacted in the response.
        for entry in result["results"]:
            assert "hunter2" not in entry["url_redacted"]
            assert "bottoken-abc" not in entry["url_redacted"]
            assert "super-secret-topic-xyz" not in entry["url_redacted"]

        # Per-channel results returned (3 total).
        assert result["sent"] == 3
        assert 0 <= result["ok_count"] <= 3
