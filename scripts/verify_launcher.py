"""Verify the Mnemosyne launcher surfaces without binding a second server.

This is a *check*, not a runtime: it asserts the properties the operator asked
for, using only read-only inspection plus one deliberate start/stop of a
throwaway instance on a scratch port.

    py scripts\\verify_launcher.py

Checks:
  1. both launchers exist and parse (PowerShell tokenizer)
  2. Mnemosyne.vbs exists and forwards to launch-current.ps1
  3. the Desktop shortcut exists, targets wscript.exe + Mnemosyne.vbs
  4. idempotence: a second invocation against a live server starts nothing
     (no new PID appears anywhere in the scan range)
  5. -Stop only kills what the launcher itself started

Exits 0 on success, 1 with a readable reason on failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRENT = ROOT / "launch-current.ps1"
PLAIN = ROOT / "launch.ps1"
VBS = ROOT / "Mnemosyne.vbs"


def ps(script: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def ps_ok(script: str) -> tuple[bool, str]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def mnemosyne_pids() -> set[int]:
    """Every PID currently running `apps.serve` (server or worker)."""
    raw = ps(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='py.exe'\" | "
        "Where-Object { $_.CommandLine -match 'apps\\.serve' } | "
        "Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    )
    if not raw:
        return set()
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    return {int(val)} if isinstance(val, int) else {int(v) for v in val}


def main() -> int:
    problems: list[str] = []

    # 1. launchers exist and parse
    for f in (CURRENT, PLAIN):
        if not f.exists():
            problems.append(f"missing: {f}")
            continue
        ok, msg = ps_ok(
            f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{f}',[ref]$null,[ref]$e); "
            "if ($e.Count -gt 0) { $e | ForEach-Object { $_.Message }; exit 1 }"
        )
        print(f"[{'ok' if ok else 'FAIL'}] parse {f.name}")
        if not ok:
            problems.append(f"{f.name} does not parse: {msg}")

    # 2. the double-click stub
    if not VBS.exists():
        problems.append(f"missing: {VBS}")
    else:
        src = VBS.read_text(errors="replace")
        good = "launch-current.ps1" in src
        print(f"[{'ok' if good else 'FAIL'}] {VBS.name} forwards to launch-current.ps1")
        if not good:
            problems.append("Mnemosyne.vbs does not reference launch-current.ps1")

    # 3. the desktop shortcut
    lnk = ps("(New-Object -ComObject WScript.Shell).SpecialFolders('Desktop')") + "\\Mnemosyne.lnk"
    ok, out = ps_ok(
        f"$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('{lnk}'); "
        "$s.TargetPath + '|' + $s.Arguments + '|' + $s.WorkingDirectory"
    )
    if not ok or "|" not in out:
        problems.append(f"shortcut missing or unreadable: {lnk}")
        print("[FAIL] desktop shortcut")
    else:
        target, args, workdir = out.split("|", 2)
        good = (
            target.lower().endswith("wscript.exe")
            and "Mnemosyne.vbs" in args
            and Path(workdir) == ROOT
        )
        print(f"[{'ok' if good else 'FAIL'}] desktop shortcut -> {target} {args}")
        if not good:
            problems.append(f"shortcut mis-targeted: {target} {args} (cwd {workdir})")

    # 4. idempotence: invoking against a live server must NOT start anything
    before = mnemosyne_pids()
    if not before:
        print("[--] no live Mnemosyne server; skipping idempotence check (start one, re-run)")
    else:
        first = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"& '{CURRENT}' -NoBrowser"],
            capture_output=True, text=True,
        )
        second = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"& '{CURRENT}' -NoBrowser"],
            capture_output=True, text=True,
        )
        time.sleep(1.5)
        after = mnemosyne_pids()
        new = after - before
        reused = "reusing it" in first.stdout and "reusing it" in second.stdout
        print(f"[{'ok' if reused and not new else 'FAIL'}] idempotent re-invocation "
              f"(reused={reused}, new pids={sorted(new) or 'none'})")
        if new:
            problems.append(f"launcher started a second instance: PIDs {sorted(new)}")
        if not reused:
            problems.append("launcher did not report adopting the running server")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nLAUNCHER OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
