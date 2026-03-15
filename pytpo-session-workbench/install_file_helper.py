#!/usr/bin/env python3
"""
Minimal privileged helper to atomically install a file to a system path.

Usage:
    install_file_helper.py --src /tmp/somefile --dst /usr/share/xsessions/my.desktop

This script must be invoked with elevated privileges (pkexec or similar).
It performs minimal checks:
 - source exists and is a regular file
 - destination parent directory exists
 - copies file to a temporary file next to destination, then os.replace() to move atomically
 - sets mode to 0644 by default (preserves mode from source if possible)
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    args = p.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)

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