"""Create the Mnemosyne desktop shortcut.

A disposable launcher surface, like launch.ps1 / launch-current.ps1: it holds no
configuration of its own and adds no Mnemosyne semantic. It only writes a .lnk
that points at Mnemosyne.vbs (which in turn hands off to launch-current.ps1).

    py scripts\\shortcut.py              # create/refresh the Desktop shortcut
    py scripts\\shortcut.py --remove     # delete it
    py scripts\\shortcut.py --where      # print its path

Idempotent: running it twice yields one icon, overwritten in place. Uses only
WScript.Shell, which is present on every Windows install.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_VBS = ROOT / "Mnemosyne.vbs"
ICON = ROOT / "apps" / "static" / "favicon.ico"
NAME = "Mnemosyne.lnk"


def _powershell(script: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"powershell failed:\n{out.stdout}\n{out.stderr}")
    return out.stdout.strip()


def _desktop() -> Path:
    # Ask the shell where the Desktop really is (handles OneDrive redirection).
    try:
        p = _powershell("(New-Object -ComObject WScript.Shell).SpecialFolders('Desktop')")
        if p:
            return Path(p)
    except SystemExit:
        pass
    return Path.home() / "Desktop"


def create(desktop: Path) -> Path:
    if not TARGET_VBS.exists():
        raise SystemExit(f"{TARGET_VBS} not found - the shortcut would point at nothing.")
    lnk = desktop / NAME
    icon = str(ICON) if ICON.exists() else ""

    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{lnk}'); "
        "$s.TargetPath = 'wscript.exe'; "
        "$s.Arguments = '\"" + str(TARGET_VBS) + "\"'; "
        f"$s.WorkingDirectory = '{ROOT}'; "
        "$s.Description = 'Mnemosyne Operator Console'; "
        "$s.WindowStyle = 7; "
        f""
        "$s.Save()"
    )
    _powershell(ps)
    return lnk


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create the Mnemosyne desktop shortcut.")
    ap.add_argument("--remove", action="store_true", help="delete the shortcut")
    ap.add_argument("--where", action="store_true", help="print the shortcut path and exit")
    args = ap.parse_args(argv)

    lnk = _desktop() / NAME

    if args.where:
        print(lnk)
        return 0
    if args.remove:
        if lnk.exists():
            lnk.unlink()
            print(f"removed {lnk}")
        else:
            print(f"nothing to remove ({lnk} does not exist)")
        return 0

    written = create(_desktop())
    print(f"shortcut : {written}")
    print(f'target   : wscript.exe "{TARGET_VBS}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
