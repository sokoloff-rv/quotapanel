"""Refresh Claude Code's OAuth token in place, the same way the CLI does.

Refresh tokens are single-use: the server rotates them on every refresh, so the
new pair must be written back to Claude Code's credentials file or the CLI is
signed out. Every other field in the file is preserved.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from enum import StrEnum
from pathlib import Path

import httpx2

from quotabubble.providers.base import as_number

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
logger = logging.getLogger(__name__)


class RefreshResult(StrEnum):
    REFRESHED = "refreshed"
    # The server refused this refresh token; retrying it can never succeed.
    REJECTED = "rejected"
    # Network trouble, rate limiting, or a server error; worth retrying later.
    FAILED = "failed"


def _read_json(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _oauth_section(raw: dict | None) -> dict | None:
    oauth = raw.get("claudeAiOauth") if raw is not None else None
    return oauth if isinstance(oauth, dict) else None


def _write_atomic(path: Path, raw: dict) -> None:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def refresh_credentials_file(
    path: Path, client: httpx2.Client, *, now_ms: float
) -> RefreshResult:
    """Exchange the file's refresh token for a new pair and write it back."""
    oauth = _oauth_section(_read_json(path))
    refresh_token = oauth.get("refreshToken") if oauth is not None else None
    if not isinstance(refresh_token, str) or not refresh_token:
        return RefreshResult.REJECTED

    body = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    scopes = oauth.get("scopes")
    if isinstance(scopes, list) and scopes and all(isinstance(s, str) for s in scopes):
        body["scope"] = " ".join(scopes)

    try:
        response = client.post(TOKEN_URL, json=body)
    except httpx2.HTTPError as exc:
        logger.warning("Claude token refresh failed: %s", exc.__class__.__name__)
        return RefreshResult.FAILED
    if response.status_code in (400, 401, 403):
        logger.warning("Claude token refresh rejected (HTTP %d)", response.status_code)
        return RefreshResult.REJECTED
    if response.status_code != 200:
        logger.warning("Claude token refresh failed (HTTP %d)", response.status_code)
        return RefreshResult.FAILED

    try:
        payload = response.json()
    except ValueError:
        payload = None
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    expires_in = as_number(payload.get("expires_in")) if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token or expires_in is None:
        logger.warning("Claude token refresh returned an unexpected response")
        return RefreshResult.FAILED
    new_refresh = payload.get("refresh_token")
    refresh_expires_in = as_number(payload.get("refresh_token_expires_in"))

    # Re-read right before writing so fields another writer changed meanwhile survive.
    raw = _read_json(path)
    latest = _oauth_section(raw)
    if raw is None or latest is None:
        return RefreshResult.FAILED
    latest["accessToken"] = access_token
    if isinstance(new_refresh, str) and new_refresh:
        latest["refreshToken"] = new_refresh
    latest["expiresAt"] = int(now_ms + expires_in * 1000)
    if refresh_expires_in is not None:
        latest["refreshTokenExpiresAt"] = int(now_ms + refresh_expires_in * 1000)
    try:
        _write_atomic(path, raw)
    except OSError as exc:
        logger.warning("could not save refreshed Claude token: %s", exc.__class__.__name__)
        return RefreshResult.FAILED
    logger.info("refreshed Claude Code OAuth token")
    return RefreshResult.REFRESHED
