"""Open the Mnemosyne console once, per port, as a real desktop window.

A disposable launcher surface, exactly like launch-current.ps1: it holds no
configuration of its own, adds no Mnemosyne semantic, and may be deleted without
touching anything authoritative. It only decides *which browser window* a URL
becomes.

WHY THIS EXISTS

launch-current.ps1 used `WScript.Shell.AppActivate("127.0.0.1:8000")` to re-focus
an already-open console tab. That can never match. Chrome/Edge's main window title
is the *page* title - console.html sets `<title>Mnemosyne - Operating Environment
</title>` - never the URL. Verified on this machine: 36 chrome processes, 23 with a
non-empty MainWindowTitle, zero containing "127.0.0.1". AppActivate therefore
returned false on every launch and the script fell through to `Start-Process $u`,
which opens a NEW tab each time.

THE INVARIANT

  one port  <->  one browser profile  <->  one window

A browser profile is its own instance with its own window. Second launch on the
same port reuses that profile, so the browser focuses the already-open window and
the tab is not duplicated - which is the property the operator asked for, and the
browser's own guarantee rather than a title-matching guess.

  - Chromium family (Chrome/Edge/Brave/Vivaldi/Chromium): `--app=<url>` for a
    chrome-less window, with `--user-data-dir=<root>/<port>`.
  - Anything else: a plain open, which a running browser already reuses.

STRICTLY BEST-EFFORT

Every failure path falls back to the URL the caller passed. This script must never
be the reason the console does not open: worst case it behaves exactly like the
old `Start-Process`.

    py scripts/mnemosyne_window.py http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.parse

ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                    "Mnemosyne", "browser")

# (exe name, argv template). `{url}` and `{profile}` are substituted.
CHROMIUM = {
    "chrome.exe": ("--app={url}", "--user-data-dir={profile}",
                   "--no-first-run", "--no-default-browser-check"),
    "msedge.exe": ("--app={url}", "--user-data-dir={profile}",
                   "--no-first-run", "--no-default-browser-check"),
    "brave.exe": ("--app={url}", "--user-data-dir={profile}",
                  "--no-first-run", "--no-default-browser-check"),
    "vivaldi.exe": ("--app={url}", "--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check"),
    "chromium.exe": ("--app={url}", "--user-data-dir={profile}",
                     "--no-first-run", "--no-default-browser-check"),
}


def _port(url: str, fallback: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        return str(parsed.port or (443 if parsed.scheme == "https" else 80))
    except ValueError:
        return fallback


def _find(exe: str) -> str | None:
    """Resolve a browser exe: PATH first, then the per-user install locations."""
    hit = shutil.which(exe)
    if hit:
        return hit
    for base in (os.environ.get("LOCALAPPDATA") or "",
                 os.environ.get("PROGRAMFILES") or "",
                 os.environ.get("PROGRAMFILES(X86)") or ""):
        if not base:
            continue
        for sub in ("Google/Chrome/Application", "Microsoft/Edge/Application",
                    "BraveSoftware/Brave-Browser/Application", "Vivaldi/Application"):
            cand = os.path.join(base, sub, exe)
            if os.path.isfile(cand):
                return cand
    return None


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8000"
    try:
        for exe, template in CHROMIUM.items():
            path = _find(exe)
            if not path:
                continue
            profile = os.path.join(ROOT, _port(url, "default"))
            os.makedirs(profile, exist_ok=True)
            argv_out = [path] + [a.format(url=url, profile=profile) for a in template]
            subprocess.Popen(argv_out, close_fds=True)
            return 0
        # No Chromium-family browser: a plain open. A running browser reuses its
        # own window, so this is a fallback, not a duplicate.
        os.startfile(url)  # noqa: S606 - a URL, not an executable
        return 0
    except Exception as exc:  # noqa: BLE001 - never break the launch path
        print(f"mnemosyne_window: falling back to a plain open ({exc})",
              file=sys.stderr)
        try:
            os.startfile(url)  # noqa: S606
        except Exception:  # noqa: BLE001
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
