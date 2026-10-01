from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

import quotabubble.providers.claude_auth as auth_module
from quotabubble.providers.claude_auth import (
    CLIENT_ID,
    TOKEN_URL,
    RefreshResult,
    refresh_credentials_file,
)

NOW_MS = 1_800_000_000_000.0


def _write(tmp_path: Path, oauth: dict | None = None) -> Path:
    path = tmp_path / ".credentials.json"
    raw = {
        "claudeAiOauth": {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "refreshTokenExpiresAt": 2,
            "scopes": ["user:profile", "user:inference"],
            "subscriptionType": "pro",
            "rateLimitTier": "default",
            **(oauth or {}),
        },
        "organizationUuid": "org-1",
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _ok(**extra: object) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    body.update(extra)
    return lambda request: httpx2.Response(200, json=body)


def test_refresh_posts_the_cli_request_and_saves_the_new_pair(tmp_path: Path) -> None:
    path = _write(tmp_path)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _ok(refresh_token_expires_in=86400)(request)

    result = refresh_credentials_file(path, _client(handler), now_ms=NOW_MS)

    assert result is RefreshResult.REFRESHED
    assert str(seen[0].url) == TOKEN_URL
    assert json.loads(seen[0].content) == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "client_id": CLIENT_ID,
        "scope": "user:profile user:inference",
    }
    saved = json.loads(path.read_text(encoding="utf-8"))
    oauth = saved["claudeAiOauth"]
    assert oauth["accessToken"] == "new-access"
    assert oauth["refreshToken"] == "new-refresh"
    assert oauth["expiresAt"] == int(NOW_MS) + 3_600_000
    assert oauth["refreshTokenExpiresAt"] == int(NOW_MS) + 86_400_000
    assert oauth["subscriptionType"] == "pro"
    assert oauth["rateLimitTier"] == "default"
    assert saved["organizationUuid"] == "org-1"
    assert [p.name for p in tmp_path.iterdir()] == [".credentials.json"]


def test_scope_is_omitted_without_stored_scopes(tmp_path: Path) -> None:
    path = _write(tmp_path, {"scopes": None})
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return _ok()(request)

    refresh_credentials_file(path, _client(handler), now_ms=NOW_MS)

    assert "scope" not in bodies[0]


def test_unrotated_refresh_token_is_kept(tmp_path: Path) -> None:
    path = _write(tmp_path)

    result = refresh_credentials_file(path, _client(_ok(refresh_token=None)), now_ms=NOW_MS)

    oauth = json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]
    assert result is RefreshResult.REFRESHED
    assert oauth["refreshToken"] == "old-refresh"
    assert oauth["refreshTokenExpiresAt"] == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_refused_refresh_token_is_rejected(tmp_path: Path, status: int) -> None:
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = refresh_credentials_file(
        path, _client(lambda request: httpx2.Response(status)), now_ms=NOW_MS
    )

    assert result is RefreshResult.REJECTED
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx2.Response(429),
        lambda request: httpx2.Response(503),
        lambda request: httpx2.Response(200, text="not json"),
        lambda request: httpx2.Response(200, json={"access_token": "x"}),
        lambda request: httpx2.Response(200, json=["unexpected"]),
    ],
)
def test_transient_or_malformed_responses_fail(
    tmp_path: Path, handler: Callable[[httpx2.Request], httpx2.Response]
) -> None:
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")

    assert refresh_credentials_file(path, _client(handler), now_ms=NOW_MS) is RefreshResult.FAILED
    assert path.read_text(encoding="utf-8") == before


def test_network_error_fails(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    result = refresh_credentials_file(_write(tmp_path), _client(handler), now_ms=NOW_MS)

    assert result is RefreshResult.FAILED


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"claudeAiOauth": {}}'])
def test_missing_refresh_token_is_rejected_without_a_request(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / ".credentials.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request expected")

    assert refresh_credentials_file(path, _client(handler), now_ms=NOW_MS) is RefreshResult.REJECTED


def test_file_removed_during_refresh_is_not_recreated(tmp_path: Path) -> None:
    path = _write(tmp_path)

    def handler(request: httpx2.Request) -> httpx2.Response:
        path.unlink()
        return _ok()(request)

    assert refresh_credentials_file(path, _client(handler), now_ms=NOW_MS) is RefreshResult.FAILED
    assert not path.exists()


def test_write_failure_fails_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")

    def broken_replace(src: str, dst: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(auth_module.os, "replace", broken_replace)

    assert refresh_credentials_file(path, _client(_ok()), now_ms=NOW_MS) is RefreshResult.FAILED
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == [".credentials.json"]
