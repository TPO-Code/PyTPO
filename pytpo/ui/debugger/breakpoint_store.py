from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from pytpo.ui.widgets.code_editor import CodeEditor
from pytpo.ui.debugger_support import debugger_breakpoints_supported_for_editor, debugger_breakpoints_supported_for_path


class DebuggerBreakpointStore(QObject):
    breakpointsChanged = Signal()

    def __init__(self, ide, parent=None):
        super().__init__(parent)
        self.ide = ide
        self._bound_handlers: dict[int, object] = {}

    def bind_editor(self, editor: CodeEditor | None) -> None:
        if not isinstance(editor, CodeEditor):
            return

        file_path = self._editor_path(editor)
        if file_path and debugger_breakpoints_supported_for_editor(editor):
            editor.set_debugger_breakpoint_specs(self.breakpoint_specs_for_path(file_path))
        else:
            editor.set_debugger_breakpoint_specs([])

        key = id(editor)
        if key in self._bound_handlers:
            return

        def _handle_change(*_args, target=editor) -> None:
            target_path = self._editor_path(target)
            if not target_path or not debugger_breakpoints_supported_for_editor(target):
                return
            self.set_breakpoint_specs_for_path(target_path, target.debugger_breakpoint_specs())

        self._bound_handlers[key] = _handle_change
        editor.debuggerBreakpointsChanged.connect(_handle_change)
        editor.destroyed.connect(lambda _obj=None, editor_key=key: self._bound_handlers.pop(editor_key, None))

    def all_breakpoint_specs(self) -> dict[str, list[dict]]:
        return self._breakpoints_map()

    def breakpoint_specs_for_path(self, file_path: str) -> list[dict]:
        if not debugger_breakpoints_supported_for_path(file_path):
            return []
        data = self._breakpoints_map()
        entry = data.get(self._canonical_path(file_path), [])
        return [dict(spec) for spec in entry if isinstance(spec, dict)]

    def breakpoints_for_path(self, file_path: str) -> set[int]:
        out: set[int] = set()
        for value in self.breakpoint_specs_for_path(file_path):
            try:
                line = int(value.get("line") or 0)
            except Exception:
                continue
            if line > 0:
                out.add(line)
        return out

    def set_breakpoints_for_path(self, file_path: str, lines: set[int]) -> None:
        self.set_breakpoint_specs_for_path(file_path, [{"line": int(line)} for line in lines if int(line) > 0])

    def set_breakpoint_specs_for_path(self, file_path: str, specs: list[dict]) -> None:
        path = self._canonical_path(file_path)
        if not path or not debugger_breakpoints_supported_for_path(path):
            return

        normalized = self._normalize_breakpoint_specs(specs)
        data = self._breakpoints_map()
        if normalized:
            data[path] = normalized
        else:
            data.pop(path, None)
        self._save_breakpoints_map(data)

    def watch_expressions(self) -> list[str]:
        debugger_cfg = self.ide.settings_manager.get("debugger", scope_preference="project", default={})
        if not isinstance(debugger_cfg, dict):
            return []
        raw = debugger_cfg.get("watches")
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for value in raw:
            expr = str(value or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            out.append(expr)
        return out

    def set_watch_expressions(self, expressions: list[str]) -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in expressions:
            expr = str(value or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            ordered.append(expr)
        self.ide.settings_manager.set("debugger.watches", ordered, "project")
        try:
            self.ide.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            pass

    def _breakpoints_map(self) -> dict[str, list[dict]]:
        debugger_cfg = self.ide.settings_manager.get("debugger", scope_preference="project", default={})
        if not isinstance(debugger_cfg, dict):
            return {}

        raw = debugger_cfg.get("breakpoints")
        if not isinstance(raw, dict):
            return {}

        out: dict[str, list[dict]] = {}
        for path_obj, values in raw.items():
            path = self._canonical_path(str(path_obj or ""))
            if not path or not isinstance(values, list) or not debugger_breakpoints_supported_for_path(path):
                continue
            normalized = self._normalize_breakpoint_specs(values)
            if normalized:
                out[path] = normalized
        return out

    @staticmethod
    def _normalize_breakpoint_specs(values: list[dict] | list[int]) -> list[dict]:
        normalized: list[dict] = []
        seen: set[int] = set()
        for value in values:
            if isinstance(value, dict):
                raw_line = value.get("line")
                raw_condition = value.get("condition")
                raw_hit_count = value.get("hit_count")
                raw_log_message = value.get("log_message")
            else:
                raw_line = value
                raw_condition = ""
                raw_hit_count = 0
                raw_log_message = ""
            try:
                line = int(raw_line)
                hit_count = max(0, int(raw_hit_count or 0))
            except Exception:
                continue
            if line <= 0 or line in seen:
                continue
            seen.add(line)
            normalized.append(
                {
                    "line": line,
                    "condition": str(raw_condition or "").strip(),
                    "hit_count": hit_count,
                    "log_message": str(raw_log_message or "").strip(),
                }
            )
        return normalized

    def _save_breakpoints_map(self, data: dict[str, list[dict]]) -> None:
        self.ide.settings_manager.set("debugger.breakpoints", data, "project")
        self.breakpointsChanged.emit()
        try:
            self.ide.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            pass

    def _editor_path(self, editor: CodeEditor | None) -> str:
        return self._canonical_path(str(getattr(editor, "file_path", "") or ""))

    def _canonical_path(self, path: str) -> str:
        text = str(path or "").strip()
        if not text:
            return ""
        return self.ide._canonical_path(text)
