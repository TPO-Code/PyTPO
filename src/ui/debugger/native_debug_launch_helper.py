from __future__ import annotations

import argparse
import ctypes
import os
import sys

PR_SET_PTRACER = 0x59616D61


def _allow_ptracer(process_id: int) -> None:
    if os.name != "posix" or sys.platform != "linux" or int(process_id or 0) <= 0:
        return
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.prctl(PR_SET_PTRACER, int(process_id), 0, 0, 0)
    if result == 0:
        return
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ptracer", type=int, default=0)
    parser.add_argument("program")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args()

    argv = [str(ns.program), *[str(arg) for arg in ns.args if str(arg) != "--"]]
    if not argv[0]:
        raise SystemExit("Missing program.")

    _allow_ptracer(int(ns.ptracer or 0))
    os.execvpe(argv[0], argv, os.environ.copy())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
