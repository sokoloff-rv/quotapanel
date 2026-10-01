from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx2
from pydantic import BaseModel

from quotabubble.credentials import enumerate_generic_credentials
from quotabubble.providers.base import (
    Credits,
    ProviderStatus,
    UsageSnapshot,
    UsageWindow,
    as_number,
    format_plan,
    parse_retry_after,
)
from quotabubble.providers.claude_auth import RefreshResult, refresh_credentials_file

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"
REQUEST_TIMEOUT_SECONDS = 15.0
_WINDOW_ORDER = {"session": 0, "weekly": 1}
KEYCHAIN_CREDENTIAL_HINT = "Claude Code-credentials"


class _Credentials(BaseModel):
    access_token: str
    subscription_type: str | None = None
    refresh_token: str | None = None
    expires_at_ms: float | None = None


class _Bucket(BaseModel):
    utilization: float
    resets_at: datetime | None = None


class _SpendAmount(BaseModel):
    amount_minor: float
    exponent: int = 0
    currency: str | None = None


class _Spend(BaseModel):
    enabled: bool = False
    used: _SpendAmount | None = None
    limit: _SpendAmount | None = None
    percent: float | None = None


class _ScopeModel(BaseModel):
    display_name: str | None = None


class _Scope(BaseModel):
    model: _ScopeModel | None = None


class _Limit(BaseModel):
    kind: str | None = None
    group: str | None = None
    percent: float | str | None = None
    severity: str | None = None
    resets_at: datetime | None = None
    scope: _Scope | None = None
    is_active: bool = False


class _UsageResponse(BaseModel):
    five_hour: _Bucket | None = None
    seven_day: _Bucket | None = None
    limits: list[_Limit] | None = None
    spend: _Spend | None = None


def default_credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def read_credentials(path: Path) -> _Credentials | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse_credentials(raw)


def _parse_credentials(raw: object) -> _Credentials | None:
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    subscription = oauth.get("subscriptionType")
    refresh = oauth.get("refreshToken")
    return _Credentials(
        access_token=token,
        subscription_type=subscription if isinstance(subscription, str) else None,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at_ms=as_number(oauth.get("expiresAt")),
    )


def read_keychain_credentials(
    credential_provider: Callable[[str], list[tuple[str, bytes]]] = enumerate_generic_credentials,
) -> _Credentials | None:
    for _target, blob in credential_provider(KEYCHAIN_CREDENTIAL_HINT):
        try:
            raw = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        credentials = _parse_credentials(raw)
        if credentials is not None:
            return credentials
    return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _scope_name(limit: _Limit) -> str | None:
    if limit.scope is None or limit.scope.model is None:
        return None
    name = (limit.scope.model.display_name or "").strip()
    if not name or _slug(name) in {"all-models", "all"}:
        return None
    return name


def _windows_from_limits(limits: list[_Limit]) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    for limit in limits:
        percent = as_number(limit.percent)
        if percent is None:
            continue
        if limit.kind == "session":
            windows.append(
                UsageWindow(
                    key="session",
                    label="5h",
                    short="5h",
                    used_pct=percent,
                    resets_at=limit.resets_at,
                    severity=limit.severity,
                    active=limit.is_active,
                )
            )
        elif limit.kind == "weekly_all":
            windows.append(
                UsageWindow(
                    key="weekly",
                    label="Weekly",
                    short="wk",
                    used_pct=percent,
                    resets_at=limit.resets_at,
                    severity=limit.severity,
                    active=limit.is_active,
                )
            )
        elif limit.kind == "weekly_scoped":
            name = _scope_name(limit)
            if name is None:
                continue
            windows.append(
                UsageWindow(
                    key=f"weekly_scoped.{_slug(name)}",
                    label=name,
                    used_pct=percent,
                    resets_at=limit.resets_at,
                    scope=name,
                    severity=limit.severity,
                    active=limit.is_active,
                )
            )
    return windows


def _windows_from_flat(parsed: _UsageResponse) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    if parsed.five_hour is not None:
        windows.append(
            UsageWindow(
                key="session",
                label="5h",
                short="5h",
                used_pct=parsed.five_hour.utilization,
                resets_at=parsed.five_hour.resets_at,
            )
        )
    if parsed.seven_day is not None:
        windows.append(
            UsageWindow(
                key="weekly",
                label="Weekly",
                short="wk",
                used_pct=parsed.seven_day.utilization,
                resets_at=parsed.seven_day.resets_at,
            )
        )
    return windows


def _usage_windows(parsed: _UsageResponse) -> list[UsageWindow]:
    windows = _windows_from_limits(parsed.limits or [])
    if not windows:
        windows = _windows_from_flat(parsed)
    windows.sort(key=lambda window: (_WINDOW_ORDER.get(window.key, 2), window.label))
    return windows


def _major(amount: _SpendAmount | None) -> float | None:
    if amount is None:
        return None
    return amount.amount_minor / 10**amount.exponent


def _credits(spend: _Spend | None) -> Credits | None:
    if spend is None or not spend.enabled:
        return None
    if spend.limit is None:
        percent = as_number(spend.percent)
        if percent is None:
            return None
        return Credits(display=f"{percent:.0f}% used", used_pct=percent)
    used = _major(spend.used)
    total = _major(spend.limit)
    if used is None or total is None:
        return None
    symbol = "$" if (spend.limit.currency or "USD") == "USD" else ""
    used_pct = as_number(spend.percent)
    if used_pct is None and total > 0:
        used_pct = used / total * 100
    return Credits(
        display=f"{symbol}{used:.2f} / {symbol}{total:.2f}",
        used_pct=used_pct,
    )


class ClaudeProvider:
    id = "claude"
    display_name = "Claude"
    uses_api_key = False

    def __init__(
        self,
        credentials_path: Path | None = None,
        keychain_credentials_provider: Callable[[str], list[tuple[str, bytes]]] | None = None,
        client: httpx2.Client | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._credentials_path = credentials_path or default_credentials_path()
        self._keychain_credentials_provider = (
            keychain_credentials_provider or enumerate_generic_credentials
        )
        self._client = client
        self._now = now
        self._rejected_access_token: str | None = None
        self._rejected_refresh_token: str | None = None

    def _load_credentials(self) -> tuple[_Credentials | None, bool]:
        """Return the credentials and whether they can be refreshed in place."""
        if sys.platform == "darwin":
            keychain_credentials = read_keychain_credentials(
                self._keychain_credentials_provider
            )
            if keychain_credentials is not None:
                return keychain_credentials, False
        return read_credentials(self._credentials_path), True

    def detect(self) -> bool:
        return self._load_credentials()[0] is not None

    def fetch(self) -> UsageSnapshot:
        credentials, refreshable = self._load_credentials()
        if credentials is None:
            return self._snapshot(
                ProviderStatus.NO_CREDENTIALS, "No Claude Code credentials found"
            )

        client = self._client or httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            if self._is_expired(credentials):
                refreshed = self._refresh(client, credentials, refreshable)
                if isinstance(refreshed, UsageSnapshot):
                    return refreshed
                credentials = refreshed
            response = self._get_usage(client, credentials)
            if response.status_code in (401, 403):
                self._rejected_access_token = credentials.access_token
                refreshed = self._refresh(client, credentials, refreshable)
                if isinstance(refreshed, UsageSnapshot):
                    return refreshed
                credentials = refreshed
                response = self._get_usage(client, credentials)
        except httpx2.HTTPError as exc:
            return self._snapshot(ProviderStatus.ERROR, str(exc))
        finally:
            if self._client is None:
                client.close()

        if response.status_code in (401, 403):
            self._rejected_access_token = credentials.access_token
            return self._expired()
        if response.status_code == 429:
            return self._snapshot(
                ProviderStatus.ERROR,
                "HTTP 429",
                parse_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code != 200:
            return self._snapshot(ProviderStatus.ERROR, f"HTTP {response.status_code}")

        try:
            parsed = _UsageResponse.model_validate(response.json())
        except ValueError:
            return self._snapshot(ProviderStatus.ERROR, "Unexpected usage response")

        return UsageSnapshot(
            provider=self.id,
            display_name=self.display_name,
            windows=_usage_windows(parsed),
            credits=_credits(parsed.spend),
            plan=format_plan(credentials.subscription_type),
        )

    def _is_expired(self, credentials: _Credentials) -> bool:
        if credentials.access_token == self._rejected_access_token:
            return True
        expires_at = credentials.expires_at_ms
        return expires_at is not None and expires_at <= self._now() * 1000

    def _get_usage(self, client: httpx2.Client, credentials: _Credentials) -> httpx2.Response:
        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "anthropic-beta": ANTHROPIC_BETA,
        }
        return client.get(USAGE_URL, headers=headers)

    def _refresh(
        self, client: httpx2.Client, credentials: _Credentials, refreshable: bool
    ) -> _Credentials | UsageSnapshot:
        """Return fresh credentials, or the snapshot to report when that is impossible."""
        refresh_token = credentials.refresh_token
        if (
            not refreshable
            or refresh_token is None
            or refresh_token == self._rejected_refresh_token
        ):
            return self._expired()
        result = refresh_credentials_file(
            self._credentials_path, client, now_ms=self._now() * 1000
        )
        if result is RefreshResult.REJECTED:
            self._rejected_refresh_token = refresh_token
            return self._expired()
        refreshed = read_credentials(self._credentials_path)
        if result is RefreshResult.FAILED or refreshed is None:
            return self._snapshot(ProviderStatus.ERROR, "Token refresh failed")
        return refreshed

    def _expired(self) -> UsageSnapshot:
        return self._snapshot(ProviderStatus.EXPIRED, "Sign in with Claude Code again")

    def _snapshot(
        self,
        status: ProviderStatus,
        message: str | None = None,
        retry_after: float | None = None,
    ) -> UsageSnapshot:
        return UsageSnapshot(
            provider=self.id,
            display_name=self.display_name,
            status=status,
            message=message,
            retry_after=retry_after,
        )
