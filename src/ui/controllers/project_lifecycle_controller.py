"""Controller for project-window lifecycle and session persistence orchestration."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.instance_coordinator import request_project_activation
from src.settings_manager import SettingsManager
from src.ui.dialogs.file_dialog_bridge import get_existing_directory, get_open_file_name, get_save_file_name
from src.ui.dialogs.new_project_dialog import NewProjectDialog
from src.ui.dialogs.project_name_dialog import ProjectNameDialog
from src.ui.editor_workspace import EditorWidget


class ProjectLifecycleController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _should_open_created_files(self) -> bool:
        return bool(self.settings_manager.get("editor.open_created_files", scope_preference="ide", default=True))

    def _ide_python_executable(self) -> str:
        # IDE relaunches must use the IDE runtime interpreter, not project interpreters.
        key = "PYTPO_IDE_PYTHON"
        pinned = str(os.environ.get(key) or "").strip()
        if pinned:
            return pinned
        runtime = (sys.executable or "").strip() or "python3"
        os.environ[key] = runtime
        return runtime

    def _instance_handoff_timeout_s(self) -> float:
        raw = str(os.environ.get("PYTPO_INSTANCE_WAIT_TIMEOUT_S") or "").strip()
        if raw:
            try:
                return max(1.0, min(30.0, float(raw)))
            except Exception:
                pass
        # PyCharm run/debug sessions can delay subprocess startup noticeably.
        if os.environ.get("PYCHARM_HOSTED") or os.environ.get("PYDEVD_LOAD_VALUES_ASYNC"):
            return 10.0
        return 5.0

    def new_file(self):
        path, _ = get_save_file_name(
            parent=self.ide,
            manager=self.settings_manager,
            caption="New File",
            directory=self.project_root,
        )
        if not path:
            return

        cpath = self._canonical_path(path)
        parent_dir = self._canonical_path(os.path.dirname(cpath) or self.project_root)
        if not os.path.isdir(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as exc:
                QMessageBox.warning(self.ide, "New File", f"Could not create folder:\n{exc}")
                return

        created = False
        if not os.path.exists(cpath):
            try:
                Path(cpath).write_text("", encoding="utf-8")
            except Exception as exc:
                QMessageBox.warning(self.ide, "New File", f"Could not create file:\n{exc}")
                return
            created = True

        if created and self._should_open_created_files():
            self.open_file(cpath)
        self.refresh_subtree(parent_dir)
        self.ide.statusBar().showMessage(f"Created file: {os.path.basename(cpath)}", 1800)

    def open_file_dialog(self):
        path, _ = get_open_file_name(
            parent=self.ide,
            manager=self.settings_manager,
            caption="Open File",
            directory=self.project_root,
        )
        if path:
            self.open_file(path)

    def open_project_dialog(self):
        default_dir = self.project_root
        if self.no_project_mode:
            default_dir = str(
                self.settings_manager.get("projects.last_create_in", scope_preference="ide", default=Path.home())
                or Path.home()
            )
        folder = get_existing_directory(
            parent=self.ide,
            manager=self.settings_manager,
            caption="Open Project Folder",
            directory=default_dir,
        )
        if folder:
            self.open_project_path(folder)

    def open_new_project_dialog(self) -> None:
        fallback = str(Path.home()) if self.no_project_mode else str(Path(self.project_root).parent)
        create_in = str(
            self.settings_manager.get("projects.last_create_in", scope_preference="ide", default=fallback) or ""
        ).strip()
        if not create_in:
            create_in = fallback
        if not os.path.isdir(create_in):
            create_in = fallback

        dialog = NewProjectDialog(
            manager=self.settings_manager,
            default_create_in=create_in,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        target = str(dialog.created_project_path or "").strip()
        if not target:
            return
        post_create_note = str(dialog.created_project_post_create_note or "").strip()
        project_name = str(dialog.created_project_name or "").strip()
        if post_create_note:
            QMessageBox.warning(self.ide, "New Project", post_create_note)
        self.ide.statusBar().showMessage(f"Created project: {target}", 2200)
        self.open_project_path(target, suggested_project_name=project_name)

    def _ask_project_open_mode(self, target_project: str) -> str | None:
        if self.no_project_mode:
            return "current"
        dialog = QMessageBox(self.ide)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Open Project")
        dialog.setText("A project is already open in this window.")
        dialog.setInformativeText(
            f"Open '{target_project}' in the current window or a new window?"
        )
        current_btn = dialog.addButton("Current Window", QMessageBox.AcceptRole)
        new_btn = dialog.addButton("New Window", QMessageBox.ActionRole)
        cancel_btn = dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(new_btn)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is current_btn:
            return "current"
        if clicked is new_btn:
            return "new"
        if clicked is cancel_btn:
            return None
        return None

    def _confirm_save_modified_editors(self) -> bool:
        seen_docs: set[str] = set()
        for widget in self.editor_workspace.all_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            if isinstance(widget, EditorWidget):
                doc_key = self._doc_key_for_editor(widget)
            else:
                doc_key = self._canonical_path(path)
            if doc_key in seen_docs:
                continue
            seen_docs.add(doc_key)

            doc_getter = getattr(widget, "document", None)
            if not callable(doc_getter):
                continue
            try:
                modified = bool(doc_getter().isModified())
            except Exception:
                continue
            if not modified:
                continue

            widget.setFocus()
            name_getter = getattr(widget, "display_name", None)
            label = str(name_getter()) if callable(name_getter) else "File"
            answer = QMessageBox.question(
                self.ide,
                "Unsaved Changes",
                f"Save changes to '{label}' before continuing?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                return False
            if answer == QMessageBox.Yes:
                if not path:
                    self.ide.statusBar().showMessage("Cannot save: editor has no backing file.", 2200)
                    return False
                saver = getattr(widget, "save_file", None)
                if not callable(saver) or not saver():
                    return False
                self._note_editor_saved(widget, source="save before action")
        return True

    def _launch_project_window(self, project_path: str) -> bool:
        main_script = Path(__file__).resolve().parents[3] / "main.py"
        python_bin = self._ide_python_executable()
        ok = QProcess.startDetached(python_bin, [str(main_script), project_path], project_path)
        if not ok:
            QMessageBox.warning(self.ide, "Open Project", f"Could not open project:\n{project_path}")
            return False
        return True

    def _launch_no_project_window(self) -> bool:
        main_script = Path(__file__).resolve().parents[3] / "main.py"
        python_bin = self._ide_python_executable()
        ok = QProcess.startDetached(
            python_bin,
            [str(main_script), self.FORCE_NO_PROJECT_ARG],
            self.project_root,
        )
        if not ok:
            QMessageBox.warning(
                self.ide,
                "Close Project",
                "Could not start a welcome-screen window.",
            )
            return False
        return True

    def _wait_for_project_instance(self, project_path: str, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.2, float(timeout_s))
        while time.monotonic() < deadline:
            if request_project_activation(project_path, timeout_ms=120):
                return True
            QApplication.processEvents()
            time.sleep(0.05)
        return False

    def _open_project_in_current_window(self, project_path: str) -> bool:
        if not self._confirm_save_modified_editors():
            return False
        if not self._launch_project_window(project_path):
            return False
        timeout_s = self._instance_handoff_timeout_s()
        if not self._wait_for_project_instance(project_path, timeout_s=timeout_s):
            QMessageBox.warning(
                self.ide,
                "Open Project",
                "The new project window did not start successfully.\n"
                "Keeping the current project open.",
            )
            return False
        self.ide._skip_close_save_prompt_once = True
        self.ide.close()
        return True

    def close_project(self) -> None:
        if self.no_project_mode:
            return
        if not self._confirm_save_modified_editors():
            return
        if not self._launch_no_project_window():
            return
        no_project_key = self.no_project_instance_key()
        timeout_s = self._instance_handoff_timeout_s()
        if not self._wait_for_project_instance(no_project_key, timeout_s=timeout_s):
            QMessageBox.warning(
                self.ide,
                "Close Project",
                "The welcome-screen window did not start successfully.\n"
                "Keeping the current project open.",
            )
            return
        self.ide._skip_close_save_prompt_once = True
        self.ide.close()

    def _project_config_exists(self, project_path: str) -> bool:
        project_json = Path(project_path) / self.PROJECT_JSON
        return project_json.is_file()

    def _default_project_name_for_path(self, project_path: str) -> str:
        folder_name = str(Path(project_path).name or "").strip()
        return folder_name or "My Python Project"

    def _prompt_for_project_name(self, project_path: str) -> str | None:
        default_name = self._default_project_name_for_path(project_path)
        dialog = ProjectNameDialog(
            project_path=project_path,
            default_name=default_name,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        selected = str(dialog.project_name or "").strip()
        if selected:
            return selected
        return default_name

    def _seed_missing_project_config(self, project_path: str, *, suggested_project_name: str = "") -> bool:
        if self._project_config_exists(project_path):
            return True

        project_name = str(suggested_project_name or "").strip()
        if not project_name:
            project_name = self._prompt_for_project_name(project_path) or ""
        if not project_name:
            return False

        try:
            manager = SettingsManager(project_root=project_path, ide_app_dir=self.ide_app_dir)
            manager.load_all()
            manager.set("project_name", project_name, "project")
            manager.save_all(scopes={"project"}, only_dirty=True, allow_project_repair=True)
        except Exception as exc:
            QMessageBox.warning(
                self.ide,
                "Open Project",
                f"Could not initialize project settings:\n{exc}",
            )
            return False
        return True

    def open_project_path(self, path: str, suggested_project_name: str = ""):
        target = self._canonical_path(path)
        if not os.path.isdir(target):
            QMessageBox.warning(self.ide, "Open Project", f"Project folder does not exist:\n{target}")
            return
        if target == self.project_root:
            self._activate_window_from_instance_request()
            self.ide.statusBar().showMessage("Project already open.", 1800)
            return

        self._remember_recent_project(target, save=True)
        self._refresh_runtime_action_states()

        if request_project_activation(target):
            self.ide.statusBar().showMessage(f"Switched to existing project window: {target}", 2200)
            return

        mode = self._ask_project_open_mode(target)
        if mode is None:
            return
        if not self._seed_missing_project_config(target, suggested_project_name=suggested_project_name):
            return
        if mode == "current":
            if self._open_project_in_current_window(target):
                self.ide.statusBar().showMessage(f"Opened project in current window: {target}", 2200)
            return

        if self._launch_project_window(target):
            self.ide.statusBar().showMessage(f"Opened project window: {target}", 2200)

    def _collect_open_editor_payload(self):
        docs = []
        seen_keys: set[str] = set()
        for widget in self.editor_workspace.all_document_widgets():
            file_path = self._document_widget_path(widget)
            if not file_path:
                continue
            if isinstance(widget, EditorWidget):
                key = self._doc_key_for_editor(widget)
            else:
                key = self._canonical_path(file_path)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            doc_getter = getattr(widget, "document", None)
            modified = False
            if callable(doc_getter):
                try:
                    modified = bool(doc_getter().isModified())
                except Exception:
                    modified = False
            docs.append(
                {
                    "key": key,
                    "file_path": self._canonical_path(file_path),
                    "modified": modified,
                }
            )
        return docs

    def _open_editor_payload_from_config(self, cfg: dict | None = None) -> list[dict]:
        source = cfg if isinstance(cfg, dict) else self.config
        docs = source.get("open_editors", []) if isinstance(source, dict) else []
        if not isinstance(docs, list):
            return []
        out: list[dict] = []
        for item in docs:
            if isinstance(item, dict):
                out.append(dict(item))
        return out

    def _close_all_editors_without_prompt(self) -> None:
        for tabs in list(self.editor_workspace.all_tabs()):
            while tabs.count() > 0:
                widget = tabs.widget(0)
                tabs.removeTab(0)
                widget.hide()
                if isinstance(widget, EditorWidget):
                    doc_key = self._doc_key_for_editor(widget)
                    self.editor_workspace.release_document_view(widget, doc_key)
                widget.deleteLater()
            owner = getattr(tabs, "owner_window", None)
            if owner is not None:
                try:
                    owner.close()
                except Exception:
                    pass
        self.editor_workspace.request_cleanup_empty_panes()

    def _sync_open_editors_from_config(self, *, source: str) -> bool:
        if not self._confirm_save_modified_editors():
            self.ide.statusBar().showMessage(
                f"Reloaded settings from {source}, but kept current open editors (save canceled).",
                2800,
            )
            return False
        self._close_all_editors_without_prompt()
        self.restore_open_files_only()
        self._attach_all_editor_lint_hooks()
        self._normalize_editor_docks()
        self._refresh_runtime_action_states()
        self.ide.statusBar().showMessage(f"Applied open editors from {source}.", 2200)
        return True

    def _reload_project_config_from_disk(self, *, source: str, honor_open_editors: bool = True) -> None:
        if self.ide._project_config_reload_active:
            return
        self.ide._project_config_reload_active = True
        try:
            before_open_editors = self._open_editor_payload_from_config()
            self.settings_manager.reload_all()
            self.ide.project_config_path = self._canonical_path(str(self.settings_manager.project_path))
            self.ide.ide_app_dir = self._canonical_path(str(self.settings_manager.paths.ide_app_dir))
            self.ide.ide_settings_path = self._canonical_path(str(self.settings_manager.ide_path))
            self._refresh_runtime_settings_from_manager()
            self._report_settings_load_errors(source=source)
            after_open_editors = self._open_editor_payload_from_config()
            synced_open_editors = False
            if honor_open_editors and after_open_editors != before_open_editors:
                synced_open_editors = self._sync_open_editors_from_config(source=source)
            if not synced_open_editors:
                self._attach_all_editor_lint_hooks()
                self._refresh_runtime_action_states()
            self.refresh_project_tree()
            self._refresh_project_symbol_index()
            self._seed_external_file_watch_state()
            if self.settings_manager.load_errors():
                self.ide.statusBar().showMessage(f"Loaded with settings errors from {source}. See Debug Output.", 3600)
            else:
                self.ide.statusBar().showMessage(f"Reloaded settings from {source}.", 1800)
        except Exception as exc:
            self.ide.statusBar().showMessage(f"Failed to reload project settings: {exc}", 4200)
        finally:
            self.ide._project_config_reload_active = False

    def restore_open_files_only(self):
        docs = self.config.get("open_editors", [])
        if not isinstance(docs, list) or not docs:
            return

        restored_keys: set[str] = set()
        for item in docs:
            if not isinstance(item, dict):
                continue
            fp = item.get("file_path")
            dedupe_key = self._canonical_path(fp) if isinstance(fp, str) and fp else ""
            if dedupe_key and dedupe_key in restored_keys:
                continue
            if dedupe_key:
                restored_keys.add(dedupe_key)

            if isinstance(fp, str) and fp and os.path.exists(fp):
                self.open_file(fp)

    def save_session_to_config(self):
        self.config["open_editors"] = self._collect_open_editor_payload()
        self.write_project_config(self.config)
