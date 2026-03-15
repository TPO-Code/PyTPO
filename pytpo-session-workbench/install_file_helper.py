#!/usr/bin/env python3
"""
Minimal privileged helper to atomically install a file to a system path.

Usage:
    install_file_helper.py --src /tmp/somefile --dst /usr/share/xsessions/my.desktop
    install_file_helper.py --delete --dst /usr/share/xsessions/my.desktop --backup /tmp/live_backup.desktop

This script must be invoked with elevated privileges (pkexec or similar).
It performs minimal checks:
 - source exists and is a regular file
 - destination parent directory exists
 - copies file to a temporary file next to destination, then os.replace() to move atomically
 - sets mode to 0644 by default (preserves mode from source if possible)
 - can create a backup and delete the destination file when `--delete` is used
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dst", required=True)
    p.add_argument("--src")
    p.add_argument("--backup", default="")
    p.add_argument("--delete", action="store_true")
    args = p.parse_args()

    dst = Path(args.dst)
    backup = Path(args.backup).expanduser() if str(args.backup or "").strip() else None

    if args.delete:
        if not dst.exists() or not dst.is_file():
            print("destination missing", file=sys.stderr)
            sys.exit(4)
        try:
            if backup is not None:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(dst), str(backup))
                try:
                    os.chmod(str(backup), 0o644)
                except Exception:
                    pass
            os.remove(str(dst))
            print("ok")
            sys.exit(0)
        except Exception as e:
            print(f"delete failed: {e}", file=sys.stderr)
            sys.exit(1)

    src = Path(args.src or "")

    if not src.exists() or not src.is_file():
        print("source missing", file=sys.stderr)
        sys.exit(2)
    if not dst.parent.exists():
        print("destination parent missing", file=sys.stderr)
        sys.exit(3)

    # Copy to temp file in destination directory to preserve same filesystem
    tmp = dst.parent / (".tmp_install_" + dst.name)
    try:
        shutil.copy2(str(src), str(tmp))
        # set safe permissions (preserve if possible)
        try:
            os.chmod(str(tmp), 0o644)
        except Exception:
            pass
        # atomic replace
        os.replace(str(tmp), str(dst))
        print("ok")
        sys.exit(0)
    except Exception as e:
        print(f"install failed: {e}", file=sys.stderr)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
