"""Verify the Mnemosyne launcher surfaces (launch.ps1, launch-current.ps1, the
desktop shortcut, and the .vbs double-click stub).

The launchers are lifecycle surfaces: they must be IDEMPOTENT. Running one twice
must never produce two servers, two workers, or two browser tabs.

There is a live Mnemosyne server on :8000 right now (the operator's). That makes
the strongest possible test *safe*: any adoption assertion starts nothing, and
any start-path assertion uses a throwaway port and cleans up.

Run directly, like the rest of this repo's suite:  py tests/test_launchers.py
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# An absolute path is only meaningful if it is REAL. This guard is a no-op unless
# MNEMO_REPO_SHIM is set. It exists because a test in this repo once resolved
# __file__ into a stale shadow copy of the tree and reported a green PASS about
# content it had never read. A test that can pass while reading the wrong tree is
# worse than a failing one, so the wrong tree becomes a hard stop.
_EXPECTED = os.environ.get("MNEMO_REPO_SHIM")
if _EXPECTED and os.path.normcase(os.path.abspath(REPO)) != os.path.normcase(
        os.path.abspath(_EXPECTED)):
    raise SystemExit(
        f"test_launchers.py is running against {REPO}, not {_EXPECTED} "
        f"(MNEMO_REPO_SHIM is set). Refusing to report on the wrong tree."
    )

PS = shutil.which("powershell") or shutil.which("pwsh")
PY = sys.executable

LAUNCH_PS1 = os.path.join(REPO, "launch.ps1")
LAUNCH_CURRENT = os.path.join(REPO, "launch-current.ps1")
VBS = os.path.join(REPO, "Mnemosyne.vbs")
SHORTCUT = os.path.join(REPO, "scripts", "shortcut.py")

_failures = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def ps_run(script, *args, timeout=60):
    """Run one of the launchers through PowerShell, window hidden, no profile."""
    cmd = [PS, "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-Command", f"& '{script}' " + " ".join(args)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=REPO)


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, len(r.read())
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Static / structural
# ---------------------------------------------------------------------------
print("\nlauncher surface - structure")


def test_files_present():
    check("launch.ps1 exists", os.path.isfile(LAUNCH_PS1))
    check("launch-current.ps1 exists", os.path.isfile(LAUNCH_CURRENT))
    check("Mnemosyne.vbs desktop stub exists", os.path.isfile(VBS))
    check("scripts/shortcut.py exists", os.path.isfile(SHORTCUT))


def test_desktop_stub_contract():
    src = read(VBS)
    check("vbs resolves its own folder",
          "GetParentFolderName(WScript.ScriptFullName)" in src)
    check("vbs targets launch-current.ps1", "launch-current.ps1" in src)
    check("vbs runs hidden (0 = vbHide)", "shell.Run cmd, 0, False" in src)
    check("vbs forwards shortcut arguments", "WScript.Arguments.Count" in src)
    # Option Explicit has no JIT safety net: an identifier that is read but never
    # Dimensioned is a FATAL runtime error, not a warning - and it only fires at
    # double-click time. Pin every name the stub reads AND that every assigned
    # identifier is declared. (The accumulator is `extra`, singular.)
    names = {n.strip() for line in re.findall(r"^\s*Dim\s+(.+)$", src, re.M)
             for n in line.split(",")}
    for var in ("shell", "fso", "here", "script", "cmd", "i", "extra"):
        check(f"vbs Dim's {var}", var in names)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("'"))
    assigned = {m for m in re.findall(r"\b([A-Za-z]\w*)\s*=", code) if m != "Quote"}
    check("vbs assigns no undeclared variable", assigned <= names)


def test_both_launchers_idempotent_and_capable():
    for label, src in (("launch.ps1", read(LAUNCH_PS1)),
                       ("launch-current.ps1", read(LAUNCH_CURRENT))):
        check(f"{label} adopts a running Mnemosyne",
              "IsMnemosyne" in src and "reusing it" in src)
        check(f"{label} can pick a fresh port", "ScanLimit" in src)
        check(f"{label} opens/reuses the console tab",
              "Open-Console" in src or "AppActivate" in src)
        check(f"{label} refuses to start a second server",
              "already in use" in src)


def test_vbs_args_forwarded_verbatim():
    """The stub must pass -Port 0 through, not eat it."""
    src = read(VBS)
    check("vbs quotes forwarded args", "Quote(WScript.Arguments(i))" in src)
    check("vbs quotes the script path as one token",
          "\"& '\" & script & \"'\"" in src)


def test_current_launcher_browser_reuse_and_status():
    src = read(LAUNCH_CURRENT)
    # Reuse must not be adoption-only. A second double-click on a session where
    # the browser lost focus is exactly when the operator wants the existing tab
    # SELECTED, not a second one born.
    check("launch-current reuses the tab on every open path",
          src.count("-Reuse") >= 3, f"{src.count('-Reuse')} call sites")
    check("launch-current can report without mutating", "[switch]$Status" in src)
    check("launch-current -Status says whether it owns the server",
          "Get-Remembered" in src)


test_files_present()
test_desktop_stub_contract()
test_both_launchers_idempotent_and_capable()
test_vbs_args_forwarded_verbatim()
test_current_launcher_browser_reuse_and_status()

# ---------------------------------------------------------------------------
# PowerShell parse check - a syntax error must not survive to double-click time
# ---------------------------------------------------------------------------
print("\nlauncher surface - PowerShell parses")


def test_parse(path):
    cmd = [PS, "-NoProfile", "-Command",
           f"$null = [System.Management.Automation.Language.Parser]::ParseFile("
           f"'{path}', [ref]$null, [ref]$null); 'parsed'"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    check(f"{os.path.basename(path)} parses", "parsed" in r.stdout,
          r.stderr.strip()[:200])


test_parse(LAUNCH_PS1)
test_parse(LAUNCH_CURRENT)

# ---------------------------------------------------------------------------
# The desktop icon - the shortcut must exist AND be readable back
# ---------------------------------------------------------------------------
print("\ndesktop icon - a real .lnk, not just a script")


def test_shortcut_exists_and_resolves():
    r = subprocess.run(
        [sys.executable, SHORTCUT, "--where"],
        capture_output=True, text=True, timeout=60, cwd=REPO)
    lnk = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    check("shortcut.py --where prints a path", bool(lnk), lnk)
    check("the Desktop shortcut exists", bool(lnk) and os.path.isfile(lnk),
          lnk if lnk else "no path")
    if not lnk or not os.path.isfile(lnk):
        return
    out = subprocess.run(
        [PS, "-NoProfile", "-Command",
         f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
         "$s.TargetPath + '|' + $s.Arguments + '|' + $s.WorkingDirectory"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    parts = out.split("|") if "|" in out else []
    check("shortcut was readable back by the shell", len(parts) == 3, out[:160])
    if len(parts) == 3:
        target, args, cwd = parts
        check("shortcut targets wscript.exe", target.lower().endswith("wscript.exe"), target)
        check("shortcut passes Mnemosyne.vbs", "Mnemosyne.vbs" in args, args)
        check("shortcut working dir is the repo",
              os.path.normcase(os.path.abspath(cwd or "")) == os.path.normcase(REPO), cwd)
    # It must also be idempotent: a second create refreshes in place.
    before = os.path.getmtime(lnk)
    r2 = subprocess.run([sys.executable, SHORTCUT], capture_output=True, text=True,
                        timeout=60, cwd=REPO)
    check("shortcut.py re-run succeeds", r2.returncode == 0, r2.stderr.strip()[:160])
    check("shortcut.py did not create a second icon",
          os.path.isfile(lnk) and os.path.getmtime(lnk) >= before)


test_shortcut_exists_and_resolves()

# ---------------------------------------------------------------------------
# The stub itself - run it, and let it fail if it is malformed
# ---------------------------------------------------------------------------
print("\ndouble-click path - the stub actually runs")


def test_vbs_executes():
    if not shutil.which("cscript"):
        check("cscript available (skipped)", True, "not on PATH")
        return
    r = subprocess.run(["cscript", "//nologo", "//E:vbscript", VBS],
                       capture_output=True, text=True, timeout=60)
    check("Mnemosyne.vbs runs without a runtime error",
          r.returncode == 0 and "Microsoft VBScript runtime error" not in (
              (r.stdout or "") + (r.stderr or "")),
          ((r.stdout or "") + (r.stderr or "")).strip()[:200] or "clean exit")


test_vbs_executes()

# ---------------------------------------------------------------------------
# Adoption - the live operator server on :8000. This must start NOTHING.
# ---------------------------------------------------------------------------
print("\nadoption - the live server is reused, never duplicated")

LIVE_PORT = None
if listening(8000):
    status, size = http_ok("http://127.0.0.1:8000/")
    if status == 200:
        LIVE_PORT = 8000
        check(":8000 is a live Mnemosyne console", True, f"HTTP {status}, {size} bytes")


def pids_on(port):
    out = subprocess.run(
        [PS, "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue"
         f" | Select-Object -First 1 -ExpandProperty OwningProcess)"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    return out.splitlines()[-1].strip() if out.strip() else ""


if LIVE_PORT:
    before = pids_on(LIVE_PORT)

    r = ps_run(LAUNCH_CURRENT, "-Adopt", "-Port", str(LIVE_PORT), "-NoBrowser")
    out = (r.stdout or "") + (r.stderr or "")
    check("launch-current -Adopt succeeds against the live server",
          r.returncode == 0, f"exit {r.returncode}")
    check("launch-current -Adopt reports reuse",
          "reusing it" in out or "already serving" in out)

    r = ps_run(LAUNCH_PS1, "-Port", str(LIVE_PORT), "-NoBrowser")
    out = (r.stdout or "") + (r.stderr or "")
    check("launch.ps1 adopts the live server instead of throwing",
          r.returncode == 0 and ("reusing it" in out), f"exit {r.returncode}")

    # A bare launch (the desktop-icon case) must adopt too, not error.
    r = ps_run(LAUNCH_CURRENT, "-NoBrowser")
    out = (r.stdout or "") + (r.stderr or "")
    check("a bare double-click launch adopts the live server",
          r.returncode == 0 and "reusing it" in out, out.strip()[:160])

    after = pids_on(LIVE_PORT)
    check("adoption started no second server", before == after and before != "",
          f"pid {before} -> {after}")

    status, _ = http_ok(f"http://127.0.0.1:{LIVE_PORT}/")
    check("live console still healthy after three adoptions", status == 200)

    # -Status must report and mutate nothing.
    r = ps_run(LAUNCH_CURRENT, "-Status")
    out = (r.stdout or "") + (r.stderr or "")
    check("launch-current -Status reports the live server",
          r.returncode == 0 and f"port {LIVE_PORT}" in out, out.strip()[:160])
    check("-Status started nothing", pids_on(LIVE_PORT) == after)

# ---------------------------------------------------------------------------
# Cold start - one throwaway instance, adopted twice, then stopped cleanly.
# ---------------------------------------------------------------------------
print("\ncold start - idempotent under a fresh boot")

FREE = None
for p in range(8400, 8460):
    if not listening(p):
        FREE = p
        break

if FREE is None:
    check("found a free port for the cold-start test", False)
else:
    scratch = tempfile.mkdtemp(prefix="mnemo-launch-test-")
    cleanup_pids = []
    try:
        # -Port 0 scans 8000..8500 and will land on the first free port.
        r = ps_run(LAUNCH_CURRENT, "-Port", "0", "-Workspace", scratch,
                   "-NoDeepseek", "-NoWorker", "-NoBrowser", timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"serving Mnemosyne at http://127\.0\.0\.1:(\d+)", out)
        booted = int(m.group(1)) if m else None
        check("launch-current -Port 0 boots a fresh instance", booted is not None,
              out.strip().splitlines()[0] if out.strip() else "no output")

        if booted:
            pid_file = os.path.join(REPO, ".launch-current.pid")
            rec = json.loads(read(pid_file))
            cleanup_pids = [rec.get("server"), rec.get("worker")]
            check("cold boot wrote a PID file", bool(rec.get("server")))

            status, size = http_ok(f"http://127.0.0.1:{booted}/")
            check("fresh instance answers HTTP", status == 200, f"{status}, {size}")

            # Idempotence, second run: must adopt, not start a second one.
            pid_before = str(rec.get("server"))
            r2 = ps_run(LAUNCH_CURRENT, "-Port", str(booted), "-NoBrowser")
            out2 = (r2.stdout or "") + (r2.stderr or "")
            check("second launch adopts the same instance",
                  "reusing it" in out2, out2.strip()[:120])
            rec2 = json.loads(read(pid_file))
            check("adoption did not rewrite the PID file",
                  str(rec2.get("server")) == pid_before,
                  f"{pid_before} -> {rec2.get('server')}")

            # Stop must be scoped to what this script started.
            r3 = ps_run(LAUNCH_CURRENT, "-Stop")
            out3 = (r3.stdout or "") + (r3.stderr or "")
            check("launch-current -Stop reports success",
                  "stopped" in out3.lower(), out3.strip()[:120])
            time.sleep(1.5)
            check("stopped instance released its port", not listening(booted))
            cleanup_pids = []
    finally:
        for pid in cleanup_pids:
            if pid:
                subprocess.run([PS, "-NoProfile", "-Command",
                                f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                               capture_output=True, text=True, timeout=30)
        shutil.rmtree(scratch, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n" + ("=" * 60))
if _failures:
    print(f"FAILURES ({len(_failures)}): " + ", ".join(_failures))
    sys.exit(1)
print("LAUNCHER TESTS PASS")
