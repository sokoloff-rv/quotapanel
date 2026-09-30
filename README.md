# QuotaPanel

Windows-панель с оставшимися квотами Codex и Claude в свободной левой части
панели задач. Рядом с каждой квотой показан отсчёт до сброса. Инструкция,
ограничения прототипа и сборка: [QUOTAPANEL.md](QUOTAPANEL.md).

Этот форк основан на [QuotaBubble](https://github.com/izzet/quotabubble),
лицензия MIT и исходное уведомление об авторских правах сохранены.
Основная ветка форка — `master`.

Ниже сохранена документация исходного приложения QuotaBubble; его команды
установки относятся к оригинальному проекту.

# QuotaBubble

[![ci](https://github.com/izzet/quotabubble/actions/workflows/ci.yml/badge.svg)](https://github.com/izzet/quotabubble/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/izzet/quotabubble/graph/badge.svg)](https://codecov.io/gh/izzet/quotabubble)
[![PyPI](https://img.shields.io/pypi/v/quotabubble)](https://pypi.org/project/quotabubble/)
[![Python versions](https://img.shields.io/pypi/pyversions/quotabubble)](https://pypi.org/project/quotabubble/)
[![License](https://img.shields.io/github/license/izzet/quotabubble)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**Your AI usage limits, always in sight.**

QuotaBubble is a small, frameless desktop widget that keeps the usage limits for your AI coding tools visible without opening a dashboard, terminal, or browser. It floats above your windows, fades to almost nothing when idle, and expands into a compact detail panel when you click it.

<p align="center">
  <img src="https://izzet.github.io/quotabubble/screenshot-compact.png?v=2" width="260" alt="Idle bubble">
  <img src="https://izzet.github.io/quotabubble/screenshot-expanded.png?v=2" width="300" alt="Expanded detail">
</p>

## Features

- **Frameless floating bubble** — translucent, rounded, always on top, and it never steals keyboard focus.
- **Fades away when idle** — a configurable low opacity keeps it unobtrusive; hover to bring it back.
- **Click to expand** — see each usage window, the percentage used, and a live reset countdown.
- **Drag anywhere** — move it across monitors; the position is remembered.
- **Tray icon** — show/hide, settings, open logs, and quit.
- **Resilient** — refreshes every 5 minutes, caches the last good values, and keeps showing them (dimmed) through transient network or rate-limit errors.
- **Local only** — it reuses the credentials your tools already store and talks straight to the providers. No telemetry, no QuotaBubble server.

## Supported providers

| Provider | Authentication |
| --- | --- |
| Claude Code | macOS Keychain or `~/.claude/.credentials.json` |
| Codex | `~/.codex/auth.json` |
| Google Antigravity | Windows Credential Manager, macOS Keychain, or Linux Secret Service |
| GitHub Copilot | Windows Credential Manager, macOS Keychain, or Linux Secret Service |
| Cursor | Cursor IDE `state.vscdb` / `auth.json` (`CURSOR_SESSION_TOKEN` override) |
| Grok (SuperGrok) | `~/.grok/auth.json` from `grok login` (`GROK_HOME` override) |
| Zed | Windows Credential Manager (sign in to Zed) |
| Kimi Code | Kimi Code CLI login in `~/.kimi-code` (`KIMI_CODE_HOME` override) |
| OpenCode | `~/.local/share/opencode/auth.json` / `opencode.jsonc` (`OPENCODE_API_KEY` override) or API key |
| DeepSeek | API key |
| OpenRouter | API key |
| Z.ai (GLM Coding Plan) | API key (`Z_AI_API_KEY` override) |

Kimi Code runs separate regional services (`kimi.com` and `kimi.ai`): the login is only ever sent to the host the CLI itself is configured for (override with `KIMI_CODE_BASE_URL`). QuotaBubble never refreshes the CLI's login; when it expires, open Kimi Code to renew it.

Providers are detected automatically. Enable or disable them in Settings; API keys are entered there and validated inline with a **Test** button. Antigravity and Copilot reuse their existing OS keychain sign-ins, including Linux Secret Service. Cursor reuses the IDE session token from `state.vscdb` or `auth.json` (or `CURSOR_SESSION_TOKEN`).

## Install

### Windows

With [winget](https://learn.microsoft.com/windows/package-manager/winget/):

```powershell
winget install Izzet.QuotaBubble
```

Or with [Chocolatey](https://community.chocolatey.org/packages/quotabubble):

```powershell
choco install quotabubble
```

Or with [Scoop](https://scoop.sh):

```powershell
scoop bucket add izzet https://github.com/izzet/scoop-bucket
scoop install izzet/quotabubble
```

Or download the latest `QuotaBubble-windows-x64.zip` from the [Releases page](https://github.com/izzet/quotabubble/releases), unzip it, and run `QuotaBubble.exe`.

> The binary is not code-signed yet, so Windows SmartScreen may warn you. Choose **More info → Run anyway**.

### Linux

There are two Linux install paths:

| Desktop | Install method | What runs |
| --- | --- | --- |
| Ubuntu GNOME 24.04 (amd64) | Release `.deb` | GNOME Shell extension and background service |
| Linux Mint 22.3 Cinnamon (X11, amd64) | `pipx` | Standalone Qt bubble; tested locally |
| Other X11 desktops on modern glibc-based Linux (for example Debian, Fedora, openSUSE, or Arch) | `pipx` | Standalone Qt bubble; expected to work but not yet tested on those distributions |

For the standalone bubble, install [pipx](https://pipx.pypa.io/) and Python 3.11 or newer, then run:

```bash
pipx install quotabubble
quotabubble
```

This is the path for Linux Mint Cinnamon, MATE, and Xfce. The `.deb` below installs a GNOME
extension, so it does not provide the standalone `quotabubble` command on Mint. On non-GNOME
desktops, add `quotabubble` to your desktop's Startup Applications if you want it at login; the
Settings launch-at-login switch currently starts the GNOME service.

The standalone bubble needs an X11 session for reliable positioning and always-on-top behavior.
On Wayland, the compositor controls top-level window placement, so dragging and restoring the
bubble's position may not work as expected. Qt documents this
[Wayland limitation](https://doc.qt.io/qt-6.8/application-windows.html#wayland-peculiarities).
Some distributions may also need system libraries for
[Qt's X11 platform plugin](https://doc.qt.io/qt-6/linux-requirements.html).
API-key storage uses the desktop Secret Service when available. On a fresh profile, create or
unlock the default keyring if prompted; an unanswered setup prompt can hold up startup.

For the Ubuntu GNOME extension, download `QuotaBubble-linux-amd64.deb` from the matching
[GitHub Release](https://github.com/izzet/quotabubble/releases), then install it:

```bash
sudo apt install ./QuotaBubble-linux-amd64.deb
gnome-extensions enable quotabubble@izzet.dev
```

The extension activates the user-session service on demand. Open `quotabubble-settings` to configure providers, notifications, and launch at login.

### macOS

Download the matching disk image from the [Releases page](https://github.com/izzet/quotabubble/releases):

- `QuotaBubble-macos-arm64.dmg` for Apple Silicon Macs.
- `QuotaBubble-macos-x64.dmg` for Intel Macs.

Install or upgrade with Homebrew after adding the first-party tap:

```bash
brew install --cask izzet/tap/quotabubble
```

Open the disk image and drag **QuotaBubble** to **Applications**. The initial macOS builds are not
yet Developer ID signed or notarized; Control-click the app, choose **Open**, then confirm the
first-launch dialog. Signing and notarization will be added in a later release.

### From source (Python 3.11+)

```bash
git clone https://github.com/izzet/quotabubble.git
cd quotabubble
uv tool install --editable .
quotabubble
```

## Usage

- **Idle** — a faint bubble; hover to make it fully visible.
- **Click** — expand the detail panel; click again to collapse.
- **Drag** — grab and move it anywhere, including across monitors.
- **Right-click / tray** — Settings, Open logs, Show/Hide, Quit.
- **Launch at login** — enable it in Settings.

## Settings

Idle opacity, fade delay, refresh interval (default 5 minutes), show percentage used vs. remaining, launch at login, and which providers are visible.

## Where things live

On Windows:

| Path | Purpose |
| --- | --- |
| `%LOCALAPPDATA%\quotabubble\settings.json` | Settings |
| `%LOCALAPPDATA%\quotabubble\Cache\last_good.json` | Last-good snapshots |
| `%LOCALAPPDATA%\quotabubble\Logs\quotabubble.log` | Rotating log |

On macOS and Linux these map to the standard platform directories.

## Development

```bash
uv sync
uv run ruff check src tests
uv run pytest -q
uv run pytest -q --cov=src/quotabubble --cov-report=term-missing  # with coverage
```

CI gates on coverage (currently 80% minimum, see `[tool.coverage.report]` in `pyproject.toml`) and uploads results to Codecov.

See [AGENTS.md](AGENTS.md) for architecture and contribution conventions. The provider layer (`src/quotabubble/providers/`) is pure Python behind a `Provider` protocol, and platform specifics live in `src/quotabubble/platform/`.

## Status

Windows, macOS, and Ubuntu GNOME 24.04 (amd64) have native release artifacts. Linux Mint 22.3
Cinnamon on X11 has been tested with the standalone Python-package install; other X11 desktops
listed above are not yet tested. Claude, Codex, Cursor, OpenCode, DeepSeek, OpenRouter, Kimi Code,
Z.ai, and Grok work anywhere. Zed currently works on Windows only. Antigravity and Copilot reuse
Windows Credential Manager, macOS Keychain, and Linux Secret Service credentials. API keys entered
in Settings are stored in the OS credential store via [`keyring`](https://pypi.org/project/keyring/);
if no OS keyring backend is available, they fall back to the local settings file.

## License

[MIT](LICENSE)
