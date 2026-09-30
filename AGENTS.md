# QuotaBubble — agent guidelines

## Workflow

- **Never commit directly to `master`.** Create a branch per item: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- Push the branch and **open a pull request** describing the change.
- **Wait for review.** Address review comments by pushing follow-up commits to the same branch.
- Merge **only after explicit approval**, and **never squash-merge** — use a merge commit (default).

## Development

- Package manager: `uv`.
- Lint: `uv run ruff check src tests`.
- Test: `uv run pytest -q`.
- Coverage: `uv run pytest -q --cov=src/quotabubble --cov-report=term-missing`. CI gates at `fail_under` in `pyproject.toml` `[tool.coverage.report]` (80%, ratchet up as coverage improves) and uploads to Codecov.
- **`main.py` is composition, not logic — and this is a policy, not a shortcut.** It's excluded from coverage (`[tool.coverage.run] omit`, `codecov.yml`'s `ignore`) and should only ever contain `QApplication` setup, object construction, and `.connect()` signal wiring. If a change to it needs a conditional, a merge, a transformation, or anything you'd want a test for, that logic belongs in a plain function or class in `app/` or elsewhere instead (see `app/providers.py`'s `merge_selected_snapshots` for the pattern: `AppState` and friends are plain Python, not `QObject`s, so almost nothing here actually needs Qt to be testable). Everything *around* main.py is expected to be thoroughly unit tested — the exclusion only covers genuine wiring, never an excuse to leave real logic uncovered.
- Providers live in `src/quotabubble/providers/` behind the `Provider` protocol; they must not import Qt.
- Platform-specific behavior (window flags, credential stores, autostart) lives in `src/quotabubble/platform/`.
- The UI (`src/quotabubble/ui/`) renders `UsageSnapshot` objects and must not perform I/O or HTTP itself.

## Runtime notes

- Config: `%LOCALAPPDATA%\quotabubble\settings.json`.
- Last-good cache: `%LOCALAPPDATA%\quotabubble\Cache\last_good.json`.
- Logs: `%LOCALAPPDATA%\quotabubble\Logs\quotabubble.log` (rotating).
- Only one instance runs; a second launch re-shows the first.
- Poll interval defaults to 5 minutes; transient errors fall back to the last-good snapshot.

## Distribution

`release.yml` runs on a `v*` tag (or manual `workflow_dispatch`) in a fan-in shape:

```
build (matrix: windows-latest, macos-15-intel x64, macos-15 arm64,
       ubuntu-24.04)
  each: checkout -> test -> PyInstaller build -> package -> upload-artifact
  (no per-OS entry publishes anything itself)
        |
        v
github-release (needs: build -- the whole matrix)
  downloads every artifact, creates ONE GitHub Release with all of them attached
        |
   +----+----+---------------------+
   v         v                         v
 winget   chocolatey                homebrew
 needs:   needs:                     needs: github-release; updates
 github-  github-                    the first-party cask tap
 release  release

pypi (fully independent -- own checkout/build/publish, no needs on
      build or github-release at all; see below for why)
```

- **`build`:** a matrix job (`strategy.matrix.include`) producing the Windows ZIP plus `QuotaBubble-macos-x64.dmg` from `macos-15-intel` and `QuotaBubble-macos-arm64.dmg` from `macos-15`. macOS builds generate `assets/quotabubble.icns`, use `packaging/quotabubble.macos.spec` to make an app bundle, then package it with `hdiutil`. Platform specs follow `quotabubble.<platform>.spec`; the Linux `QuotaBubble-linux-amd64.deb` uses `packaging/quotabubble.linux.spec` and is built on `ubuntu-24.04`. That runner is pinned rather than `ubuntu-latest` on purpose: PyInstaller binaries link the build host's glibc, so the runner must match the Ubuntu GNOME 24.04 target we ship. Adding another platform means adding its own `include` entry and packaging step — nothing downstream needs to change, since `github-release` and everything after it only cares about whatever artifacts the matrix produced.
- **macOS signing:** initial macOS artifacts use PyInstaller's ad hoc signatures. Once Apple Developer credentials are available, add Developer ID signing with the hardened runtime, notarization, and ticket stapling to the macOS packaging step before uploading the artifact.
- **`github-release`:** fans in the whole matrix, downloads all artifacts (`actions/download-artifact@v4` with no `name:` filter + `merge-multiple: true`), and creates the GitHub Release with everything attached in one `softprops/action-gh-release@v2` call. Nothing else creates or touches the release.
- **Homebrew:** the `homebrew` job (`needs: github-release`) downloads the two macOS DMGs, computes their SHA-256 values, renders `packaging/homebrew/quotabubble.rb.tmpl`, validates it with `brew audit --cask --strict`, and commits it to `izzet/homebrew-tap`. It needs a `HOMEBREW_TAP_TOKEN` repository secret: a fine-grained token with Contents read/write access to that tap only. Users install it with `brew install --cask izzet/tap/quotabubble`. The first-party tap can distribute initial unsigned builds; submitting to the official `homebrew/cask` waits for Developer ID signing and notarization.
- **winget:** the manifest for a version lives in `packaging/winget/<version>/` and is validated with `winget validate --manifest <dir>`. The `winget` job (`needs: github-release`) runs `komac update Izzet.QuotaBubble` to open a PR against `microsoft/winget-pkgs`.
  - Needs a repository secret `WINGET_TOKEN`: a classic personal access token with `public_repo` (the default `GITHUB_TOKEN` cannot open PRs on another repository). Without it the job is skipped.
  - First submission is manual: `komac submit packaging/winget/<version> --yes --token <PAT>`.
  - Committing to `microsoft/winget-pkgs` requires signing the Microsoft CLA once by commenting `@microsoft-github-policy-service agree` on the PR.
  - In a winget multi-file manifest the default-locale file must declare `ManifestType: defaultLocale` (not `locale`), and each file should start with a `# yaml-language-server: $schema=...` header.
- **Chocolatey:** the package lives in `packaging/chocolatey/` (`quotabubble.nuspec`, `tools/chocolateyinstall.ps1`). The `chocolatey` job (`needs: github-release`) downloads that version's release zip, computes its SHA256, substitutes the `__URL__`/`__CHECKSUM__` placeholders in `chocolateyinstall.ps1`, then runs `choco pack` and `choco push`.
  - Needs a repository secret `CHOCOLATEY_API_KEY` (from a chocolatey.org account). Without it the job is skipped.
  - Unlike winget, there's no separate "first submission" step — `choco push` creates the package on first use. New packages go through Chocolatey's moderation queue before showing up in default search/`choco install` from the community feed.
- **Microsoft Store (MSIX):** the Windows `build` entry also runs `scripts/build_msix.py`, which stages `dist/QuotaBubble/` with `packaging/msix/AppxManifest.xml.tmpl` and generated logos and packs it with `makeappx` (Windows SDK, preinstalled on `windows-latest`). It is uploaded like the other archives and attached to the GitHub Release, whose notes say it is the Store submission package and is not signed for direct installation (winget, Chocolatey, and Scoop all use the ZIP by explicit name, so the extra asset doesn't affect them).
  - The Store re-signs MSIX packages after certification, so no code-signing certificate is needed. Submission to Partner Center is manual for now: download `QuotaBubble-windows-x64.msix` from the GitHub Release and upload it to the QuotaBubble submission.
  - The manifest's identity (`Name`, `Publisher`, `PublisherDisplayName`) must match the values Partner Center shows for the reserved product. Package versions are four-part and the Store requires the last part to be `0`, so `X.Y.Z` becomes `X.Y.Z.0`; the version must increase with every submission.
  - Packaged apps run with `runFullTrust` and behave differently from the ZIP build in places (registry-based autostart, `%LOCALAPPDATA%` writes are redirected), so changes to autostart or file locations need testing in the packaged form too.
- **PyPI:** the `pypi` job runs `uv build`, a smoke test of the built wheel (installs it into a throwaway venv and imports a non-Qt module — not the full pytest suite, since PySide6 needs system libraries this runner doesn't have and the commit already passed the full suite via `ci.yml` before merge), then `uv publish --trusted-publishing always`. No separate manual step or token; auth is via PyPI Trusted Publishing (OIDC), scoped on pypi.org to this exact repo + workflow filename (`release.yml`) + `pypi` environment. If that job's auth fails, the Trusted Publisher config on pypi.org almost certainly still points at the old `publish.yml` (removed — folded into `release.yml`) and needs updating to `release.yml`. Bump the version in `pyproject.toml` per release.
  - Deliberately has **no `needs:`** on `build`/`github-release`: the wheel is `py3-none-any` (pure Python, no compiled extensions), identical regardless of which OS built it, so it must never depend on an unrelated platform-specific binary build succeeding.
