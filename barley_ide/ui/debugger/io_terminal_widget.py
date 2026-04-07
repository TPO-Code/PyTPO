from __future__ import annotations

from TPOPyside.widgets.terminal_widget import TerminalWidget


class DebuggerIoTerminalWidget(TerminalWidget):
    def __init__(
        self,
        *,
        argv: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        command = [str(part) for part in (argv or []) if str(part)]
        super().__init__(
            shell=command[0] if command else None,
            argv=command,
            login=False,
            cwd=str(cwd or "").strip() or None,
            env=dict(env or {}),
            parent=parent,
            show_toolbar=False,
        )
