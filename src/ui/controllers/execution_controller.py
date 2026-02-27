"""Controller for run/stop orchestration and terminal tab policy."""

from __future__ import annotations

import os
import re
import shlex

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu, QToolButton

from src.lang_rust.cargo_discovery import discover_workspace_root_for_file
from src.ui.widgets.terminal_widget import TerminalWidget

_CPP_RUNNABLE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx"}
_CPP_BUILDABLE_SUFFIXES = _CPP_RUNNABLE_SUFFIXES | {".h", ".hpp", ".hh", ".hxx"}
_RUST_RUNNABLE_SUFFIXES = {".rs"}
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
        if not self.console_run_manager:
            return
        sessions = self._running_script_sessions()
        if not sessions:
            self.ide.statusBar().showMessage("No running scripts.", 1200)
            return
        for session in sessions:
            self.console_run_manager.stop_file(session.file_key)
        self.ide.statusBar().showMessage(f"Stop signal sent for {len(sessions)} running script(s).", 1600)
        self._update_toolbar_run_controls()

    def _rebuild_toolbar_stop_menu(self) -> None:
        menu = self.ide._toolbar_stop_menu
        if menu is None:
            return
        menu.clear()

        sessions = self._running_script_sessions()
        if not sessions:
            empty = menu.addAction("No Active Runs")
            empty.setEnabled(False)
            return

        if len(sessions) > 1:
            stop_all = menu.addAction(f"Stop All ({len(sessions)})")
            stop_all.triggered.connect(self._stop_all_running_sessions)
            menu.addSeparator()

        for session in sessions:
            rel = self._rel_to_project(session.file_key)
            label = rel if rel and rel != "." else (session.label or session.file_key)
            action = menu.addAction(f"Stop {label}")
            action.triggered.connect(lambda _checked=False, key=session.file_key: self._stop_running_session(key))

    def _toolbar_stop_clicked(self) -> None:
        sessions = self._running_script_sessions()
        if not sessions:
            self.ide.statusBar().showMessage("No running scripts.", 1200)
            self._update_toolbar_run_controls()
            return
        self.stop_current_run()
        self._update_toolbar_run_controls()

    def _update_toolbar_run_controls(self) -> None:
        running_count = len(self._running_script_sessions())
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
                stop_btn.setToolTip("Stop Current Run (click again to force)")
            else:
                stop_btn.setToolTip("No running scripts")

            if not bool(stop_btn.property("icon_loaded")):
                stop_btn.setText("Stop" if running_count <= 1 else f"Stop ({running_count})")
        self._refresh_runtime_action_states(running_count=running_count)

    def run_current_file(self):
        ed = self.current_editor()
        if not ed:
            self.ide.statusBar().showMessage("No active editor to run.", 1500)
            return

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

        self.console_run_manager.run_file(file_path)
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        self.ide.statusBar().showMessage(f"Running {os.path.basename(file_path)}", 1500)

    def has_python_run_configs(self) -> bool:
        return bool(self.python_run_config_names())

    def has_rust_run_configs(self) -> bool:
        return bool(self.rust_run_config_names())

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
            out.append(
                {
                    "name": name,
                    "script_path": str(cfg.get("script_path") or "").strip(),
                    "args": str(cfg.get("args") or "").strip(),
                    "working_dir": str(cfg.get("working_dir") or "").strip(),
                    "interpreter": str(cfg.get("interpreter") or "").strip(),
                    "env": [f"{k}={v}" for k, v in self._normalize_env_assignments(cfg.get("env"))],
                }
            )
        return out

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

        if not self._save_all_dirty_editors_for_run():
            return False

        if not self.console_run_manager:
            return False

        run_in_spec = str(chosen.get("working_dir") or "").strip()
        if run_in_spec:
            run_in = (
                self._canonical_path(run_in_spec)
                if os.path.isabs(run_in_spec)
                else self._canonical_path(os.path.join(self.project_root, run_in_spec))
            )
        else:
            run_in = self.resolve_run_in(script_path)
        if not os.path.isdir(run_in):
            run_in = os.path.dirname(script_path) or self.project_root

        interpreter = str(chosen.get("interpreter") or "").strip() or self.resolve_interpreter(script_path)
        args_text = str(chosen.get("args") or "").strip()
        env_assignments = self._normalize_env_assignments(chosen.get("env"))

        command_block = self._build_python_run_command(
            run_in=run_in,
            interpreter=interpreter,
            script_path=script_path,
            args_text=args_text,
            env_assignments=env_assignments,
        )
        file_key = f"{script_path}::pycfg::{name}"
        self.console_run_manager.run_custom_command(
            file_key=file_key,
            label=f"Run: {name}",
            run_in=run_in,
            command_block=command_block,
        )
        self.dock_terminal.show()
        if self._run_config().get("focus_output_on_run", True):
            self.dock_terminal.raise_()
        if set_active:
            self.set_active_python_run_config(name)
        self.ide.statusBar().showMessage(f"Running config '{name}'", 2200)
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

    def _resolve_rust_run_directory(self, *, working_dir_spec: str, context_file: str) -> str:
        spec = str(working_dir_spec or "").strip()
        if spec:
            base = self._canonical_path(spec) if os.path.isabs(spec) else self._canonical_path(os.path.join(self.project_root, spec))
            if not os.path.isdir(base):
                return ""
            return self._resolve_rust_workspace_root(base) or ""

        context_target = str(context_file or "").strip() or self.project_root
        return self._resolve_rust_workspace_root(context_target)

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

        command_block = self._build_shell_run_block(
            run_in=run_in,
            command="cargo run",
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
            f"{status_prefix} cargo run ({os.path.basename(run_in) or run_in})",
            2200,
        )
        return True

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

    def _is_cpp_buildable_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in _CPP_BUILDABLE_SUFFIXES

    def can_build_current_file(self) -> bool:
        ed = self.current_editor()
        if not ed:
            return False
        file_path = getattr(ed, "file_path", "") or ""
        return self._is_cpp_buildable_file(file_path)

    def can_build_and_run_current_file(self) -> bool:
        ed = self.current_editor()
        if not ed:
            return False
        file_path = getattr(ed, "file_path", "") or ""
        return self._is_cpp_runnable_file(file_path)

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
