from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

import quotabubble.providers.claude as claude_module
from quotabubble.providers.base import Provider, ProviderStatus
from quotabubble.providers.claude import ClaudeProvider
from quotabubble.providers.claude_auth import TOKEN_URL

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def no_system_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_module, "enumerate_generic_credentials", lambda _: [])


def _write_credentials(tmp_path: Path) -> Path:
    target = tmp_path / "credentials.json"
    source = FIXTURES / "claude_credentials.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _provider_with_body(tmp_path: Path, body: str) -> ClaudeProvider:
    return ClaudeProvider(
        credentials_path=_write_credentials(tmp_path),
        client=_client(lambda request: httpx2.Response(200, text=body)),
    )


def test_implements_provider_protocol() -> None:
    assert isinstance(ClaudeProvider(), Provider)


def test_missing_credentials_reports_no_credentials(tmp_path: Path) -> None:
    provider = ClaudeProvider(credentials_path=tmp_path / "absent.json")

    assert provider.fetch().status is ProviderStatus.NO_CREDENTIALS


def test_flat_buckets_are_used_when_limits_are_absent(tmp_path: Path) -> None:
    body = (FIXTURES / "claude_usage.json").read_text(encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
        return httpx2.Response(200, text=body)

    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path), client=_client(handler)
    )
    snapshot = provider.fetch()

    assert snapshot.status is ProviderStatus.OK
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_pct == 42.0
    assert snapshot.windows[1].used_pct == 71.0
    assert snapshot.windows[0].resets_at is not None


def test_limits_are_preferred_and_scoped_windows_are_labelled(tmp_path: Path) -> None:
    body = (FIXTURES / "claude_usage_limits.json").read_text(encoding="utf-8")
    snapshot = _provider_with_body(tmp_path, body).fetch()

    assert [window.label for window in snapshot.windows] == ["5h", "Weekly", "Fable"]
    assert [window.used_pct for window in snapshot.windows] == [9.0, 90.0, 53.0]

    weekly = snapshot.windows[1]
    assert weekly.key == "weekly"
    assert weekly.severity == "critical"
    assert weekly.active is True

    fable = snapshot.windows[2]
    assert fable.key == "weekly_scoped.fable"
    assert fable.scope == "Fable"


def test_credits_are_reported_when_enabled(tmp_path: Path) -> None:
    body = (FIXTURES / "claude_usage_credits.json").read_text(encoding="utf-8")
    snapshot = _provider_with_body(tmp_path, body).fetch()

    assert snapshot.credits is not None
    assert snapshot.credits.display == "$13.59 / $50.00"
    assert snapshot.credits.used_pct == 27.0


def test_credits_absent_when_disabled(tmp_path: Path) -> None:
    body = (FIXTURES / "claude_usage_limits.json").read_text(encoding="utf-8")
    snapshot = _provider_with_body(tmp_path, body).fetch()

    assert snapshot.credits is None


def test_rejected_credentials_report_expired(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path),
        client=_client(lambda request: httpx2.Response(401)),
    )

    assert provider.fetch().status is ProviderStatus.EXPIRED


def test_server_error_reports_error(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path),
        client=_client(lambda request: httpx2.Response(503)),
    )

    assert provider.fetch().status is ProviderStatus.ERROR


def test_rate_limit_reports_retry_after(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path),
        client=_client(
            lambda request: httpx2.Response(429, headers={"Retry-After": "600"})
        ),
    )

    snapshot = provider.fetch()

    assert snapshot.status is ProviderStatus.ERROR
    assert snapshot.message == "HTTP 429"
    assert snapshot.retry_after == 600.0


def test_malformed_body_reports_error(tmp_path: Path) -> None:
    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path),
        client=_client(lambda request: httpx2.Response(200, text="not json")),
    )

    assert provider.fetch().status is ProviderStatus.ERROR


def test_network_error_reports_error(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom")

    provider = ClaudeProvider(
        credentials_path=_write_credentials(tmp_path), client=_client(handler)
    )

    assert provider.fetch().status is ProviderStatus.ERROR


def test_detect_reflects_credential_presence(tmp_path: Path) -> None:
    present = ClaudeProvider(credentials_path=_write_credentials(tmp_path))
    absent = ClaudeProvider(credentials_path=tmp_path / "absent.json")

    assert present.detect() is True
    assert absent.detect() is False


def test_plan_is_reported_from_credentials(tmp_path: Path) -> None:
    body = (FIXTURES / "claude_usage.json").read_text(encoding="utf-8")

    snapshot = _provider_with_body(tmp_path, body).fetch()

    assert snapshot.plan == "Max"


NOW = 1_800_000_000.0


def _write_refreshable(tmp_path: Path, *, expires_at_ms: float) -> Path:
    path = tmp_path / ".credentials.json"
    raw = {
        "claudeAiOauth": {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": expires_at_ms,
            "subscriptionType": "pro",
        }
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class _Server:
    """Routes the token and usage endpoints; records every request."""

    def __init__(
        self,
        *,
        token: Callable[[], httpx2.Response] | None = None,
        usage: Callable[[httpx2.Request], httpx2.Response] | None = None,
    ) -> None:
        self.requests: list[httpx2.Request] = []
        self._token = token or (
            lambda: httpx2.Response(
                200,
                json={"access_token": "new-access", "refresh_token": "new", "expires_in": 3600},
            )
        )
        body = (FIXTURES / "claude_usage.json").read_text(encoding="utf-8")
        self._usage = usage or (lambda request: httpx2.Response(200, text=body))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            return self._token()
        return self._usage(request)

    @property
    def usage_tokens(self) -> list[str]:
        return [
            r.headers["Authorization"].removeprefix("Bearer ")
            for r in self.requests
            if str(r.url) != TOKEN_URL
        ]

    @property
    def refreshes(self) -> int:
        return sum(str(r.url) == TOKEN_URL for r in self.requests)


def _refreshable_provider(path: Path, server: _Server) -> ClaudeProvider:
    return ClaudeProvider(credentials_path=path, client=_client(server), now=lambda: NOW)


def test_expired_token_is_refreshed_before_the_usage_request(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 - 1)
    server = _Server()

    snapshot = _refreshable_provider(path, server).fetch()

    assert snapshot.status is ProviderStatus.OK
    assert snapshot.plan == "Pro"
    assert server.refreshes == 1
    assert server.usage_tokens == ["new-access"]
    assert json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == (
        "new-access"
    )


def test_valid_token_is_not_refreshed(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 + 60_000)
    server = _Server()

    assert _refreshable_provider(path, server).fetch().status is ProviderStatus.OK
    assert server.refreshes == 0


def test_rejected_access_token_is_refreshed_and_retried(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 + 60_000)
    body = (FIXTURES / "claude_usage.json").read_text(encoding="utf-8")

    def usage(request: httpx2.Request) -> httpx2.Response:
        if request.headers["Authorization"] == "Bearer old-access":
            return httpx2.Response(401)
        return httpx2.Response(200, text=body)

    server = _Server(usage=usage)

    assert _refreshable_provider(path, server).fetch().status is ProviderStatus.OK
    assert server.usage_tokens == ["old-access", "new-access"]


def test_rejected_refresh_token_is_not_retried(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 - 1)
    server = _Server(token=lambda: httpx2.Response(400))
    provider = _refreshable_provider(path, server)

    first = provider.fetch()
    second = provider.fetch()

    assert first.status is ProviderStatus.EXPIRED
    assert second.status is ProviderStatus.EXPIRED
    assert len(server.requests) == 1


def test_transient_refresh_failure_reports_error(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 - 1)
    server = _Server(token=lambda: httpx2.Response(503))
    provider = _refreshable_provider(path, server)

    snapshot = provider.fetch()
    provider.fetch()

    assert snapshot.status is ProviderStatus.ERROR
    assert snapshot.message == "Token refresh failed"
    assert server.refreshes == 2
    assert server.usage_tokens == []


def test_refresh_network_error_reports_error(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 - 1)

    def token() -> httpx2.Response:
        raise httpx2.ConnectError("offline")

    snapshot = _refreshable_provider(path, _Server(token=token)).fetch()

    assert snapshot.status is ProviderStatus.ERROR


def test_rejected_token_without_refresh_is_not_requested_again(tmp_path: Path) -> None:
    server = _Server(usage=lambda request: httpx2.Response(401))
    provider = ClaudeProvider(credentials_path=_write_credentials(tmp_path), client=_client(server))

    assert provider.fetch().status is ProviderStatus.EXPIRED
    assert provider.fetch().status is ProviderStatus.EXPIRED
    assert len(server.requests) == 1


def test_still_rejected_after_refresh_reports_expired(tmp_path: Path) -> None:
    path = _write_refreshable(tmp_path, expires_at_ms=NOW * 1000 + 60_000)
    server = _Server(usage=lambda request: httpx2.Response(401))

    assert _refreshable_provider(path, server).fetch().status is ProviderStatus.EXPIRED
    assert server.usage_tokens == ["old-access", "new-access"]


def test_keychain_credentials_are_never_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_module.sys, "platform", "darwin")
    blob = json.dumps(
        {"claudeAiOauth": {"accessToken": "kc", "refreshToken": "r", "expiresAt": 1}}
    ).encode()
    server = _Server()
    provider = ClaudeProvider(
        credentials_path=tmp_path / "absent.json",
        keychain_credentials_provider=lambda _: [("Claude Code-credentials", blob)],
        client=_client(server),
        now=lambda: NOW,
    )

    assert provider.fetch().status is ProviderStatus.EXPIRED
    assert server.requests == []
