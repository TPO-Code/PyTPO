from __future__ import annotations


class DebugTerminalBridge:
    def __init__(self, host) -> None:
        self.host = host

    def available(self) -> bool:
        available = getattr(self.host, "debug_io_terminal_available", None)
        if callable(available):
            return bool(available())
        return callable(getattr(self.host, "start_debug_io_terminal", None))

    def launch(
        self,
        *,
        label: str,
        cwd: str,
        argv: list[str],
        env: dict[str, str | None] | None = None,
        start_stopped: bool = False,
    ) -> int:
        starter = getattr(self.host, "start_debug_io_terminal", None)
        if not callable(starter):
            return 0
        try:
            return int(
                starter(
                    label=label,
                    cwd=cwd,
                    argv=list(argv),
                    env=dict(env or {}),
                    start_stopped=bool(start_stopped),
                )
                or 0
            )
        except TypeError:
            return int(starter(label=label, cwd=cwd, argv=list(argv), env=dict(env or {})) or 0)

    def send_input(self, text: str) -> bool:
        sender = getattr(self.host, "send_debug_io_input", None)
        if not callable(sender):
            return False
        return bool(sender(str(text)))
