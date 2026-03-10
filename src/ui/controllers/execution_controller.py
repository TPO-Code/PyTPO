"""Controller for run/stop orchestration and terminal tab policy."""

from __future__ import annotations

import importlib.machinery
import os
import re
import shlex

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolButton

from src.lang_rust.cargo_discovery import discover_workspace_root_for_file, find_nearest_cargo_project_dir
from src.ui.debugger.lldb_dap_backend import LldbDapDebuggerBackend
from src.ui.widgets.terminal_widget import TerminalWidget

_CPP_RUNNABLE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx"}
_CPP_BUILDABLE_SUFFIXES = _CPP_RUNNABLE_SUFFIXES | {".h", ".hpp", ".hh", ".hxx"}
_RUST_RUNNABLE_SUFFIXES = {".rs"}
_PYTHON_RUNNABLE_SUFFIXES = {".py"}
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

class ExecutionController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _running_script_sessions(self):
        if self.console_run_manager is None:
            return []
        sessions = [session for session in self.console_run_manager.running_sessions() if isinstance(session.file_key, str)]
        sessions.sort(key=lambda item: ((item.label or item.file_key).lower(), item.file_key.lower()))
        return sessions

    def _debugger_widget(self):
        return getattr(self.ide, "debugger_dock_widget", None)

    def _has_active_debugger_session(self) -> bool:
        debugger = self._debugger_widget()
        session = debugger.active_session() if debugger is not None else None
        return bool(session is not None and session.is_active())

    def _active_debugger_item(self) -> dict | None:
        debugger = self._debugger_widget()
        if debugger is None:
            return None
        session = debugger.active_session()
        if session is None or not session.is_active():
            return None
        return {
            "kind": "debugger",
            "key": str(session.session_key() or "__debugger__"),
            "label": str(session.session_label() or "debug session"),
        }

    def _running_debugger_sessions(self) -> list[dict]:
        debugger = self._debugger_widget()
        if debugger is None:
            return []
        return list(debugger.running_sessions())

    def _show_debugger_dock(self) -> None:
        dock = getattr(self.ide, "dock_debugger", None)
        if dock is None:
            return
        dock.show()
        if self._run_config().get("focus_output_on_run", True):
            dock.raise_()

    def _stop_running_session(self, file_key: str) -> None:
        if not self.console_run_manager:
            return
        stage = int(self.console_run_manager.stop_file(file_key) or 0)
        self.ide.statusBar().showMessage(self._stop_status_message(stage, target=file_key), 2200)
        self._update_toolbar_run_controls()

    @staticmethod
    def _stop_status_message(stage: int, *, target: str = "") -> str:
        label = os.path.basename(str(target or "").strip()) or "run"
        if stage <= 0:
            return f"No active process to stop for {label}."
        if stage == 1:
            return f"Interrupt sent to {label}. Click Stop again to terminate."
        if stage == 2:
            return f"Terminate signal sent to {label}. Click Stop again to force kill."
        return f"Force kill sent to {label}."

    def _stop_all_running_sessions(self) -> None:
        total = 0
        debugger = self._debugger_widget()
        for debugger_item in self._running_debugger_sessions():
            if debugger is not None:
                debugger.request_stop_for_key(str(debugger_item.get("key") or ""))
                total += 1
        sessions = self._running_script_sessions()
        if not sessions and total == 0:
            self.ide.statusBar().showMessage("No running scripts.", 1200)
            return
        for session in sessions:
            self.console_run_manager.stop_file(session.file_key)
            total += 1
        self.ide.statusBar().showMessage(f"Stop signal sent for {total} active run(s).", 1600)
        self._update_toolbar_run_controls()

    def _rebuild_toolbar_stop_menu(self) -> None:
        menu = self.ide._toolbar_stop_menu
        if menu is None:
            return
        menu.clear()

        debugger_items = self._running_debugger_sessions()
        debugger_item = self._active_debugger_item()
        sessions = self._running_script_sessions()
        total_count = len(sessions) + len(debugger_items)
        if total_count <= 0:
            empty = menu.addAction("No Active Runs")
            empty.setEnabled(False)
            return

        if total_count > 1:
            stop_all = menu.addAction(f"Stop All ({total_count})")
            stop_all.triggered.connect(self._stop_all_running_sessions)
            menu.addSeparator()

        for item in debugger_items:
            label = str(item.get("label") or "Python debug session")
            key = str(item.get("key") or "")
            action = menu.addAction(f"Stop Debugger: {label}")
            if debugger_item is not None and key == str(debugger_item.get("key") or ""):
                action.triggered.connect(lambda _checked=False: self.stop_current_run())
            else:
                action.triggered.connect(lambda _checked=False, session_key=key: self._stop_debugger_session(session_key))

        for session in sessions:
            rel = self._rel_to_project(session.file_key)
            label = rel if rel and rel != "." else (session.label or session.file_key)
            action = menu.addAction(f"Stop {label}")
            action.triggered.connect(lambda _checked=False, key=session.file_key: self._stop_running_session(key))

    def _toolbar_stop_clicked(self) -> None:
        active_count = len(self._running_script_sessions()) + len(self._running_debugger_sessions())
        if not active_count:
            self.ide.statusBar().showMessage("No active runs.", 1200)
            self._update_toolbar_run_controls()
            return
        self.stop_current_run()
        self._update_toolbar_run_controls()

    def _update_toolbar_run_controls(self) -> None:
        running_count = len(self._running_script_sessions()) + len(self._running_debugger_sessions())
        stop_btn = self.ide._toolbar_stop_btn
        if stop_btn is not None:
            stop_btn.setEnabled(running_count > 0)
            stop_menu = self.ide._toolbar_stop_menu
            if stop_menu is not None:
                if running_count > 1:
                    stop_btn.setMenu(stop_menu)
                    stop_btn.setPopupMode(QToolButton.MenuButtonPopup)
                else:
                    stop_btn.setMenu(None)
            if running_count > 1:
                stop_btn.setToolTip(f"Stop Running Scripts ({running_count})")
            elif running_count == 1:
                if self._has_active_debugger_session():
                    stop_btn.setToolTip("Stop Debug Session (click again to escalate)")
                else:
                    stop_btn.setToolTip("Stop Current Run (click again to force)")
            else:
                stop_btn.setToolTip("No active runs")

            if not bool(stop_btn.property("icon_loaded")):
                stop_btn.setText("Stop" if running_count <= 1 else f"Stop ({running_count})")
        self._refresh_runtime_action_states(running_count=running_count)

    def run_current_file(self):
        ed = self.current_editor()
        if not ed:
            self.ide.statusBar().showMessage("No active editor to run.", 1500)
            return

        current_path = getattr(ed, "file_path", "") or ""
        if not self._save_all_dirty_editors_for_run():
            return

        file_path = self._save_editor_for_run(ed)
        if not file_path:
            return

        if not self.console_run_manager:
            return

        if self._is_cpp_runnable_file(file_path):
            if self._run_cpp_cmake_pipeline(file_path, status_prefix="Running", run_executable=True):
                return
            return

        if self._is_rust_runnable_file(file_path):
            self._run_default_rust_context(file_path=file_path, status_prefix="Running")
            return

        if self._is_python_runnable_file(file_path):
            self._run_python_script_in_terminal(
                script_path=file_path,
                interpreter=str(self.resolve_interpreter(file_path) or "").strip(),
                working_directory=str(self.resolve_run_in(file_path) or os.path.dirname(file_path) or self.project_root),
                arguments=(),
                environment={},
                session_label=os.path.basename(file_path) or file_path,
                session_key=file_path,
            )
            return

        self.console_run_manager.run_file(file_path)
        self._show_terminal_dock()
        self.ide.statusBar().showMessage(f"Running {os.path.basename(file_path)}", 1500)

    def has_python_run_configs(self) -> bool:
        return bool(self.python_run_config_names())

    def has_rust_run_configs(self) -> bool:
        return bool(self.rust_run_config_names())

    def has_cmake_build_configs(self) -> bool:
        return bool(self.cmake_build_config_names())

    def current_editor_file_path(self) -> str:
        ed = self.current_editor()
        if not ed:
            return ""
        file_path = getattr(ed, "file_path", "") or ""
        return self._canonical_path(file_path) if str(file_path or "").strip() else ""

    def can_run_current_file(self) -> bool:
        file_path = self.current_editor_file_path()
        if not file_path:
            return False
        if self._is_python_runnable_file(file_path):
            return True
        if self._is_rust_runnable_file(file_path):
            return bool(self._resolve_rust_workspace_root(file_path))
        if self._is_cpp_runnable_file(file_path):
            return bool(self._find_cmake_root_for_file(file_path))
        return False

    def can_run_python_current_file(self) -> bool:
        file_path = self.current_editor_file_path()
        return bool(file_path and self._is_python_runnable_file(file_path))

    def can_debug_current_file(self) -> bool:
        file_path = self.current_editor_file_path()
        if file_path and self._is_python_runnable_file(file_path):
            return True
        if file_path and self._is_rust_runnable_file(file_path):
            return bool(self.rust_debugger_available() and self._resolve_rust_debug_directory(file_path))
        return False

    @staticmethod
    def rust_debugger_available() -> bool:
        return bool(LldbDapDebuggerBackend.is_available())

    def can_run_rust_current_context(self) -> bool:
        current_file = self.current_editor_file_path()
        if current_file and self._is_rust_runnable_file(current_file):
            return bool(self._resolve_rust_workspace_root(current_file))
        return bool(self._resolve_rust_workspace_root(self.project_root))

    def can_offer_python_run_setup(self) -> bool:
        if self.has_python_run_configs():
            return True
        return self.can_run_python_current_file()

    def can_offer_python_debug_setup(self) -> bool:
        if self.has_python_run_configs():
            return True
        return self.can_debug_current_file()

    def can_offer_rust_debug_setup(self) -> bool:
        if not self.rust_debugger_available():
            return False
        if self.has_rust_run_configs():
            return True
        file_path = self.current_editor_file_path()
        return bool(
            file_path
            and self._is_rust_runnable_file(file_path)
            and self._resolve_rust_debug_directory(file_path)
        )

    def can_offer_rust_run_setup(self) -> bool:
        if self.has_rust_run_configs():
            return True
        return self.can_run_rust_current_context()

    def can_offer_cmake_build_setup(self) -> bool:
        if self.has_cmake_build_configs():
            return True
        file_path = self.current_editor_file_path()
        if file_path and self._is_cpp_buildable_file(file_path):
            return bool(self._find_cmake_root_for_file(file_path))
        root_cmake = os.path.join(self.project_root, "CMakeLists.txt")
        return os.path.isfile(root_cmake)

    def _normalized_python_run_configs(self) -> list[dict]:
        project_python = self.settings_manager.get("build.python", scope_preference="project", default={})
        if not isinstance(project_python, dict):
            project_python = {}
        raw_configs = project_python.get("run_configs")
        if not isinstance(raw_configs, list):
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for idx, item in enumerate(raw_configs):
            cfg = item if isinstance(item, dict) else {}
            name_base = str(cfg.get("name") or "").strip() or f"Run Config {idx + 1}"
            name = name_base
            suffix = 2
            while name.lower() in seen:
                name = f"{name_base} ({suffix})"
                suffix += 1
            seen.add(name.lower())
            launch_kind = str(cfg.get("launch_kind") or cfg.get("target_kind") or "script").strip().lower()
            if launch_kind not in {"script", "module"}:
                launch_kind = "script"
            out.append(
                {
                    "name": name,
                    "launch_kind": launch_kind,
                    "script_path": str(cfg.get("script_path") or "").strip(),
                    "module_name": str(cfg.get("module_name") or cfg.get("module") or "").strip(),
                    "args": str(cfg.get("args") or "").strip(),
                    "working_dir": str(cfg.get("working_dir") or "").strip(),
                    "interpreter": str(cfg.get("interpreter") or "").strip(),
                    "just_my_code": self._coerce_bool(cfg.get("just_my_code"), default=self._debugger_just_my_code_default()),
                    "env": [f"{k}={v}" for k, v in self._normalize_env_assignments(cfg.get("env"))],
                }
            )
        return out

    def _debugger_just_my_code_default(self) -> bool:
        raw = self.settings_manager.get("debugger.just_my_code", scope_preference="project", default=True)
        return self._coerce_bool(raw, default=True)

    @staticmethod
    def _coerce_bool(value: object, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def python_run_config_names(self) -> list[str]:
        names: list[str] = []
        for cfg in self._normalized_python_run_configs():
            name = str(cfg.get("name") or "").strip()
            if name:
                names.append(name)
        return names

    def active_python_run_config_name(self, *, fallback_to_first: bool = False) -> str:
        project_python = self.settings_manager.get("build.python", scope_preference="project", default={})
        active = str(project_python.get("active_config") or "").strip() if isinstance(project_python, dict) else ""
        names = self.python_run_config_names()
        if active and active.lower() in {name.lower() for name in names}:
            return active
        if fallback_to_first and names:
            return names[0]
        return ""

    def active_python_debug_config_name(self, *, fallback_to_first: bool = False) -> str:
        raw = self.settings_manager.get("debugger.active_python_config", scope_preference="project", default="")
        active = str(raw or "").strip()
        names = self.python_run_config_names()
        if active and active.lower() in {name.lower() for name in names}:
            return active
        if fallback_to_first and names:
            return names[0]
        return ""

    def set_active_python_run_config(self, config_name: str) -> bool:
        name = str(config_name or "").strip()
        if name:
            names = {item.lower() for item in self.python_run_config_names()}
            if name.lower() not in names:
                return False
        self.settings_manager.set("build.python.active_config", name, "project")
        try:
            self.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            return False
        self.ide._refresh_runtime_settings_from_manager()
        if name:
            self.ide.statusBar().showMessage(f"Active Python run config: {name}", 2200)
        else:
            self.ide.statusBar().showMessage("Python run target: current file", 2200)
        return True

    def set_active_python_debug_config(self, config_name: str) -> bool:
        name = str(config_name or "").strip()
        if name:
            names = {item.lower() for item in self.python_run_config_names()}
            if name.lower() not in names:
                return False
        self.settings_manager.set("debugger.active_python_config", name, "project")
        try:
            self.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            return False
        self.ide._refresh_runtime_settings_from_manager()
        if name:
            self.ide.statusBar().showMessage(f"Active Python debug config: {name}", 2200)
        else:
            self.ide.statusBar().showMessage("Python debug target: current file", 2200)
        return True

    @staticmethod
    def _resolve_activate_script_for_interpreter(interpreter: str) -> str:
        path_text = str(interpreter or "").strip()
        if not path_text:
            return ""
        if not os.path.isabs(path_text):
            return ""
        name = os.path.basename(path_text)
        parent = os.path.basename(os.path.dirname(path_text))
        if not name.startswith("python") or parent != "bin":
            return ""
        activate = os.path.join(os.path.dirname(path_text), "activate")
        return activate if os.path.isfile(activate) else ""

    def _build_python_run_command(
        self,
        *,
        run_in: str,
        interpreter: str,
        script_path: str,
        args_text: str,
        env_assignments: list[tuple[str, str]],
    ) -> str:
        q = shlex.quote
        lines = [f"cd {q(run_in)}", "clear"]
        for key, value in env_assignments:
            lines.append(f"export {key}={q(str(value or ''))}")
        activate_script = self._resolve_activate_script_for_interpreter(interpreter)
        args_suffix = self._quoted_args(args_text)
        if activate_script:
            lines.append(f"source {q(activate_script)}")
            cmd = f"python {q(script_path)}"
        else:
            runner = str(interpreter or "python").strip() or "python"
            cmd = f"{q(runner)} {q(script_path)}"
        if args_suffix:
            cmd += f" {args_suffix}"
        lines.extend(
            [
                cmd,
                "status=$?",
                "printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
            ]
        )
        return "\n".join(lines)

    def _build_python_module_run_command(
        self,
        *,
        run_in: str,
        interpreter: str,
        module_name: str,
        args_text: str,
        env_assignments: list[tuple[str, str]],
    ) -> str:
        q = shlex.quote
        lines = [f"cd {q(run_in)}", "clear"]
        for key, value in env_assignments:
            lines.append(f"export {key}={q(str(value or ''))}")
        activate_script = self._resolve_activate_script_for_interpreter(interpreter)
        args_suffix = self._quoted_args(args_text)
        if activate_script:
            lines.append(f"source {q(activate_script)}")
            cmd = f"python -m {q(module_name)}"
        else:
            runner = str(interpreter or "python").strip() or "python"
            cmd = f"{q(runner)} -m {q(module_name)}"
        if args_suffix:
            cmd += f" {args_suffix}"
        lines.extend(
            [
                cmd,
                "status=$?",
                "printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
            ]
        )
        return "\n".join(lines)

    def run_named_python_config(self, config_name: str, *, set_active: bool = False) -> bool:
        name = str(config_name or "").strip()
        if not name:
            return False
        chosen = None
        for cfg in self._normalized_python_run_configs():
            if str(cfg.get("name") or "").strip().lower() == name.lower():
                chosen = cfg
                break
        if not isinstance(chosen, dict):
            self.ide.statusBar().showMessage(f"Run config not found: {name}", 2200)
            return False

        run_in_spec = str(chosen.get("working_dir") or "").strip()
        if run_in_spec:
            run_in = (
                self._canonical_path(run_in_spec)
                if os.path.isabs(run_in_spec)
                else self._canonical_path(os.path.join(self.project_root, run_in_spec))
            )
        else:
            run_in = self.project_root
        if not os.path.isdir(run_in):
            run_in = self.project_root

        launch_kind = str(chosen.get("launch_kind") or "script").strip().lower()
        env_assignments = self._normalize_env_assignments(chosen.get("env"))
        arguments = self._split_args(str(chosen.get("args") or ""))
        if arguments is None:
            self.ide.statusBar().showMessage(f"Run config '{name}' has invalid shell-style arguments.", 2800)
            return False

        if not self._save_all_dirty_editors_for_run():
            return False

        ok = False
        if launch_kind == "module":
            module_name = str(chosen.get("module_name") or "").strip()
            if not module_name:
                self.ide.statusBar().showMessage(f"Run config '{name}' has no module name.", 2600)
                return False
            interpreter = str(chosen.get("interpreter") or "").strip() or self.resolve_interpreter(run_in or self.project_root)
            ok = self._run_python_module_in_terminal(
                module_name=module_name,
                interpreter=interpreter,
                working_directory=run_in,
                arguments=arguments,
                environment=dict(env_assignments),
                session_label=name,
                session_key=f"module::{module_name}::pyrun::{name}" if name else f"module::{module_name}",
            )
        else:
            script_spec = str(chosen.get("script_path") or "").strip()
            if not script_spec:
                self.ide.statusBar().showMessage(f"Run config '{name}' has no script path.", 2600)
                return False
            if os.path.isabs(script_spec):
                script_path = self._canonical_path(script_spec)
            else:
                script_path = self._canonical_path(os.path.join(self.project_root, script_spec))
            if not os.path.isfile(script_path):
                self.ide.statusBar().showMessage(f"Run config '{name}' script not found: {script_path}", 3200)
                return False
            if not run_in_spec:
                run_in = self.resolve_run_in(script_path)
                if not os.path.isdir(run_in):
                    run_in = os.path.dirname(script_path) or self.project_root
            interpreter = str(chosen.get("interpreter") or "").strip() or self.resolve_interpreter(script_path)
            ok = self._run_python_script_in_terminal(
                script_path=script_path,
                interpreter=interpreter,
                working_directory=run_in,
                arguments=arguments,
                environment=dict(env_assignments),
                session_label=name,
                session_key=f"{script_path}::pyrun::script::{name}" if name else script_path,
            )
        if not ok:
            return False
        if set_active:
            self.set_active_python_run_config(name)
        self.ide.statusBar().showMessage(f"Running config '{name}'", 2200)
        return True

    def debug_named_python_config(self, config_name: str, *, set_active: bool = False) -> bool:
        name = str(config_name or "").strip()
        if not name:
            return False
        chosen = None
        for cfg in self._normalized_python_run_configs():
            if str(cfg.get("name") or "").strip().lower() == name.lower():
                chosen = cfg
                break
        if not isinstance(chosen, dict):
            self.ide.statusBar().showMessage(f"Run config not found: {name}", 2200)
            return False

        run_in_spec = str(chosen.get("working_dir") or "").strip()
        if run_in_spec:
            run_in = (
                self._canonical_path(run_in_spec)
                if os.path.isabs(run_in_spec)
                else self._canonical_path(os.path.join(self.project_root, run_in_spec))
            )
        else:
            run_in = self.project_root
        if not os.path.isdir(run_in):
            run_in = self.project_root

        launch_kind = str(chosen.get("launch_kind") or "script").strip().lower()
        env_assignments = self._normalize_env_assignments(chosen.get("env"))
        arguments = self._split_args(str(chosen.get("args") or ""))
        if arguments is None:
            self.ide.statusBar().showMessage(f"Run config '{name}' has invalid shell-style arguments.", 2800)
            return False

        if not self._save_all_dirty_editors_for_run():
            return False

        ok = False
        if launch_kind == "module":
            module_name = str(chosen.get("module_name") or "").strip()
            if not module_name:
                self.ide.statusBar().showMessage(f"Run config '{name}' has no module name.", 2600)
                return False
            interpreter = str(chosen.get("interpreter") or "").strip() or self.resolve_interpreter(run_in or self.project_root)
            ok = self._start_debugger_for_module(
                module_name=module_name,
                interpreter=interpreter,
                working_directory=run_in,
                arguments=arguments,
                environment=dict(env_assignments),
                just_my_code=bool(chosen.get("just_my_code")),
                resolved_file_path=self._resolve_python_module_entry_path(module_name, run_in),
                session_label=name,
            )
        else:
            script_spec = str(chosen.get("script_path") or "").strip()
            if not script_spec:
                self.ide.statusBar().showMessage(f"Run config '{name}' has no script path.", 2600)
                return False
            if os.path.isabs(script_spec):
                script_path = self._canonical_path(script_spec)
            else:
                script_path = self._canonical_path(os.path.join(self.project_root, script_spec))
            if not os.path.isfile(script_path):
                self.ide.statusBar().showMessage(f"Run config '{name}' script not found: {script_path}", 3200)
                return False
            if not run_in_spec:
                run_in = self.resolve_run_in(script_path)
                if not os.path.isdir(run_in):
                    run_in = os.path.dirname(script_path) or self.project_root
            interpreter = str(chosen.get("interpreter") or "").strip() or self.resolve_interpreter(script_path)
            ok = self._start_debugger_for_script(
                script_path=script_path,
                interpreter=interpreter,
                working_directory=run_in,
                arguments=arguments,
                environment=dict(env_assignments),
                just_my_code=bool(chosen.get("just_my_code")),
                session_label=name,
            )
        if not ok:
            return False
        if set_active:
            self.set_active_python_debug_config(name)
        self.ide.statusBar().showMessage(f"Debugging config '{name}'", 2200)
        return True

    def _is_rust_runnable_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _RUST_RUNNABLE_SUFFIXES

    def _normalized_rust_run_configs(self) -> list[dict]:
        project_rust = self.settings_manager.get("build.rust", scope_preference="project", default={})
        if not isinstance(project_rust, dict):
            project_rust = {}
        raw_configs = project_rust.get("run_configs")
        if not isinstance(raw_configs, list):
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for idx, item in enumerate(raw_configs):
            cfg = item if isinstance(item, dict) else {}
            name_base = str(cfg.get("name") or "").strip() or f"Cargo Config {idx + 1}"
            name = name_base
            suffix = 2
            while name.lower() in seen:
                name = f"{name_base} ({suffix})"
                suffix += 1
            seen.add(name.lower())

            command_type = str(cfg.get("command_type") or "run").strip().lower()
            if command_type not in {"run", "test", "build", "custom"}:
                command_type = "run"
            profile = str(cfg.get("profile") or "debug").strip().lower()
            if profile not in {"debug", "release"}:
                profile = "debug"

            out.append(
                {
                    "name": name,
                    "command_type": command_type,
                    "package": str(cfg.get("package") or "").strip(),
                    "binary": str(cfg.get("binary") or "").strip(),
                    "profile": profile,
                    "features": str(cfg.get("features") or "").strip(),
                    "args": str(cfg.get("args") or "").strip(),
                    "test_filter": str(cfg.get("test_filter") or "").strip(),
                    "command": str(cfg.get("command") or "").strip(),
                    "working_dir": str(cfg.get("working_dir") or "").strip(),
                    "env": [f"{k}={v}" for k, v in self._normalize_env_assignments(cfg.get("env"))],
                }
            )
        return out

    def rust_run_config_names(self) -> list[str]:
        names: list[str] = []
        for cfg in self._normalized_rust_run_configs():
            name = str(cfg.get("name") or "").strip()
            if name:
                names.append(name)
        return names

    def active_rust_run_config_name(self, *, fallback_to_first: bool = False) -> str:
        project_rust = self.settings_manager.get("build.rust", scope_preference="project", default={})
        active = str(project_rust.get("active_config") or "").strip() if isinstance(project_rust, dict) else ""
        names = self.rust_run_config_names()
        if active and active.lower() in {name.lower() for name in names}:
            return active
        if fallback_to_first and names:
            return names[0]
        return ""

    def set_active_rust_run_config(self, config_name: str) -> bool:
        name = str(config_name or "").strip()
        if name:
            names = {item.lower() for item in self.rust_run_config_names()}
            if name.lower() not in names:
                return False
        self.settings_manager.set("build.rust.active_config", name, "project")
        try:
            self.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            return False
        self.ide._refresh_runtime_settings_from_manager()
        if name:
            self.ide.statusBar().showMessage(f"Active Cargo config: {name}", 2200)
        else:
            self.ide.statusBar().showMessage("Cargo run target: current context", 2200)
        return True

    def run_named_rust_config(self, config_name: str, *, set_active: bool = False) -> bool:
        name = str(config_name or "").strip()
        if not name:
            return False
        chosen = None
        for cfg in self._normalized_rust_run_configs():
            if str(cfg.get("name") or "").strip().lower() == name.lower():
                chosen = cfg
                break
        if not isinstance(chosen, dict):
            self.ide.statusBar().showMessage(f"Cargo config not found: {name}", 2200)
            return False

        if not self._save_all_dirty_editors_for_run():
            return False
        if not self.console_run_manager:
            return False

        current_file = self._current_editor_path_for_rust_context()
        run_in = self._resolve_rust_run_directory(
            working_dir_spec=str(chosen.get("working_dir") or "").strip(),
            context_file=current_file,
        )
        if not run_in:
            self.ide.statusBar().showMessage("No Cargo.toml found for the selected Rust run target.", 2800)
            return False

        command_type = str(chosen.get("command_type") or "run").strip().lower()
        if command_type not in {"run", "test", "build", "custom"}:
            command_type = "run"
        cargo_cmd = self._build_cargo_command_from_config(chosen)
        if not cargo_cmd:
            self.ide.statusBar().showMessage(f"Cargo config '{name}' has no command to run.", 2600)
            return False

        command_block = self._build_shell_run_block(
            run_in=run_in,
            command=cargo_cmd,
            env_assignments=self._normalize_env_assignments(chosen.get("env")),
            require_cargo=(command_type != "custom"),
        )
        session_key = f"{run_in}::rustcfg::{name}"
        self.console_run_manager.run_custom_command(
            file_key=session_key,
            label=f"Cargo: {name}",
            run_in=run_in,
            command_block=command_block,
        )
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        if set_active:
            self.set_active_rust_run_config(name)
        self.ide.statusBar().showMessage(f"Running Cargo config '{name}'", 2400)
        return True

    def _current_editor_path_for_rust_context(self) -> str:
        ed = self.current_editor()
        if not ed:
            return ""
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            return ""
        cpath = self._canonical_path(file_path)
        return cpath if self._is_rust_runnable_file(cpath) else ""

    def _resolve_rust_workspace_root(self, target_path: str) -> str:
        cpath = self._canonical_path(target_path)
        if not cpath:
            return ""
        root = discover_workspace_root_for_file(
            file_path=cpath,
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            path_has_prefix=self._path_has_prefix,
        )
        root = self._canonical_path(root) if root else ""
        if not root:
            return ""
        manifest = os.path.join(root, "Cargo.toml")
        return root if os.path.isfile(manifest) else ""

    def _resolve_rust_debug_directory(self, target_path: str) -> str:
        cpath = self._canonical_path(target_path)
        if not cpath:
            return ""
        root = find_nearest_cargo_project_dir(
            file_path=cpath,
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            path_has_prefix=self._path_has_prefix,
        )
        root = self._canonical_path(root) if root else ""
        if root and os.path.isfile(os.path.join(root, "Cargo.toml")):
            return root
        return self._resolve_rust_workspace_root(cpath)

    def _resolve_rust_run_directory(self, *, working_dir_spec: str, context_file: str) -> str:
        spec = str(working_dir_spec or "").strip()
        if spec:
            base = self._canonical_path(spec) if os.path.isabs(spec) else self._canonical_path(os.path.join(self.project_root, spec))
            if not os.path.isdir(base):
                return ""
            return self._resolve_rust_workspace_root(base) or ""

        context_target = str(context_file or "").strip() or self.project_root
        return self._resolve_rust_workspace_root(context_target)

    def _rust_debug_target_for_file(self, file_path: str, run_in: str) -> tuple[str, str]:
        path = self._canonical_path(file_path) if str(file_path or "").strip() else ""
        if not path or not run_in:
            return "", ""
        try:
            rel_path = os.path.relpath(path, run_in)
        except Exception:
            rel_path = path
        rel_norm = rel_path.replace("\\", "/")
        if rel_norm.startswith("src/bin/") and rel_norm.endswith(".rs"):
            return os.path.splitext(os.path.basename(rel_norm))[0], "bin"
        if rel_norm == "src/main.rs":
            return self._cargo_package_name(os.path.join(run_in, "Cargo.toml")), "bin"
        return "", ""

    @staticmethod
    def _cargo_package_name(manifest_path: str) -> str:
        path = str(manifest_path or "").strip()
        if not path or not os.path.isfile(path):
            return ""
        in_package = False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    line = str(raw or "").strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        in_package = line.lower() == "[package]"
                        continue
                    if in_package and line.lower().startswith("name"):
                        _key, _eq, value = line.partition("=")
                        return str(value or "").strip().strip('"').strip("'")
        except Exception:
            return ""
        return ""

    def _build_rust_debug_build_command(
        self,
        *,
        package: str = "",
        binary: str = "",
        profile: str = "debug",
        features: str = "",
        command_type: str = "run",
    ) -> tuple[str, ...]:
        parts = ["cargo", "build", "--message-format=json-render-diagnostics"]
        package_text = str(package or "").strip()
        binary_text = str(binary or "").strip()
        feature_text = str(features or "").strip()
        profile_text = str(profile or "debug").strip().lower()
        command_text = str(command_type or "run").strip().lower()
        if package_text:
            parts.extend(["--package", package_text])
        if binary_text:
            parts.extend(["--bin", binary_text])
        if feature_text:
            parts.extend(["--features", feature_text])
        if profile_text == "release":
            parts.append("--release")
        if command_text == "test":
            parts[1] = "test"
            parts.append("--no-run")
        return tuple(parts)

    def _build_cargo_command_from_config(self, cfg: dict) -> str:
        command_type = str(cfg.get("command_type") or "run").strip().lower()
        if command_type not in {"run", "test", "build", "custom"}:
            command_type = "run"

        if command_type == "custom":
            cmd = str(cfg.get("command") or "").strip()
            if not cmd:
                return ""
            args_text = self._quoted_args(str(cfg.get("args") or ""))
            return f"{cmd} {args_text}".strip() if args_text else cmd

        q = shlex.quote
        cmd = f"cargo {command_type}"
        package = str(cfg.get("package") or "").strip()
        binary = str(cfg.get("binary") or "").strip()
        profile = str(cfg.get("profile") or "debug").strip().lower()
        features = str(cfg.get("features") or "").strip()
        args = str(cfg.get("args") or "").strip()
        test_filter = str(cfg.get("test_filter") or "").strip()

        if package:
            cmd += f" --package {q(package)}"
        if binary:
            cmd += f" --bin {q(binary)}"
        if features:
            cmd += f" --features {q(features)}"
        if profile == "release":
            cmd += " --release"

        if command_type == "test":
            filter_args = self._quoted_args(test_filter)
            if filter_args:
                cmd += f" {filter_args}"
            test_args = self._quoted_args(args)
            if test_args:
                cmd += f" -- {test_args}"
        elif command_type == "run":
            run_args = self._quoted_args(args)
            if run_args:
                cmd += f" -- {run_args}"
        return cmd

    def _build_shell_run_block(
        self,
        *,
        run_in: str,
        command: str,
        env_assignments: list[tuple[str, str]],
        require_cargo: bool,
    ) -> str:
        q = shlex.quote
        lines = [f"cd {q(run_in)}", "clear"]
        for key, value in env_assignments:
            lines.append(f"export {key}={q(str(value or ''))}")
        if require_cargo:
            lines.extend(
                [
                    "if ! command -v cargo >/dev/null 2>&1; then",
                    "  echo 'Error: cargo not found in PATH.'",
                    "  status=127",
                    "  printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
                    "  exit",
                    "fi",
                ]
            )
        lines.extend(
            [
                str(command or "").strip(),
                "status=$?",
                "printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
            ]
        )
        return "\n".join(lines)

    def _run_default_rust_context(self, *, file_path: str, status_prefix: str) -> bool:
        if not self.console_run_manager:
            return False
        context_path = self._canonical_path(file_path) if str(file_path or "").strip() else self.project_root
        run_in = self._resolve_rust_workspace_root(context_path)
        if not run_in:
            self.ide.statusBar().showMessage("No Cargo.toml found for this Rust file.", 2800)
            return False
        command = self._default_rust_run_command()

        command_block = self._build_shell_run_block(
            run_in=run_in,
            command=command,
            env_assignments=[],
            require_cargo=True,
        )
        key = context_path if self._is_rust_runnable_file(context_path) else f"{run_in}::rustctx"
        label = f"Cargo: {os.path.basename(run_in) or 'workspace'}"
        self.console_run_manager.run_custom_command(
            file_key=key,
            label=label,
            run_in=run_in,
            command_block=command_block,
        )
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        self.ide.statusBar().showMessage(
            f"{status_prefix} {command} ({os.path.basename(run_in) or run_in})",
            2200,
        )
        return True

    @staticmethod
    def _default_rust_run_command() -> str:
        return "cargo run --release"

    def build_current_file(self):
        ed = self.current_editor()
        if not ed:
            self.ide.statusBar().showMessage("No active editor to build.", 1500)
            return

        if not self._save_all_dirty_editors_for_run():
            return

        file_path = self._save_editor_for_run(ed)
        if not file_path:
            return

        if not self.console_run_manager:
            return

        if not self._is_cpp_buildable_file(file_path):
            self.ide.statusBar().showMessage("Build is currently available for C/C++ files only.", 2200)
            return

        self._run_cpp_cmake_pipeline(file_path, status_prefix="Building", run_executable=False)

    def build_and_run_current_file(self):
        ed = self.current_editor()
        if not ed:
            self.ide.statusBar().showMessage("No active editor to build and run.", 1500)
            return

        if not self._save_all_dirty_editors_for_run():
            return

        file_path = self._save_editor_for_run(ed)
        if not file_path:
            return

        if not self.console_run_manager:
            return

        if not self._is_cpp_runnable_file(file_path):
            self.ide.statusBar().showMessage("Build + Run is currently available for C/C++ source files only.", 2200)
            return

        self._run_cpp_cmake_pipeline(file_path, status_prefix="Building + running", run_executable=True)

    def rerun_current_file(self):
        active_python = self.active_python_run_config_name()
        if active_python:
            self.run_named_python_config(active_python, set_active=False)
            return

        ed = self.current_editor()
        if ed and self._is_python_runnable_file(getattr(ed, "file_path", "") or ""):
            self.run_current_file()
            return

        if not self.console_run_manager:
            return

        if not self._save_all_dirty_editors_for_run():
            return

        ed = self.current_editor()
        target = None

        if ed and ed.file_path:
            target = self._save_editor_for_run(ed)
        else:
            target = self.console_run_manager.active_file_key()

        if not target:
            self.ide.statusBar().showMessage("No file available to rerun.", 1500)
            return

        if self._is_cpp_runnable_file(target):
            if self._run_cpp_cmake_pipeline(target, status_prefix="Rerunning", run_executable=True):
                return
            return

        self.console_run_manager.rerun_file(target)
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        self.ide.statusBar().showMessage(f"Rerunning {os.path.basename(target)}", 1500)

    def stop_current_run(self):
        debugger_item = self._active_debugger_item()
        if debugger_item is not None:
            debugger = self._debugger_widget()
            stage = int(debugger.request_stop_active() or 0) if debugger is not None else 0
            self.ide.statusBar().showMessage(
                self._debugger_stop_status_message(stage, target=str(debugger_item.get("label") or "")),
                2200,
            )
            self._update_toolbar_run_controls()
            return

        if not self.console_run_manager:
            return

        stage = 0
        target = ""
        active_session = self.console_run_manager.active_session()
        if active_session:
            target = str(active_session.file_key or "")
            stage = int(self.console_run_manager.stop_active_tab() or 0)
        else:
            ed = self.current_editor()
            if ed and ed.file_path:
                target = str(ed.file_path or "")
                stage = int(self.console_run_manager.stop_file(ed.file_path) or 0)
        self.ide.statusBar().showMessage(self._stop_status_message(stage, target=target), 2200)
        self._update_toolbar_run_controls()

    def new_terminal_tab(self):
        if self.console_tabs is None:
            return
        run_cfg = self._run_config()
        start_in = self._resolve_path_from_project(str(run_cfg.get("default_cwd") or "."))
        if not os.path.isdir(start_in):
            start_in = self.project_root

        self.ide._ad_hoc_terminal_counter += 1
        key = f"__adhoc__/{self.ide._ad_hoc_terminal_counter}"
        if self.ide._ad_hoc_terminal_counter == 1:
            label = "Terminal"
        else:
            label = f"Terminal {self.ide._ad_hoc_terminal_counter}"

        terminal = TerminalWidget(cwd=start_in, parent=self.console_tabs)
        self._style_terminal_widget(terminal)
        terminal.setProperty("file_key", key)
        terminal.tracebackLinkActivated.connect(self._on_console_traceback_activated)

        self.ide._ad_hoc_terminal_keys.add(key)
        self.console_tabs.addTab(terminal, label)
        self.console_tabs.setCurrentWidget(terminal)
        self.dock_terminal.show()
        self.dock_terminal.raise_()
        self.console_tabs.setFocus()
        terminal.setFocus()
        QTimer.singleShot(0, terminal.setFocus)
        self.ide.statusBar().showMessage(f"Opened {label}.", 1500)
        self._refresh_runtime_action_states()

    def _on_console_tab_close_requested(self, index: int):
        self._close_console_tab_at(index)

    def _close_console_tab_at(self, index: int) -> bool:
        if self.console_tabs is None or index < 0:
            return False
        widget = self.console_tabs.widget(index)
        if not isinstance(widget, TerminalWidget):
            return False
        return self._close_console_terminal(widget)

    def _close_console_terminal(self, terminal: TerminalWidget) -> bool:
        tabs = self.console_tabs
        if tabs is None:
            return False

        key = terminal.property("file_key")
        if isinstance(key, str) and key in self.ide._ad_hoc_terminal_keys:
            idx = tabs.indexOf(terminal)
            if idx >= 0:
                tabs.removeTab(idx)
            self.ide._ad_hoc_terminal_keys.discard(key)
            try:
                terminal.close()
            except Exception:
                pass
            terminal.deleteLater()
            self.ide.statusBar().showMessage("Terminal tab closed.", 1400)
            self._refresh_runtime_action_states()
            return True

        if self.console_run_manager is not None and self.console_run_manager.close_session_for_terminal(terminal):
            self.ide.statusBar().showMessage("Terminal tab closed.", 1400)
            self._refresh_runtime_action_states()
            return True

        idx = tabs.indexOf(terminal)
        if idx < 0:
            return False
        tabs.removeTab(idx)
        try:
            terminal.close()
        except Exception:
            pass
        terminal.deleteLater()
        self.ide.statusBar().showMessage("Terminal tab closed.", 1400)
        self._refresh_runtime_action_states()
        return True

    def close_active_terminal_tab(self):
        if self.console_tabs is None:
            return
        idx = self.console_tabs.currentIndex()
        if idx < 0:
            self.ide.statusBar().showMessage("No active terminal tab.", 1500)
            return
        if not self._close_console_tab_at(idx):
            self.ide.statusBar().showMessage("No active terminal tab.", 1500)
            return

    def _is_cpp_runnable_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _CPP_RUNNABLE_SUFFIXES

    def _is_python_runnable_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _PYTHON_RUNNABLE_SUFFIXES

    def _is_cpp_buildable_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _CPP_BUILDABLE_SUFFIXES

    def can_build_current_file(self) -> bool:
        file_path = self.current_editor_file_path()
        if not file_path or not self._is_cpp_buildable_file(file_path):
            return False
        return bool(self._find_cmake_root_for_file(file_path))

    def can_build_and_run_current_file(self) -> bool:
        file_path = self.current_editor_file_path()
        if not file_path or not self._is_cpp_runnable_file(file_path):
            return False
        return bool(self._find_cmake_root_for_file(file_path))

    def _find_cmake_root_for_file(self, file_path: str) -> str:
        cpath = self._canonical_path(file_path)
        cursor = self._canonical_path(os.path.dirname(cpath))
        project_root = self._canonical_path(self.project_root)

        while True:
            cmake_lists = os.path.join(cursor, "CMakeLists.txt")
            if os.path.isfile(cmake_lists):
                return cursor
            if cursor == project_root:
                break
            parent = self._canonical_path(os.path.dirname(cursor))
            if parent == cursor or not self._path_has_prefix(parent, project_root):
                break
            cursor = parent

        root_cmake = os.path.join(project_root, "CMakeLists.txt")
        if os.path.isfile(root_cmake):
            return project_root
        return ""

    def _cmake_run_cfg(self) -> dict:
        project_build = self.settings_manager.get("build.cmake", scope_preference="project", default={})
        if not isinstance(project_build, dict):
            project_build = {}
        run_cfg = self._run_config()
        legacy_cmake_cfg = run_cfg.get("cmake", {}) if isinstance(run_cfg, dict) else {}
        base = project_build if project_build else (legacy_cmake_cfg if isinstance(legacy_cmake_cfg, dict) else {})
        resolved = {
            "build_dir": str(base.get("build_dir") or "build").strip() or "build",
            "build_type": str(base.get("build_type") or "Debug").strip() or "Debug",
            "target": str(base.get("target") or "").strip(),
            "configure_args": str(base.get("configure_args") or "").strip(),
            "build_args": str(base.get("build_args") or "").strip(),
            "run_args": str(base.get("run_args") or "").strip(),
            "parallel_jobs": 0,
            "env": self._normalize_env_assignments(base.get("env")),
            "_active_config": "",
        }
        try:
            resolved["parallel_jobs"] = max(0, int(base.get("parallel_jobs", 0)))
        except Exception:
            resolved["parallel_jobs"] = 0

        presets = self._normalized_cmake_build_configs()
        active_name = str(base.get("active_config") or "").strip().lower()
        selected = None
        if active_name:
            for cfg in presets:
                if str(cfg.get("name") or "").strip().lower() == active_name:
                    selected = cfg
                    break
        if selected is None and presets:
            selected = presets[0]
        if isinstance(selected, dict):
            resolved["_active_config"] = str(selected.get("name") or "").strip()
            resolved["build_dir"] = str(selected.get("build_dir") or resolved["build_dir"]).strip() or "build"
            resolved["build_type"] = str(selected.get("build_type") or resolved["build_type"]).strip() or "Debug"
            resolved["target"] = str(selected.get("target") or "").strip()
            resolved["configure_args"] = str(selected.get("configure_args") or "").strip()
            resolved["build_args"] = str(selected.get("build_args") or "").strip()
            resolved["run_args"] = str(selected.get("run_args") or "").strip()
            resolved["env"] = self._normalize_env_assignments(selected.get("env"))
            try:
                resolved["parallel_jobs"] = max(0, int(selected.get("parallel_jobs", 0)))
            except Exception:
                resolved["parallel_jobs"] = 0
        return resolved

    @staticmethod
    def _normalize_env_assignments(raw_env: object) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        if isinstance(raw_env, dict):
            for key_obj, value_obj in raw_env.items():
                key = str(key_obj or "").strip()
                if not key or not _ENV_KEY_RE.match(key):
                    continue
                value = str(value_obj or "")
                dedupe = key.lower()
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append((key, value))
            return out
        if not isinstance(raw_env, list):
            return out
        for item in raw_env:
            text = str(item or "").strip()
            if not text or "=" not in text:
                continue
            key, _, value = text.partition("=")
            key = key.strip()
            if not key or not _ENV_KEY_RE.match(key):
                continue
            dedupe = key.lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append((key, value))
        return out

    @staticmethod
    def _split_args(raw_args: str) -> tuple[str, ...] | None:
        text = str(raw_args or "").strip()
        if not text:
            return ()
        try:
            return tuple(str(part) for part in shlex.split(text))
        except Exception:
            return None

    @staticmethod
    def _debugger_stop_status_message(stage: int, *, target: str = "") -> str:
        label = str(target or "").strip() or "debug session"
        if stage <= 0:
            return f"No active debug session to stop for {label}."
        if stage == 1:
            return f"Polite stop requested for {label}. Click Stop again to escalate."
        if stage == 2:
            return f"Forceful stop requested for {label}. Click Stop again to kill it."
        return f"Kill signal sent to {label}."

    def debug_current_file(self) -> bool:
        return bool(self._start_debugger_for_current_file())

    def _start_debugger_for_current_file(self) -> bool:
        debugger = self._debugger_widget()
        if debugger is None:
            self.ide.statusBar().showMessage("Debugger is not available.", 2200)
            return False
        current_file = self.current_editor_file_path()
        if current_file and self._is_rust_runnable_file(current_file):
            ok = bool(self._start_debugger_for_rust_current_file(current_file))
        else:
            ok = bool(debugger.start_current_file_debugging())
        if ok:
            self._show_debugger_dock()
            session = debugger.active_session()
            self.ide.statusBar().showMessage(
                f"Debugging {session.session_label() if session is not None else 'current file'}",
                1800,
            )
            self._update_toolbar_run_controls()
        return ok

    def _show_terminal_dock(self) -> None:
        if self.dock_terminal is None:
            return
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()

    def _run_python_script_in_terminal(
        self,
        *,
        script_path: str,
        interpreter: str,
        working_directory: str,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        session_label: str,
        session_key: str,
    ) -> bool:
        if not self.console_run_manager:
            return False
        run_in = str(working_directory or os.path.dirname(script_path) or self.project_root).strip()
        if not os.path.isdir(run_in):
            run_in = os.path.dirname(script_path) or self.project_root
        command_block = self._build_python_run_command(
            run_in=run_in,
            interpreter=str(interpreter or self.resolve_interpreter(script_path) or "").strip(),
            script_path=script_path,
            args_text=" ".join(shlex.quote(str(arg)) for arg in arguments),
            env_assignments=self._normalize_env_assignments(environment),
        )
        key = str(session_key or script_path).strip() or script_path
        label = str(session_label or os.path.basename(script_path) or script_path).strip()
        self.console_run_manager.run_custom_command(
            file_key=key,
            label=label,
            run_in=run_in,
            command_block=command_block,
        )
        self._show_terminal_dock()
        self._update_toolbar_run_controls()
        return True

    def _run_python_module_in_terminal(
        self,
        *,
        module_name: str,
        interpreter: str,
        working_directory: str,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        session_label: str,
        session_key: str,
    ) -> bool:
        if not self.console_run_manager:
            return False
        run_in = str(working_directory or self.project_root).strip() or self.project_root
        if not os.path.isdir(run_in):
            run_in = self.project_root
        command_block = self._build_python_module_run_command(
            run_in=run_in,
            interpreter=str(interpreter or self.resolve_interpreter(run_in) or "").strip(),
            module_name=module_name,
            args_text=" ".join(shlex.quote(str(arg)) for arg in arguments),
            env_assignments=self._normalize_env_assignments(environment),
        )
        key = str(session_key or f"module::{module_name}").strip() or f"module::{module_name}"
        label = str(session_label or module_name or "Python module").strip()
        self.console_run_manager.run_custom_command(
            file_key=key,
            label=label,
            run_in=run_in,
            command_block=command_block,
        )
        self._show_terminal_dock()
        self._update_toolbar_run_controls()
        return True

    def _start_debugger_for_script(
        self,
        *,
        script_path: str,
        interpreter: str,
        working_directory: str,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        just_my_code: bool,
        session_label: str,
    ) -> bool:
        debugger = self._debugger_widget()
        if debugger is None:
            self.ide.statusBar().showMessage("Debugger is not available.", 2200)
            return False
        ok = bool(
            debugger.start_script_debugging(
                file_path=script_path,
                interpreter=interpreter,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                just_my_code=just_my_code,
                session_label=session_label,
                session_key=f"{script_path}::pycfg::script::{session_label}" if session_label else script_path,
            )
        )
        if ok:
            self._show_debugger_dock()
            self._update_toolbar_run_controls()
        return ok

    def _start_debugger_for_module(
        self,
        *,
        module_name: str,
        interpreter: str,
        working_directory: str,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        just_my_code: bool,
        resolved_file_path: str,
        session_label: str,
    ) -> bool:
        debugger = self._debugger_widget()
        if debugger is None:
            self.ide.statusBar().showMessage("Debugger is not available.", 2200)
            return False
        ok = bool(
            debugger.start_module_debugging(
                module_name=module_name,
                interpreter=interpreter,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                just_my_code=just_my_code,
                resolved_file_path=resolved_file_path,
                session_label=session_label,
                session_key=f"module::{module_name}::pycfg::{session_label}" if session_label else f"module::{module_name}",
            )
        )
        if ok:
            self._show_debugger_dock()
            self._update_toolbar_run_controls()
        return ok

    def _start_debugger_for_executable(
        self,
        *,
        file_path: str,
        program_path: str = "",
        working_directory: str,
        arguments: tuple[str, ...],
        environment: dict[str, str],
        build_command: tuple[str, ...],
        target_name: str,
        target_kind: str,
        language: str,
        session_label: str,
        session_key: str,
    ) -> bool:
        debugger = self._debugger_widget()
        if debugger is None:
            self.ide.statusBar().showMessage("Debugger is not available.", 2200)
            return False
        ok = bool(
            debugger.start_executable_debugging(
                file_path=file_path,
                program_path=program_path,
                working_directory=working_directory,
                arguments=arguments,
                environment=environment,
                build_command=build_command,
                target_name=target_name,
                target_kind=target_kind,
                language=language,
                session_label=session_label,
                session_key=session_key,
            )
        )
        if ok:
            self._show_debugger_dock()
            self._update_toolbar_run_controls()
        return ok

    def _start_debugger_for_rust_current_file(self, file_path: str) -> bool:
        if not LldbDapDebuggerBackend.is_available():
            self.ide.statusBar().showMessage(
                "Rust debugging requires an LLDB debug adapter in PATH (lldb-dap or lldb-vscode).",
                3200,
            )
            return False
        if not self._save_all_dirty_editors_for_run():
            return False
        current_path = self._canonical_path(file_path)
        debug_in = self._resolve_rust_debug_directory(current_path)
        if not debug_in:
            self.ide.statusBar().showMessage("No Cargo.toml found for this Rust file.", 2800)
            return False
        target_name, target_kind = self._rust_debug_target_for_file(current_path, debug_in)
        build_command = self._build_rust_debug_build_command(
            binary=target_name if target_kind == "bin" else "",
            command_type="run",
        )
        return self._start_debugger_for_executable(
            file_path=current_path,
            working_directory=debug_in,
            arguments=(),
            environment={},
            build_command=build_command,
            target_name=target_name,
            target_kind=target_kind or "bin",
            language="rust",
            session_label=os.path.basename(debug_in) or os.path.basename(current_path) or current_path,
            session_key=f"{debug_in}::rustdbg::current",
        )

    def debug_named_rust_config(self, config_name: str, *, set_active: bool = False) -> bool:
        name = str(config_name or "").strip()
        if not name:
            return False
        if not LldbDapDebuggerBackend.is_available():
            self.ide.statusBar().showMessage(
                "Rust debugging requires an LLDB debug adapter in PATH (lldb-dap or lldb-vscode).",
                3200,
            )
            return False
        chosen = None
        for cfg in self._normalized_rust_run_configs():
            if str(cfg.get("name") or "").strip().lower() == name.lower():
                chosen = cfg
                break
        if not isinstance(chosen, dict):
            self.ide.statusBar().showMessage(f"Cargo config not found: {name}", 2200)
            return False
        command_type = str(chosen.get("command_type") or "run").strip().lower()
        if command_type not in {"run", "test", "build"}:
            self.ide.statusBar().showMessage(
                f"Cargo config '{name}' cannot be debugged because custom cargo commands are not supported.",
                3200,
            )
            return False
        if not self._save_all_dirty_editors_for_run():
            return False
        current_file = self._current_editor_path_for_rust_context()
        run_in = self._resolve_rust_run_directory(
            working_dir_spec=str(chosen.get("working_dir") or "").strip(),
            context_file=current_file,
        )
        if not run_in:
            self.ide.statusBar().showMessage("No Cargo.toml found for the selected Rust debug target.", 2800)
            return False
        args = self._split_args(str(chosen.get("args") or ""))
        if args is None:
            self.ide.statusBar().showMessage(f"Cargo config '{name}' has invalid shell-style arguments.", 2800)
            return False
        runtime_args = tuple(args)
        if command_type == "test":
            filter_args = self._split_args(str(chosen.get("test_filter") or ""))
            if filter_args is None:
                self.ide.statusBar().showMessage(f"Cargo config '{name}' has invalid test filter arguments.", 2800)
                return False
            runtime_args = tuple(filter_args) + tuple(args)
        target_name = str(chosen.get("binary") or "").strip() or str(chosen.get("package") or "").strip()
        target_kind = "test" if command_type == "test" else "bin"
        build_command = self._build_rust_debug_build_command(
            package=str(chosen.get("package") or "").strip(),
            binary=str(chosen.get("binary") or "").strip(),
            profile=str(chosen.get("profile") or "debug"),
            features=str(chosen.get("features") or "").strip(),
            command_type=command_type,
        )
        ok = self._start_debugger_for_executable(
            file_path=current_file or os.path.join(run_in, "src", "main.rs"),
            working_directory=run_in,
            arguments=runtime_args,
            environment=dict(self._normalize_env_assignments(chosen.get("env"))),
            build_command=build_command,
            target_name=target_name,
            target_kind=target_kind,
            language="rust",
            session_label=f"Cargo: {name}",
            session_key=f"{run_in}::rustdbg::{name}",
        )
        if not ok:
            return False
        if set_active:
            self.set_active_rust_run_config(name)
        self.ide.statusBar().showMessage(f"Debugging Cargo config '{name}'", 2400)
        return True

    def _resolve_python_module_entry_path(self, module_name: str, working_directory: str) -> str:
        module = str(module_name or "").strip()
        if not module:
            return ""
        search_roots: list[str] = []
        seen: set[str] = set()
        for candidate in (working_directory, self.project_root):
            base = str(candidate or "").strip()
            if not base:
                continue
            for root in (base, os.path.join(base, "src")):
                resolved = self._canonical_path(root)
                if resolved in seen or not os.path.isdir(resolved):
                    continue
                seen.add(resolved)
                search_roots.append(resolved)

        for root in search_roots:
            try:
                spec = importlib.machinery.PathFinder.find_spec(module, [root])
            except Exception:
                spec = None
            if spec is not None and getattr(spec, "submodule_search_locations", None):
                try:
                    main_spec = importlib.machinery.PathFinder.find_spec(f"{module}.__main__", list(spec.submodule_search_locations))
                except Exception:
                    main_spec = None
                main_origin = str(getattr(main_spec, "origin", "") or "").strip() if main_spec is not None else ""
                if main_origin and main_origin not in {"built-in", "frozen"} and os.path.isfile(main_origin):
                    return self._canonical_path(main_origin)
            origin = str(getattr(spec, "origin", "") or "").strip() if spec is not None else ""
            if origin and origin not in {"built-in", "frozen"} and os.path.isfile(origin):
                return self._canonical_path(origin)

        return ""

    def _stop_debugger_session(self, session_key: str) -> None:
        debugger = self._debugger_widget()
        if debugger is None:
            return
        session = debugger.session_for_key(session_key)
        label = session.session_label() if session is not None else session_key
        stage = int(debugger.request_stop_for_key(session_key) or 0)
        self.ide.statusBar().showMessage(
            self._debugger_stop_status_message(stage, target=label),
            2200,
        )
        self._update_toolbar_run_controls()

    def _normalized_cmake_build_configs(self) -> list[dict]:
        project_build = self.settings_manager.get("build.cmake", scope_preference="project", default={})
        cmake_cfg = project_build if isinstance(project_build, dict) else {}
        if not cmake_cfg:
            run_cfg = self._run_config()
            legacy = run_cfg.get("cmake", {}) if isinstance(run_cfg, dict) else {}
            cmake_cfg = legacy if isinstance(legacy, dict) else {}
        raw_configs = cmake_cfg.get("build_configs")
        if not isinstance(raw_configs, list):
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for idx, item in enumerate(raw_configs):
            cfg = item if isinstance(item, dict) else {}
            name_base = str(cfg.get("name") or "").strip() or f"Config {idx + 1}"
            name = name_base
            suffix = 2
            while name.lower() in seen:
                name = f"{name_base} ({suffix})"
                suffix += 1
            seen.add(name.lower())
            norm = {
                "name": name,
                "build_dir": str(cfg.get("build_dir") or "build").strip() or "build",
                "build_type": str(cfg.get("build_type") or "Debug").strip() or "Debug",
                "target": str(cfg.get("target") or "").strip(),
                "configure_args": str(cfg.get("configure_args") or "").strip(),
                "build_args": str(cfg.get("build_args") or "").strip(),
                "run_args": str(cfg.get("run_args") or "").strip(),
                "env": [f"{k}={v}" for k, v in self._normalize_env_assignments(cfg.get("env"))],
            }
            try:
                norm["parallel_jobs"] = max(0, int(cfg.get("parallel_jobs", 0)))
            except Exception:
                norm["parallel_jobs"] = 0
            out.append(norm)
        return out

    def cmake_build_config_names(self) -> list[str]:
        names: list[str] = []
        for cfg in self._normalized_cmake_build_configs():
            name = str(cfg.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
        return names

    def active_cmake_build_config_name(self) -> str:
        cfg = self._cmake_run_cfg()
        name = str(cfg.get("_active_config") or "").strip()
        if name:
            return name
        names = self.cmake_build_config_names()
        return names[0] if names else ""

    def set_active_cmake_build_config(self, config_name: str) -> bool:
        name = str(config_name or "").strip()
        if not name:
            return False
        names = {item.lower() for item in self.cmake_build_config_names()}
        if name.lower() not in names:
            return False
        self.settings_manager.set("build.cmake.active_config", name, "project")
        try:
            self.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            return False
        self.ide._refresh_runtime_settings_from_manager()
        self.ide.statusBar().showMessage(f"Active build configuration: {name}", 2200)
        return True

    @staticmethod
    def _quoted_args(raw_args: str) -> str:
        text = str(raw_args or "").strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text)
        except Exception:
            parts = text.split()
        return " ".join(shlex.quote(part) for part in parts if str(part).strip())

    def _build_cmake_run_command(
        self,
        *,
        cmake_root: str,
        build_dir: str,
        build_type: str,
        target: str,
        configure_args: str,
        build_args: str,
        run_args: str,
        parallel_jobs: int,
        env_assignments: list[tuple[str, str]],
        run_executable: bool,
    ) -> str:
        q = shlex.quote
        lines = [
            f"cd {q(cmake_root)}",
            "clear",
            "if ! command -v cmake >/dev/null 2>&1; then",
            "  echo 'Error: cmake not found in PATH.'",
            "  status=127",
            "  printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
            "  exit",
            "fi",
        ]
        for key, value in env_assignments:
            lines.append(f"export {key}={q(str(value or ''))}")

        configure_cmd = f"cmake -S {q(cmake_root)} -B {q(build_dir)}"
        if build_type:
            configure_cmd += f" -DCMAKE_BUILD_TYPE={q(build_type)}"
        configure_cmd += " -DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
        cfg_extra = self._quoted_args(configure_args)
        if cfg_extra:
            configure_cmd += f" {cfg_extra}"
        lines.extend(
            [
                configure_cmd,
                "status=$?",
                "if [ $status -ne 0 ]; then",
                "  printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
                "  exit",
                "fi",
            ]
        )

        build_cmd = f"cmake --build {q(build_dir)}"
        if build_type:
            build_cmd += f" --config {q(build_type)}"
        target_text = str(target or "").strip()
        if target_text:
            build_cmd += f" --target {q(target_text)}"
        if int(parallel_jobs) > 0:
            build_cmd += f" --parallel {int(parallel_jobs)}"
        bld_extra = self._quoted_args(build_args)
        if bld_extra:
            build_cmd += f" -- {bld_extra}"
        lines.extend(
            [
                build_cmd,
                "status=$?",
                "if [ $status -ne 0 ]; then",
                "  printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
                "  exit",
                "fi",
            ]
        )

        if not run_executable:
            lines.extend(
                [
                    "echo 'Build completed successfully.'",
                    "status=0",
                    "printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
                ]
            )
            return "\n".join(lines)

        lines.append("run_bin=''")
        if target_text:
            lines.append(f"run_bin={q(os.path.join(build_dir, target_text))}")
            lines.extend(
                [
                    "if [ ! -x \"$run_bin\" ]; then",
                    (
                        f"  run_bin=$(find {q(build_dir)} -type f -perm -111 "
                        f"-name {q(target_text)} ! -path '*/CMakeFiles/*' 2>/dev/null | head -n1)"
                    ),
                    "fi",
                ]
            )
        else:
            lines.extend(
                [
                    (
                        f"run_bin=$(find {q(build_dir)} -type f -perm -111 "
                        "! -path '*/CMakeFiles/*' "
                        "! -name '*.o' ! -name '*.a' ! -name '*.so' ! -name '*.so.*' "
                        "-printf '%T@ %p\\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
                    ),
                ]
            )

        lines.extend(
            [
                "if [ -z \"$run_bin\" ] || [ ! -x \"$run_bin\" ]; then",
                "  echo 'Error: no runnable executable found after build.'",
                "  echo 'Tip: set a CMake target in Settings -> IDE -> Run -> C/C++ (CMake).'",
                "  status=1",
                "  printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
                "  exit",
                "fi",
                "echo \"Running: $run_bin\"",
            ]
        )
        run_extra = self._quoted_args(run_args)
        if run_extra:
            lines.append(f"\"$run_bin\" {run_extra}")
        else:
            lines.append("\"$run_bin\"")
        lines.extend(
            [
                "status=$?",
                "printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"",
            ]
        )
        return "\n".join(lines)

    def _run_cpp_cmake_pipeline(self, file_path: str, *, status_prefix: str, run_executable: bool) -> bool:
        if self.console_run_manager is None:
            return False

        cmake_root = self._find_cmake_root_for_file(file_path)
        if not cmake_root:
            self.ide.statusBar().showMessage(
                "No CMakeLists.txt found for this file. Add one or open the correct CMake project root.",
                3600,
            )
            return False

        cfg = self._cmake_run_cfg()
        build_dir_cfg = str(cfg.get("build_dir") or "build").strip() or "build"
        if os.path.isabs(build_dir_cfg):
            build_dir = self._canonical_path(build_dir_cfg)
        else:
            build_dir = self._canonical_path(os.path.join(cmake_root, build_dir_cfg))

        build_type = str(cfg.get("build_type") or "Debug").strip()
        target = str(cfg.get("target") or "").strip()
        configure_args = str(cfg.get("configure_args") or "").strip()
        build_args = str(cfg.get("build_args") or "").strip()
        run_args = str(cfg.get("run_args") or "").strip()
        env_assignments = list(cfg.get("env") or [])
        try:
            parallel_jobs = max(0, int(cfg.get("parallel_jobs", 0)))
        except Exception:
            parallel_jobs = 0

        command_block = self._build_cmake_run_command(
            cmake_root=cmake_root,
            build_dir=build_dir,
            build_type=build_type,
            target=target,
            configure_args=configure_args,
            build_args=build_args,
            run_args=run_args,
            parallel_jobs=parallel_jobs,
            env_assignments=env_assignments,
            run_executable=run_executable,
        )

        self.console_run_manager.run_custom_command(
            file_key=file_path,
            label=os.path.basename(file_path) or file_path,
            run_in=cmake_root,
            command_block=command_block,
        )
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        mode_label = "CMake build + run" if run_executable else "CMake build"
        active_cfg = str(cfg.get("_active_config") or "").strip()
        if active_cfg:
            self.ide.statusBar().showMessage(
                f"{status_prefix} {mode_label} [{active_cfg}] for {os.path.basename(file_path)}",
                2000,
            )
        else:
            self.ide.statusBar().showMessage(f"{status_prefix} {mode_label} for {os.path.basename(file_path)}", 1800)
        return True
