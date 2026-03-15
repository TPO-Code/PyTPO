# pytpo-session-workbench/ui/main_window.py
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QSplitter, QTextEdit, QPlainTextEdit, QPushButton, QLabel, QFileDialog,
    QMessageBox, QInputDialog, QToolBar, QListWidget, QListWidgetItem
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QTimer
from pathlib import Path
from backend import fs
from backend import project as project_api
import json
import traceback
import os

DEFAULT_PROJECTS_ROOT = str(Path.home() / "session-workspaces")
ESCALATE_HELPER_PATH = "/usr/lib/pytpo-session-workbench/install_file_helper.py"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Session Workspace Editor (PySide6)")
        self.resize(1200, 760)

        self.project_root = None
        self.current_tracked_dir = None
        self.current_live_path = None

        self._dirty = False
        self._programmatic_change = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left: tree
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Workspaces / Live roots"])
        left_layout.addWidget(self.tree)

        # project buttons
        btn_row = QWidget()
        br_layout = QHBoxLayout(btn_row)
        btn_new_project = QPushButton("Create Project…")
        btn_new_project.clicked.connect(self.on_create_project)
        br_layout.addWidget(btn_new_project)
        btn_open_project = QPushButton("Open Project…")
        btn_open_project.clicked.connect(self.on_open_project)
        br_layout.addWidget(btn_open_project)
        btn_close_project = QPushButton("Close Project")
        btn_close_project.clicked.connect(self.on_close_project)
        br_layout.addWidget(btn_close_project)
        left_layout.addWidget(btn_row)

        splitter.addWidget(left)

        # Center: editor and diff
        center = QWidget()
        center_layout = QVBoxLayout(center)

        toolbar = QToolBar()
        self.action_toggle_edit = QAction("Edit", self)
        self.action_toggle_edit.setCheckable(True)
        self.action_toggle_edit.toggled.connect(self.on_toggle_edit)
        toolbar.addAction(self.action_toggle_edit)

        self.btn_save = QPushButton("Save draft")
        self.btn_save.clicked.connect(self.on_save_draft)
        toolbar.addWidget(self.btn_save)

        self.btn_diff = QPushButton("Show diff (draft ↔ live)")
        self.btn_diff.clicked.connect(self.on_show_diff)
        toolbar.addWidget(self.btn_diff)

        center_layout.addWidget(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.textChanged.connect(self.on_editor_text_changed)
        center_layout.addWidget(self.editor)

        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setVisible(False)
        center_layout.addWidget(self.diff_view)

        splitter.addWidget(center)

        # Right: inspector + backups
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Inspector / Controls"))
        self.lbl_meta = QLabel("")
        self.lbl_meta.setWordWrap(True)
        right_layout.addWidget(self.lbl_meta)

        btn_import = QPushButton("Import selected → Project")
        btn_import.clicked.connect(self.on_import_clicked)
        right_layout.addWidget(btn_import)

        # Backups UI
        right_layout.addWidget(QLabel("Backups"))
        self.list_backups_widget = QListWidget()
        right_layout.addWidget(self.list_backups_widget)

        btn_restore_draft = QPushButton("Restore → Draft")
        btn_restore_draft.clicked.connect(self.on_restore_to_draft)
        right_layout.addWidget(btn_restore_draft)

        btn_restore_live = QPushButton("Restore → Live (may require password)")
        btn_restore_live.clicked.connect(self.on_restore_to_live)
        right_layout.addWidget(btn_restore_live)

        btn_backup = QPushButton("Create backup of draft")
        btn_backup.clicked.connect(self.on_create_backup)
        right_layout.addWidget(btn_backup)

        btn_push = QPushButton("Push draft → live")
        btn_push.clicked.connect(self.on_push)
        right_layout.addWidget(btn_push)

        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)

        # populate tree and connect
        self._populate_tree()
        self.tree.itemClicked.connect(self.on_tree_item_clicked)

        # autosave timer
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30 * 1000)
        self._autosave_timer.timeout.connect(self._autosave_if_dirty)
        self._autosave_timer.start()

    # ---------- helpers ----------
    def _set_dirty(self, v: bool):
        self._dirty = v
        title = "Session Workspace Editor"
        if self._dirty:
            title += " — *unsaved*"
        self.setWindowTitle(title)

    def _autosave_if_dirty(self):
        if self._dirty and self.current_tracked_dir:
            try:
                self._save_draft_internal()
            except Exception:
                pass

    def _maybe_autosave_before_change(self):
        if self._dirty and self.current_tracked_dir:
            try:
                self._save_draft_internal()
            except Exception as e:
                QMessageBox.warning(self, "Auto-save failed", f"Auto-save failed before switching file: {e}")

    # ---------- tree ----------
    def _populate_tree(self):
        self.tree.clear()
        ws_root = QTreeWidgetItem(["Workspaces"])
        ws_root.setData(0, Qt.UserRole, {"type": "workspaces_root"})
        self.tree.addTopLevelItem(ws_root)

        if self.project_root:
            proj_item = QTreeWidgetItem([str(self.project_root)])
            proj_item.setData(0, Qt.UserRole, {"type": "project", "path": str(self.project_root)})
            ws_root.addChild(proj_item)
            files_dir = Path(self.project_root) / "files"
            if files_dir.exists():
                for tracked in sorted(files_dir.iterdir(), key=lambda p: p.name):
                    child = QTreeWidgetItem([tracked.name])
                    child.setData(0, Qt.UserRole, {"type": "tracked", "path": str(tracked)})
                    proj_item.addChild(child)
        else:
            no_item = QTreeWidgetItem(["(no project open)"])
            no_item.setDisabled(True)
            ws_root.addChild(no_item)

        self.tree.expandItem(ws_root)

        lr_root = QTreeWidgetItem(["Live roots"])
        lr_root.setData(0, Qt.UserRole, {"type": "live_roots_root"})
        self.tree.addTopLevelItem(lr_root)
        for root in fs.LIVE_ROOTS:
            root_item = QTreeWidgetItem([root])
            root_item.setData(0, Qt.UserRole, {"type": "root", "path": root})
            lr_root.addChild(root_item)
            try:
                children = fs.list_root_children(root)
                for child in children:
                    ci = QTreeWidgetItem([child.name])
                    ci.setData(0, Qt.UserRole, {"type": "file", "path": str(child)})
                    root_item.addChild(ci)
            except Exception:
                pass
        self.tree.expandItem(lr_root)

    # ---------- project actions ----------
    def on_create_project(self):
        parent_dir = QFileDialog.getExistingDirectory(self, "Choose parent folder for new project", DEFAULT_PROJECTS_ROOT)
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(self, "Project name", "Enter project name (no slashes):")
        if not ok or not name:
            QMessageBox.information(self, "Cancelled", "Project creation cancelled.")
            return
        pdir = Path(parent_dir) / name
        try:
            project_api.create_project(pdir, name, description="")
        except Exception as e:
            QMessageBox.critical(self, "Create project failed", str(e))
            return
        self.project_root = pdir.resolve()
        QMessageBox.information(self, "Project created", f"Project created at: {self.project_root}")
        self._populate_tree()

    def on_open_project(self):
        project_dir = QFileDialog.getExistingDirectory(self, "Choose existing project folder", DEFAULT_PROJECTS_ROOT)
        if not project_dir:
            return
        pj = Path(project_dir) / "project.json"
        if not pj.exists():
            QMessageBox.warning(self, "Not a project", "Selected folder does not contain project.json.")
            return
        try:
            project_api.load_project(Path(project_dir))
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.project_root = Path(project_dir).resolve()
        QMessageBox.information(self, "Project opened", f"Project opened: {self.project_root}")
        self._populate_tree()

    def on_close_project(self):
        self._maybe_autosave_before_change()
        self.project_root = None
        self.current_tracked_dir = None
        self.editor.clear()
        self._set_dirty(False)
        self._populate_tree()
        QMessageBox.information(self, "Project closed", "No project is open now.")

    # ---------- import ----------
    def on_import_clicked(self):
        if not self.current_live_path:
            QMessageBox.warning(self, "No file selected", "Select a live file on the left to import.")
            return
        if self.project_root:
            target_project = self.project_root
        else:
            project_root = QFileDialog.getExistingDirectory(self, "Choose project folder (or a folder to create a new project)", DEFAULT_PROJECTS_ROOT)
            if not project_root:
                return
            pjfile = Path(project_root) / "project.json"
            if not pjfile.exists():
                create = QMessageBox.question(self, "Project missing", "No project.json in that folder. Create a new project here?")
                if create == QMessageBox.Yes:
                    try:
                        project_api.create_project(Path(project_root), Path(project_root).name, description="")
                    except Exception as e:
                        QMessageBox.critical(self, "Create project failed", str(e))
                        return
                else:
                    return
            target_project = Path(project_root)

        try:
            tracked_dir = fs.import_file_to_project(self.current_live_path, Path(target_project))
            try:
                pj = project_api.load_project(Path(target_project))
                key = Path(tracked_dir).name
                if "tracked_files" not in pj:
                    pj["tracked_files"] = []
                if key not in pj["tracked_files"]:
                    pj["tracked_files"].append(key)
                    project_api.save_project(Path(target_project), pj)
            except Exception:
                pass

            QMessageBox.information(self, "Imported", f"Imported into: {tracked_dir}")
            self.project_root = Path(target_project)
            self._populate_tree()
            draft = fs.read_draft(tracked_dir)
            self._programmatic_change = True
            self.editor.setPlainText(draft)
            self._programmatic_change = False
            # open editable
            self.editor.setReadOnly(False)
            self.action_toggle_edit.setChecked(True)
            self.editor.setFocus()
            self.current_tracked_dir = tracked_dir
            self._set_dirty(False)
            self.lbl_meta.setText(self.lbl_meta.text() + f"\nImported -> {tracked_dir}")
            # refresh backups list for this tracked file
            self._refresh_backups_list()
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))

    # ---------- tree selection ----------
    def on_tree_item_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        try:
            self._maybe_autosave_before_change()
        except Exception:
            pass

        t = data.get("type")
        if t == "file":
            path = data.get("path")
            try:
                info = fs.read_live_file(path)
                self.current_live_path = path
                self._programmatic_change = True
                self.editor.setPlainText(info["contents"])
                self._programmatic_change = False
                self.editor.setReadOnly(True)
                self.action_toggle_edit.setChecked(False)
                self.lbl_meta.setText(f"Live: {path}\nSize: {info['size']} bytes\nMTime: {info['mtime']}")
                self.current_tracked_dir = None
                self._set_dirty(False)
                self.diff_view.setVisible(False)
                self._clear_backups_list()
            except Exception as e:
                QMessageBox.warning(self, "Read error", str(e))
        elif t == "tracked":
            tracked_dir = data.get("path")
            try:
                draft = fs.read_draft(tracked_dir)
                self._programmatic_change = True
                self.editor.setPlainText(draft)
                self._programmatic_change = False
                # open editable by default
                self.editor.setReadOnly(False)
                self.action_toggle_edit.setChecked(True)
                self.editor.setFocus()
                meta_path = Path(tracked_dir) / "meta.json"
                meta_text = meta_path.read_text() if meta_path.exists() else "{}"
                self.lbl_meta.setText(f"Tracked: {tracked_dir}\nmeta: {meta_text}")
                self.current_tracked_dir = tracked_dir
                try:
                    meta = json.loads(meta_text)
                    lp = meta.get("original_live_path")
                    if lp:
                        try:
                            liv = fs.read_live_file(lp)
                            self.current_live_path = lp
                        except Exception:
                            pass
                except Exception:
                    pass
                self._set_dirty(False)
                self.diff_view.setVisible(False)
                # refresh backups list
                self._refresh_backups_list()
            except Exception as e:
                QMessageBox.warning(self, "Open tracked file failed", str(e))
        elif t == "project":
            p = data.get("path")
            try:
                pj = project_api.load_project(Path(p))
                self.lbl_meta.setText(f"Project: {p}\n{pj.get('description','')}")
            except Exception as e:
                self.lbl_meta.setText(f"Project: {p}\n(Unable to read project.json: {e})")

    # ---------- backups UI helpers ----------
    def _clear_backups_list(self):
        self.list_backups_widget.clear()

    def _refresh_backups_list(self):
        """Populate the backups list widget for current_tracked_dir (or show empty)."""
        self.list_backups_widget.clear()
        if not self.current_tracked_dir:
            return
        try:
            items = fs.list_backups(self.current_tracked_dir)
            for it in items:
                label = f"{it['name']} — {it.get('mtime','')} — {it.get('size', 0)} bytes"
                lw = QListWidgetItem(label)
                lw.setData(Qt.UserRole, it)
                self.list_backups_widget.addItem(lw)
        except Exception as e:
            QMessageBox.warning(self, "Backups list failed", str(e))

    # ---------- editor handlers ----------
    def on_toggle_edit(self, checked: bool):
        if not checked:
            if self._dirty and self.current_tracked_dir:
                try:
                    self._save_draft_internal()
                except Exception as e:
                    QMessageBox.warning(self, "Save failed", f"Failed to save when leaving edit mode: {e}")
        self.editor.setReadOnly(not checked)

    def on_editor_text_changed(self):
        if self._programmatic_change:
            return
        self._set_dirty(True)

    def _save_draft_internal(self):
        if not self.current_tracked_dir:
            raise RuntimeError("No tracked file open")
        contents = self.editor.toPlainText()
        fs.save_draft(self.current_tracked_dir, contents)
        self._set_dirty(False)
        # after a save, refresh backups list (meta hash updated)
        self._refresh_backups_list()

    def on_save_draft(self):
        if not self.current_tracked_dir:
            QMessageBox.warning(self, "No tracked file", "Open/import a tracked file first.")
            return
        try:
            self._save_draft_internal()
            QMessageBox.information(self, "Saved", "Workspace draft saved.")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    # ---------- backups actions ----------
    def on_create_backup(self):
        if not self.current_tracked_dir:
            QMessageBox.warning(self, "No tracked file", "Import a file into a project first.")
            return
        try:
            b = fs.create_backup(self.current_tracked_dir)
            QMessageBox.information(self, "Backup created", f"Backup at {b}")
            self._refresh_backups_list()
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e))

    def _get_selected_backup(self):
        it = self.list_backups_widget.currentItem()
        if not it:
            return None
        data = it.data(Qt.UserRole)
        return data  # dict with keys name/path/mtime/size

    def on_restore_to_draft(self):
        sel = self._get_selected_backup()
        if not sel:
            QMessageBox.information(self, "Select backup", "Select a backup from the list first.")
            return
        if not self.current_tracked_dir:
            QMessageBox.warning(self, "No tracked file", "Open/import a tracked file first.")
            return
        try:
            res = fs.restore_backup_to_draft(self.current_tracked_dir, sel["path"])
            if res.get("ok"):
                QMessageBox.information(self, "Restored", res.get("message"))
                # reload draft into editor
                draft = fs.read_draft(self.current_tracked_dir)
                self._programmatic_change = True
                self.editor.setPlainText(draft)
                self._programmatic_change = False
                self._set_dirty(False)
            else:
                QMessageBox.critical(self, "Restore failed", res.get("message"))
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))

    def on_restore_to_live(self):
        sel = self._get_selected_backup()
        if not sel:
            QMessageBox.information(self, "Select backup", "Select a backup from the list first.")
            return
        if not self.current_tracked_dir:
            QMessageBox.warning(self, "No tracked file", "Open/import a tracked file first.")
            return
        # Attempt direct install -> if needs elevation, offer pkexec helper
        try:
            res = fs.restore_backup_to_live(self.current_tracked_dir, sel["path"], escalate_helper=ESCALATE_HELPER_PATH)
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))
            return

        if res.get("ok"):
            QMessageBox.information(self, "Installed", res.get("message"))
        else:
            if res.get("needs_elevation"):
                choice = QMessageBox.question(self, "Permission required",
                    f"{res.get('message')}\n\nRun privileged helper (pkexec) to finish the install now?")
                if choice == QMessageBox.Yes:
                    try:
                        res2 = fs.restore_backup_to_live(self.current_tracked_dir, sel["path"],
                                                         escalate_helper=ESCALATE_HELPER_PATH, try_escalate=True)
                        if res2.get("ok"):
                            QMessageBox.information(self, "Installed", res2.get("message"))
                        else:
                            QMessageBox.critical(self, "Install failed", res2.get("message"))
                    except Exception as e:
                        QMessageBox.critical(self, "Install failed", str(e))
                else:
                    QMessageBox.information(self, "Cancelled", "Install cancelled.")
            else:
                QMessageBox.critical(self, "Install failed", res.get("message"))

    # ---------- diff/push ----------
    def on_show_diff(self):
        if not self.current_tracked_dir and not self.current_live_path:
            QMessageBox.information(self, "Nothing to diff", "Open a live file or a tracked file first.")
            return
        lhs = ""
        rhs = ""
        if self.current_tracked_dir:
            try:
                lhs = fs.read_live_file(self.current_live_path)["contents"] if self.current_live_path else ""
            except Exception:
                lhs = ""
            try:
                rhs = fs.read_draft(self.current_tracked_dir)
            except Exception:
                rhs = ""
        else:
            lhs = fs.read_live_file(self.current_live_path)["contents"]
            rhs = self.editor.toPlainText()
        d = fs.diff_text(lhs, rhs, a_label="live", b_label="draft")
        self.diff_view.setPlainText(d)
        self.diff_view.setVisible(True)

    def on_push(self):
        if not self.current_tracked_dir:
            QMessageBox.warning(self, "No tracked file", "Import a file into a project first.")
            return
        if self._dirty:
            try:
                self._save_draft_internal()
                QMessageBox.information(self, "Auto-saved", "Unsaved draft was auto-saved before push.")
            except Exception as e:
                QMessageBox.critical(self, "Auto-save failed", f"Failed to save draft before push: {e}")
                return
        try:
            res = fs.push_draft_to_live(self.current_tracked_dir, escalate_helper=ESCALATE_HELPER_PATH)
        except Exception as e:
            QMessageBox.critical(self, "Push failed", str(e))
            return

        if res.get("ok"):
            QMessageBox.information(self, "Pushed", res.get("message"))
        else:
            if res.get("needs_elevation"):
                choice = QMessageBox.question(self, "Permission required",
                    f"{res.get('message')}\n\nRun privileged helper (pkexec) to finish the push now?")
                if choice == QMessageBox.Yes:
                    try:
                        res2 = fs.push_draft_to_live(self.current_tracked_dir, escalate_helper=ESCALATE_HELPER_PATH, try_escalate=True)
                        if res2.get("ok"):
                            QMessageBox.information(self, "Pushed", res2.get("message"))
                        else:
                            QMessageBox.critical(self, "Push failed", res2.get("message"))
                    except Exception as e:
                        QMessageBox.critical(self, "Push failed", str(e))
                else:
                    QMessageBox.information(self, "Cancelled", "Push cancelled.")
            else:
                QMessageBox.critical(self, "Push failed", res.get("message"))