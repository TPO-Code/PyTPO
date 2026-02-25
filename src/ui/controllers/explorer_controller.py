"""Controller for project explorer menus and filesystem operations."""

from __future__ import annotations

import os
import shutil

from PySide6.QtCore import QMimeData, QPoint, QUrl
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox

from src.ui.dialogs.interpreter_picker_dialog import InterpreterPickerDialog


class ExplorerController:
    def __init__(self, ide, tree):
        self.ide = ide
        self.tree = tree

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _show_tree_error(self, title: str, message: str):
        QMessageBox.warning(self.ide, title, message)
        self.ide.statusBar().showMessage(message.replace("\n", " "), 3000)

    def _show_project_tree_context_menu(self, path_obj: object, global_pos: QPoint):
        path = self._canonical_path(path_obj) if isinstance(path_obj, str) else None
        targets = self._context_target_paths(path)
        menu = QMenu(self.ide)

        if len(targets) > 1:
            self._populate_multi_context_menu(menu, targets)
        elif len(targets) == 1:
            single = targets[0]
            if os.path.isfile(single):
                self._populate_file_context_menu(menu, single)
            elif os.path.isdir(single):
                self._populate_folder_context_menu(menu, single)
            else:
                self._populate_root_context_menu(menu)
        elif path and path == self.project_root:
            self._populate_root_context_menu(menu)
        elif path and os.path.isfile(path):
            self._populate_file_context_menu(menu, path)
        elif path and os.path.isdir(path):
            self._populate_folder_context_menu(menu, path)
        else:
            self._populate_root_context_menu(menu)

        menu.exec(global_pos)

    def _copy_tree_selection(self):
        self._copy_tree_paths(None)

    def _paste_tree_into_selection(self):
        selected = self.tree.selected_path()
        if isinstance(selected, str) and selected:
            target_dir = selected if os.path.isdir(selected) else os.path.dirname(selected)
        else:
            target_dir = self.project_root
        self._paste_tree_paths_into(target_dir)

    def _selected_tree_paths(self) -> list[str]:
        if self.tree is None:
            return []
        raw = self.tree.selected_paths()
        if not raw:
            return []

        canonical: list[str] = []
        seen: set[str] = set()
        for path in raw:
            cpath = self._canonical_path(path)
            if cpath == self.project_root:
                continue
            if cpath in seen:
                continue
            if not os.path.exists(cpath):
                continue
            seen.add(cpath)
            canonical.append(cpath)
        return self._filter_nested_paths(canonical)

    def _context_target_paths(self, trigger_path: str | None) -> list[str]:
        selected = self._selected_tree_paths()
        if trigger_path:
            cpath = self._canonical_path(trigger_path)
            if cpath == self.project_root:
                return []
            if cpath in selected:
                return selected
            if os.path.exists(cpath):
                return [cpath]
            return []
        return selected

    def _populate_multi_context_menu(self, menu: QMenu, paths: list[str]) -> None:
        targets = self._filter_nested_paths([self._canonical_path(p) for p in paths if isinstance(p, str)])
        if not targets:
            self._populate_root_context_menu(menu)
            return

        file_paths = [p for p in targets if os.path.isfile(p)]
        folder_paths = [p for p in targets if os.path.isdir(p)]

        act_copy = menu.addAction(f"Copy ({len(targets)})")
        act_copy.triggered.connect(lambda: self._copy_tree_paths(targets[0]))

        act_delete = menu.addAction(f"Delete Selected ({len(targets)})...")
        act_delete.triggered.connect(lambda: self._delete_paths(targets))

        if file_paths:
            menu.addSeparator()
            all_files_excluded = all(self._is_file_explicitly_excluded(path) for path in file_paths)
            label = "Include Selected Files in Indexing" if all_files_excluded else "Exclude Selected Files from Indexing"
            act_toggle_files = menu.addAction(label)
            act_toggle_files.triggered.connect(lambda: self._set_files_excluded_bulk(file_paths, excluded=not all_files_excluded))

            repo_root = self._repo_root_for_path(file_paths[0])
            if not folder_paths and repo_root and all(self._path_has_prefix(path, repo_root) for path in file_paths):
                use_fallback = len(file_paths) <= 40
                if all(self._is_untracked_git_path(path, repo_root=repo_root, allow_fallback=use_fallback) for path in file_paths):
                    act_track_files = menu.addAction(f"Track Selected Files in Git ({len(file_paths)})")
                    act_track_files.triggered.connect(lambda: self.track_paths_in_git(file_paths))

        if folder_paths:
            menu.addSeparator()
            all_folders_excluded = all(bool(self.resolve_folder_policy(path).get("exclude_from_indexing")) for path in folder_paths)
            label = "Include Selected Folders in Indexing" if all_folders_excluded else "Exclude Selected Folders from Indexing"
            act_toggle_folders = menu.addAction(label)
            act_toggle_folders.triggered.connect(
                lambda: self._set_folders_excluded_bulk(folder_paths, excluded=not all_folders_excluded)
            )

    def _copy_tree_paths(self, path: str | None):
        selected = self._selected_tree_paths()
        chosen: list[str]
        if path:
            cpath = self._canonical_path(path)
            if cpath in selected:
                chosen = selected
            else:
                chosen = [cpath] if cpath != self.project_root and os.path.exists(cpath) else []
        else:
            chosen = selected

        chosen = self._filter_nested_paths(chosen)
        if not chosen:
            self.ide.statusBar().showMessage("Nothing to copy.", 1500)
            return

        self.ide._tree_clipboard_paths = chosen
        self._set_system_clipboard_paths(chosen)
        if len(chosen) == 1:
            self.ide.statusBar().showMessage(f"Copied {os.path.basename(chosen[0])}", 1500)
        else:
            self.ide.statusBar().showMessage(f"Copied {len(chosen)} items", 1500)

    def _paste_tree_paths_into(self, dest_dir: str):
        destination = self._canonical_path(dest_dir)
        sources = self._resolve_tree_paste_paths()
        if not sources:
            self.ide.statusBar().showMessage("Clipboard is empty.", 1500)
            return
        if not os.path.isdir(destination):
            self._show_tree_error("Paste", f"Destination is not a folder:\n{destination}")
            return

        copied: list[str] = []
        failures: list[str] = []
        for source in sources:
            src = self._canonical_path(source)
            if not os.path.exists(src):
                failures.append(f"Source no longer exists: {src}")
                continue

            if os.path.isdir(src) and self._path_has_prefix(destination, src):
                failures.append(f"Cannot copy folder into itself:\n{src}")
                continue

            target = self._next_copy_target(destination, os.path.basename(src))
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
            except Exception as exc:
                failures.append(f"Could not copy '{src}': {exc}")
                continue
            copied.append(target)

        if copied:
            self.refresh_subtree(destination)
            self.tree.select_path(copied[0])
            if len(copied) == 1:
                self.ide.statusBar().showMessage(f"Pasted {os.path.basename(copied[0])}", 1800)
            else:
                self.ide.statusBar().showMessage(f"Pasted {len(copied)} items", 1800)

        if failures:
            summary = "\n".join(failures[:8])
            self._show_tree_error("Paste", summary)

    def _resolve_tree_paste_paths(self) -> list[str]:
        external = self._system_clipboard_paths()
        if external:
            return self._filter_nested_paths(external)
        return self._filter_nested_paths(self.ide._tree_clipboard_paths)

    def _set_system_clipboard_paths(self, paths: list[str]) -> None:
        app = QApplication.instance()
        if app is None:
            return
        clipboard = app.clipboard()
        if clipboard is None:
            return
        urls = [QUrl.fromLocalFile(self._canonical_path(path)) for path in paths if os.path.exists(path)]
        if urls:
            mime = QMimeData()
            mime.setUrls(urls)
            mime.setText("\n".join(str(url.toString()) for url in urls))
            clipboard.setMimeData(mime)
            return
        if paths:
            clipboard.setText("\n".join(paths))

    def _system_clipboard_paths(self) -> list[str]:
        app = QApplication.instance()
        if app is None:
            return []
        clipboard = app.clipboard()
        if clipboard is None:
            return []
        mime = clipboard.mimeData()
        if mime is None:
            return []

        candidates: list[str] = []
        if mime.hasUrls():
            for url in mime.urls():
                if not isinstance(url, QUrl):
                    continue
                if not url.isLocalFile():
                    continue
                local = str(url.toLocalFile() or "").strip()
                if local:
                    candidates.append(local)

        text = str(mime.text() or "").strip()
        if text:
            for raw_line in text.splitlines():
                line = raw_line.strip().strip('"').strip("'")
                if not line or line.lower() in {"copy", "cut"}:
                    continue
                if line.lower().startswith("file://"):
                    url = QUrl(line)
                    if url.isLocalFile():
                        local = str(url.toLocalFile() or "").strip()
                        if local:
                            candidates.append(local)
                    continue
                if os.path.isabs(line):
                    candidates.append(line)

        canonical: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            cpath = self._canonical_path(item)
            if cpath in seen:
                continue
            if not os.path.exists(cpath):
                continue
            seen.add(cpath)
            canonical.append(cpath)
        return canonical

    def _filter_nested_paths(self, paths: list[str]) -> list[str]:
        ordered = sorted({self._canonical_path(p) for p in paths if isinstance(p, str)}, key=lambda item: (len(item), item.lower()))
        result: list[str] = []
        for path in ordered:
            if any(self._path_has_prefix(path, kept) for kept in result):
                continue
            result.append(path)
        return result

    def _next_copy_target(self, dest_dir: str, source_name: str) -> str:
        base_name = str(source_name or "").strip() or "copy"
        candidate = self._canonical_path(os.path.join(dest_dir, base_name))
        if not os.path.exists(candidate):
            return candidate

        stem, ext = os.path.splitext(base_name)
        if not stem:
            stem = base_name
            ext = ""

        counter = 1
        while True:
            suffix = " copy" if counter == 1 else f" copy {counter}"
            name = f"{stem}{suffix}{ext}"
            candidate = self._canonical_path(os.path.join(dest_dir, name))
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def _populate_folder_context_menu(self, menu: QMenu, folder_path: str):
        act_new_file = menu.addAction("New File...")
        act_new_file.triggered.connect(lambda: self._create_new_file(folder_path))

        act_new_folder = menu.addAction("New Folder...")
        act_new_folder.triggered.connect(lambda: self._create_new_folder(folder_path))

        menu.addSeparator()

        act_copy = menu.addAction("Copy")
        act_copy.triggered.connect(lambda: self._copy_tree_paths(folder_path))

        act_paste = menu.addAction("Paste")
        act_paste.setEnabled(bool(self._resolve_tree_paste_paths()))
        act_paste.triggered.connect(lambda: self._paste_tree_paths_into(folder_path))

        menu.addSeparator()

        act_rename = menu.addAction("Rename...")
        act_rename.triggered.connect(lambda: self._rename_path(folder_path))

        act_delete = menu.addAction("Delete...")
        act_delete.triggered.connect(lambda: self._delete_path(folder_path))

        menu.addSeparator()

        act_set_interp = menu.addAction("Set Interpreter for Folder...")
        act_set_interp.triggered.connect(lambda: self._set_interpreter_for_folder_dialog(folder_path))

        act_clear_interp = menu.addAction("Clear Folder Interpreter Override")
        act_clear_interp.triggered.connect(lambda: self._clear_folder_interpreter_action(folder_path))

        policy = self.resolve_folder_policy(folder_path)
        excluded = bool(policy.get("exclude_from_indexing"))
        act_toggle_excluded = menu.addAction(
            "Include Folder in Indexing" if excluded else "Exclude Folder from Indexing"
        )
        act_toggle_excluded.triggered.connect(lambda: self._toggle_folder_excluded(folder_path))

        menu.addSeparator()

        act_refresh = menu.addAction("Refresh")
        act_refresh.triggered.connect(lambda: self.refresh_subtree(folder_path))

    def _populate_file_context_menu(self, menu: QMenu, file_path: str):
        act_open = menu.addAction("Open")
        act_open.triggered.connect(lambda: self.open_file(file_path))

        menu.addSeparator()

        act_copy = menu.addAction("Copy")
        act_copy.triggered.connect(lambda: self._copy_tree_paths(file_path))

        act_paste = menu.addAction("Paste Into Parent Folder")
        act_paste.setEnabled(bool(self._resolve_tree_paste_paths()))
        act_paste.triggered.connect(lambda: self._paste_tree_paths_into(os.path.dirname(file_path)))

        repo_root = self._repo_root_for_path(file_path)

        menu.addSeparator()

        act_rename = menu.addAction("Rename...")
        act_rename.triggered.connect(lambda: self._rename_path(file_path))

        act_delete = menu.addAction("Delete...")
        act_delete.triggered.connect(lambda: self._delete_path(file_path))

        excluded = self._is_file_explicitly_excluded(file_path)
        act_toggle_excluded = menu.addAction(
            "Include File in Indexing" if excluded else "Exclude File from Indexing"
        )
        act_toggle_excluded.triggered.connect(lambda: self._toggle_file_excluded(file_path))

        git_state = self.ide._git_file_states.get(self._canonical_path(file_path), "")
        is_untracked = self._is_untracked_git_path(file_path, repo_root=repo_root)
        if repo_root and is_untracked:
            menu.addSeparator()
            act_track_file = menu.addAction("Track File in Git")
            act_track_file.triggered.connect(lambda: self.track_paths_in_git([file_path]))

        if repo_root and git_state in {"dirty", "untracked"}:
            menu.addSeparator()
            act_rollback_file = menu.addAction("Rollback File Changes...")
            act_rollback_file.triggered.connect(lambda: self.rollback_file_changes(file_path))

        menu.addSeparator()

        act_refresh = menu.addAction("Refresh Parent")
        act_refresh.triggered.connect(lambda: self.refresh_subtree(os.path.dirname(file_path)))

    def _populate_root_context_menu(self, menu: QMenu):
        act_new_file = menu.addAction("New File...")
        act_new_file.triggered.connect(lambda: self._create_new_file(self.project_root))

        act_new_folder = menu.addAction("New Folder...")
        act_new_folder.triggered.connect(lambda: self._create_new_folder(self.project_root))

        menu.addSeparator()

        act_paste = menu.addAction("Paste")
        act_paste.setEnabled(bool(self._resolve_tree_paste_paths()))
        act_paste.triggered.connect(lambda: self._paste_tree_paths_into(self.project_root))

        menu.addSeparator()

        act_refresh = menu.addAction("Refresh Project")
        act_refresh.triggered.connect(self.refresh_project_tree)

        act_refresh_inc = menu.addAction("Refresh Project (Include Excluded Dirs Once)")
        act_refresh_inc.triggered.connect(lambda: self.refresh_project_tree(include_excluded=True))

    def refresh_project_tree(self, include_excluded: bool = False):
        try:
            self.tree.refresh_project(include_excluded=include_excluded)
        except Exception as exc:
            self._show_tree_error("Refresh Error", f"Could not refresh project tree:\n{exc}")
            return
        self.schedule_git_status_refresh(delay_ms=180)

    def refresh_subtree(self, path: str):
        cpath = self._canonical_path(path)
        try:
            self.tree.refresh_subtree(cpath)
        except Exception as exc:
            self._show_tree_error("Refresh Error", f"Could not refresh subtree:\n{exc}")

    def _on_tree_path_moved(self, old_path: str, new_path: str):
        self._update_open_editors_for_move(old_path, new_path)
        self.tree.select_path(new_path)
        self.ide.statusBar().showMessage(f"Moved: {old_path} -> {new_path}", 2500)
        self.schedule_git_status_refresh(delay_ms=120)

    def _create_new_file(self, folder_path: str):
        base = self._canonical_path(folder_path)
        if not os.path.isdir(base):
            self._show_tree_error("Create File", "Target directory does not exist.")
            return

        name = self._prompt_simple_name("New File", "File name:")
        if name is None:
            return

        target = self._canonical_path(os.path.join(base, name))
        if os.path.exists(target):
            self._show_tree_error("Create File", f"Path already exists:\n{target}")
            return

        try:
            with open(target, "x", encoding="utf-8"):
                pass
        except Exception as exc:
            self._show_tree_error("Create File", f"Could not create file:\n{exc}")
            return

        self.refresh_subtree(base)
        self.tree.select_path(target)
        self.ide.statusBar().showMessage(f"Created file: {name}", 2000)
        self.schedule_git_status_refresh(delay_ms=120)

    def _create_new_folder(self, folder_path: str):
        base = self._canonical_path(folder_path)
        if not os.path.isdir(base):
            self._show_tree_error("Create Folder", "Target directory does not exist.")
            return

        name = self._prompt_simple_name("New Folder", "Folder name:")
        if name is None:
            return

        target = self._canonical_path(os.path.join(base, name))
        if os.path.exists(target):
            self._show_tree_error("Create Folder", f"Path already exists:\n{target}")
            return

        try:
            os.makedirs(target, exist_ok=False)
        except Exception as exc:
            self._show_tree_error("Create Folder", f"Could not create folder:\n{exc}")
            return

        self.refresh_subtree(base)
        self.tree.select_path(target)
        self.ide.statusBar().showMessage(f"Created folder: {name}", 2000)
        self.schedule_git_status_refresh(delay_ms=120)

    def _rename_path(self, path: str):
        cpath = self._canonical_path(path)
        if cpath == self.project_root:
            self._show_tree_error("Rename", "Cannot rename the project root from explorer.")
            return

        if not os.path.exists(cpath):
            self._show_tree_error("Rename", "Path no longer exists.")
            self.refresh_project_tree()
            return

        old_name = os.path.basename(cpath)
        new_name = self._prompt_simple_name("Rename", "New name:", old_name)
        if new_name is None or new_name == old_name:
            return

        new_path = self._canonical_path(os.path.join(os.path.dirname(cpath), new_name))
        if os.path.exists(new_path):
            self._show_tree_error("Rename", f"Target already exists:\n{new_path}")
            return

        try:
            os.rename(cpath, new_path)
        except Exception as exc:
            self._show_tree_error("Rename", f"Could not rename path:\n{exc}")
            return

        self._update_open_editors_for_move(cpath, new_path)
        self.refresh_subtree(os.path.dirname(new_path))
        self.tree.select_path(new_path)
        self.ide.statusBar().showMessage(f"Renamed '{old_name}' to '{new_name}'", 2200)
        self.schedule_git_status_refresh(delay_ms=120)

    def _delete_path(self, path: str):
        self._delete_paths([path])

    def _delete_paths(self, paths: list[str]) -> None:
        targets = self._filter_nested_paths([self._canonical_path(p) for p in paths if isinstance(p, str)])
        targets = [p for p in targets if p != self.project_root]
        if not targets:
            self._show_tree_error("Delete", "Nothing to delete.")
            return

        existing = [p for p in targets if os.path.exists(p)]
        if not existing:
            self._show_tree_error("Delete", "Selected paths no longer exist.")
            self.refresh_project_tree()
            return

        if len(existing) == 1:
            only = existing[0]
            kind = "folder" if os.path.isdir(only) else "file"
            prompt = f"Delete this {kind}?\n\n{only}"
        else:
            preview = "\n".join(existing[:6])
            if len(existing) > 6:
                preview += f"\n... and {len(existing) - 6} more"
            prompt = f"Delete {len(existing)} selected items?\n\n{preview}"
        answer = QMessageBox.question(self.ide, "Confirm Delete", prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        failures: list[str] = []
        for cpath in existing:
            try:
                if os.path.isdir(cpath):
                    shutil.rmtree(cpath)
                else:
                    os.unlink(cpath)
            except Exception as exc:
                failures.append(f"{cpath}: {exc}")
                continue
            self._detach_deleted_editors(cpath)
            self.lint_manager.clear_paths_under(cpath)

        self.refresh_project_tree()
        if failures:
            summary = "\n".join(failures[:8])
            self._show_tree_error("Delete", f"Some items could not be deleted:\n{summary}")
        deleted_count = len(existing) - len(failures)
        if deleted_count > 0:
            self.ide.statusBar().showMessage(f"Deleted {deleted_count} item(s).", 2200)
            self.schedule_git_status_refresh(delay_ms=120)

    def _set_interpreter_for_folder_dialog(self, folder_path: str):
        current = self.resolve_folder_policy(folder_path).get("python") or ""
        value, ok = InterpreterPickerDialog.pick_interpreter(
            manager=self.settings_manager,
            project_root=self.project_root,
            title="Set Folder Interpreter",
            initial_value=str(current),
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if not ok:
            return

        python_path = str(value).strip()
        if not python_path:
            self._show_tree_error("Interpreter", "Interpreter path cannot be empty.")
            return

        self.set_folder_interpreter(folder_path, python_path)
        self.ide.statusBar().showMessage(f"Interpreter override set for {self._rel_to_project(folder_path)}", 2600)

    def _clear_folder_interpreter_action(self, folder_path: str):
        self.clear_folder_interpreter(folder_path)
        self.ide.statusBar().showMessage(f"Interpreter override cleared for {self._rel_to_project(folder_path)}", 2600)

    def _toggle_folder_excluded(self, folder_path: str):
        policy = self.resolve_folder_policy(folder_path)
        excluded = bool(policy.get("exclude_from_indexing"))
        self.set_folder_excluded(folder_path, not excluded)
        if not excluded:
            self.lint_manager.clear_paths_under(folder_path)
        text = "excluded" if not excluded else "included"
        self.ide.statusBar().showMessage(f"Folder {text} in indexing policy: {self._rel_to_project(folder_path)}", 2600)

    def _toggle_file_excluded(self, file_path: str):
        excluded = self._is_file_explicitly_excluded(file_path)
        self.set_file_excluded(file_path, not excluded)
        if not excluded:
            self.lint_manager.clear_file(file_path)
        text = "excluded" if not excluded else "included"
        self.ide.statusBar().showMessage(f"File {text} in indexing policy: {self._rel_to_project(file_path)}", 2600)

    def _set_folders_excluded_bulk(self, folder_paths: list[str], *, excluded: bool) -> None:
        targets = self._filter_nested_paths([self._canonical_path(p) for p in folder_paths if os.path.isdir(self._canonical_path(p))])
        if not targets:
            return
        for folder in targets:
            self.set_folder_excluded(folder, excluded)
            if excluded:
                self.lint_manager.clear_paths_under(folder)
        text = "excluded from" if excluded else "included in"
        self.ide.statusBar().showMessage(f"{len(targets)} folder(s) {text} indexing policy.", 2600)

    def _set_files_excluded_bulk(self, file_paths: list[str], *, excluded: bool) -> None:
        targets = [self._canonical_path(p) for p in file_paths if os.path.isfile(self._canonical_path(p))]
        if not targets:
            return
        for file_path in targets:
            self.set_file_excluded(file_path, excluded)
            if excluded:
                self.lint_manager.clear_file(file_path)
        text = "excluded from" if excluded else "included in"
        self.ide.statusBar().showMessage(f"{len(targets)} file(s) {text} indexing policy.", 2600)

    def _is_untracked_git_path(
        self,
        file_path: str,
        *,
        repo_root: str | None = None,
        allow_fallback: bool = True,
    ) -> bool:
        cpath = self._canonical_path(file_path)
        state = self.ide._git_file_states.get(cpath, "")
        if state == "untracked":
            return True
        if not allow_fallback:
            return False
        root = repo_root or self._repo_root_for_path(cpath)
        if not root or not self._path_has_prefix(cpath, root):
            return False
        rel_paths = self._to_repo_rel_paths(root, [cpath])
        if not rel_paths:
            return False
        try:
            return not self.git_service.is_tracked_path(root, rel_paths[0])
        except Exception:
            return False

    def _prompt_simple_name(self, title: str, label: str, initial: str = "") -> str | None:
        while True:
            value, ok = QInputDialog.getText(self.ide, title, label, text=initial)
            if not ok:
                return None
            name = str(value).strip()
            if not name:
                QMessageBox.warning(self.ide, title, "Name cannot be empty.")
                continue
            if name in (".", ".."):
                QMessageBox.warning(self.ide, title, "Invalid name.")
                continue
            if "/" in name or "\\" in name:
                QMessageBox.warning(self.ide, title, "Use a simple name without path separators.")
                continue
            return name
