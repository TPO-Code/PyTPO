from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMenuBar,
    QPushButton,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.reusable_file_dialog import FileDialog
from TPOPyside.widgets import CodeEditor
from TPOPyside.widgets.custom_window import Window
from TPOPyside.widgets.split_tab_workspace import SplitterTabWorkspace
from backend import fs
from backend import project as project_api
from barley_ide.file_dialog_settings import shared_file_dialog_settings
from ui.dialogs import DangerCodeDialog, MessageDialog, TextInputDialog

DEFAULT_PROJECTS_ROOT = Path.home() / "session-workspaces"
_LOCAL_HELPER_PATH = Path(__file__).resolve().parents[1] / "install_file_helper.py"
ESCALATE_HELPER_PATH = Path("/usr/lib/pytpo-session-workbench/install_file_helper.py")
if not ESCALATE_HELPER_PATH.exists():
    ESCALATE_HELPER_PATH = _LOCAL_HELPER_PATH


class FocusAwareCodeEditor(CodeEditor):
    focused = Signal()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focused.emit()


class SessionEditorPane(QWidget):
    titleChanged = Signal(object)
    activated = Signal(object)
    stateChanged = Signal()

    def __init__(
        self,
        *,
        editor_id: str,
        title: str,
        contents: str,
        live_path: str | None,
        tracked_dir: str | None,
        editable: bool,
        metadata: dict[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.editor_id = str(editor_id or "").strip()
        self._title = str(title or "Untitled")
        self.live_path = str(live_path or "").strip() or None
        self.tracked_dir = str(tracked_dir or "").strip() or None
        self._metadata = dict(metadata or {})
        self._saved_text = str(contents or "")
        self._programmatic_change = False
        self._dirty = False
        self._diff_visible = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header = QFrame(self)
        header.setObjectName("WorkbenchEditorHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(8)

        self.kind_label = QLabel("Tracked draft" if self.is_tracked() else "Live file", header)
        self.kind_label.setObjectName("WorkbenchEditorKind")
        header_layout.addWidget(self.kind_label, 0)

        self.path_label = QLabel(self._display_path(), header)
        self.path_label.setObjectName("WorkbenchEditorPath")
        self.path_label.setWordWrap(False)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setToolTip(self._display_path())
        header_layout.addWidget(self.path_label, 1)

        root.addWidget(header)

        self.editor = FocusAwareCodeEditor(self)
        self.editor.setObjectName("WorkbenchEditor")
        self.editor.setPlainText(self._saved_text)
        self.editor.setReadOnly(not editable)
        self.editor.setLineWrapMode(self.editor.LineWrapMode.NoWrap)
        if self.live_path or self.tracked_dir:
            self.editor.set_file_path(self.live_path or self.tracked_dir)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.focused.connect(self._emit_activated)
        root.addWidget(self.editor, 1)

        self.diff_view = FocusAwareCodeEditor(self)
        self.diff_view.setObjectName("WorkbenchDiffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(self.diff_view.LineWrapMode.NoWrap)
        self.diff_view.focused.connect(self._emit_activated)
        self.diff_view.setVisible(False)
        root.addWidget(self.diff_view, 1)

    def _display_path(self) -> str:
        if self.tracked_dir:
            return self.tracked_dir
        if self.live_path:
            return self.live_path
        return self._title

    def _emit_activated(self) -> None:
        self.activated.emit(self)

    def is_tracked(self) -> bool:
        return bool(self.tracked_dir)

    def is_dirty(self) -> bool:
        return self._dirty

    def is_editable(self) -> bool:
        return not self.editor.isReadOnly()

    def diff_visible(self) -> bool:
        return self._diff_visible

    def tab_title(self) -> str:
        suffix = " [draft]" if self.is_tracked() else " [live]"
        prefix = "* " if self.is_dirty() else ""
        return f"{prefix}{self._title}{suffix}"

    def display_name(self) -> str:
        return self.tab_title()

    def contents(self) -> str:
        return self.editor.toPlainText()

    def mark_clean(self) -> None:
        self._saved_text = self.contents()
        self._set_dirty(False)

    def replace_contents(self, text: str, *, editable: bool | None = None) -> None:
        self._programmatic_change = True
        self.editor.setPlainText(str(text or ""))
        self._programmatic_change = False
        self._saved_text = self.contents()
        self._set_dirty(False)
        if editable is not None:
            self.set_edit_mode(editable)

    def set_edit_mode(self, enabled: bool) -> None:
        self.editor.setReadOnly(not enabled)
        self.stateChanged.emit()

    def set_diff_text(self, diff_text: str) -> None:
        self.diff_view.setPlainText(str(diff_text or ""))
        self.diff_view.setVisible(True)
        self._diff_visible = True
        self.stateChanged.emit()

    def hide_diff(self) -> None:
        self.diff_view.clear()
        self.diff_view.setVisible(False)
        self._diff_visible = False
        self.stateChanged.emit()

    def metadata_text(self) -> str:
        lines = [
            f"Kind: {'Tracked draft' if self.is_tracked() else 'Live file'}",
            f"Title: {self._title}",
        ]
        if self.live_path:
            lines.append(f"Live path: {self.live_path}")
            exists_live = self._metadata.get("exists_live")
            if exists_live is not None:
                lines.append(f"Live exists: {'yes' if bool(exists_live) else 'no'}")
        if self.tracked_dir:
            lines.append(f"Tracked dir: {self.tracked_dir}")
        size = self._metadata.get("size")
        if size is not None:
            lines.append(f"Size: {size} bytes")
        mtime = self._metadata.get("mtime")
        if mtime:
            lines.append(f"MTime: {mtime}")
        if self.tracked_dir:
            meta_text = self._metadata.get("meta_text")
            if meta_text:
                lines.append("")
                lines.append(str(meta_text))
        return "\n".join(lines)

    def _on_text_changed(self) -> None:
        if self._programmatic_change:
            return
        self._set_dirty(self.contents() != self._saved_text)

    def _set_dirty(self, dirty: bool) -> None:
        dirty = bool(dirty)
        if self._dirty == dirty:
            self.stateChanged.emit()
            return
        self._dirty = dirty
        self.titleChanged.emit(self)
        self.stateChanged.emit()


class WorkbenchWorkspace(SplitterTabWorkspace):
    def __init__(self, host: "MainWindow", parent: QWidget | None = None) -> None:
        self._host = host
        super().__init__(parent)

    def confirm_close_editor(self, editor: QWidget, parent: QWidget | None = None) -> bool:
        return self._host.confirm_close_editor(editor, parent)


class MainWindow(Window):
    def __init__(self) -> None:
        super().__init__(use_native_chrome=False)
        self.setWindowTitle("PyTPO Session Workbench")
        self.resize(1440, 920)
        self._project_root: Path | None = None
        self._tree_items_by_key: dict[str, QTreeWidgetItem] = {}
        self._tracked_dirs_by_live_path: dict[str, str] = {}
        self._file_dialog_settings = shared_file_dialog_settings()

        self._build_actions()
        self._build_window_controls()
        self._build_workbench_ui()
        self._apply_local_style()
        self._populate_tree()
        self._refresh_ui_for_active_editor()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30 * 1000)
        self._autosave_timer.timeout.connect(self._autosave_dirty_editors)
        self._autosave_timer.start()
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_actions(self) -> None:
        self.action_create_project = QAction("New Project...", self)
        self.action_create_project.triggered.connect(self.on_create_project)
        self.action_open_project = QAction("Open Project...", self)
        self.action_open_project.triggered.connect(self.on_open_project)
        self.action_close_project = QAction("Close Project", self)
        self.action_close_project.triggered.connect(self.on_close_project)
        self.action_new_file = QAction("Create Workspace File...", self)
        self.action_new_file.triggered.connect(self.on_create_workspace_file)
        self.action_import = QAction("Import Live File", self)
        self.action_import.triggered.connect(self.on_import_clicked)
        self.action_toggle_edit = QAction("Edit", self)
        self.action_toggle_edit.setCheckable(True)
        self.action_toggle_edit.toggled.connect(self.on_toggle_edit)
        self.action_save = QAction("Save Draft", self)
        self.action_save.triggered.connect(self.on_save_draft)
        self.action_show_diff = QAction("Show Diff", self)
        self.action_show_diff.setCheckable(True)
        self.action_show_diff.toggled.connect(self.on_toggle_diff)
        self.action_create_backup = QAction("Create Backup", self)
        self.action_create_backup.triggered.connect(self.on_create_backup)
        self.action_restore_draft = QAction("Restore Backup to Draft", self)
        self.action_restore_draft.triggered.connect(self.on_restore_to_draft)
        self.action_restore_live = QAction("Restore Backup to Live", self)
        self.action_restore_live.triggered.connect(self.on_restore_to_live)
        self.action_push = QAction("Push Draft to Live", self)
        self.action_push.triggered.connect(self.on_push)
        self.action_remove_from_project = QAction("Remove from Project", self)
        self.action_remove_from_project.triggered.connect(self.on_remove_from_project)
        self.action_delete_from_system = QAction("Delete from System", self)
        self.action_delete_from_system.triggered.connect(self.on_delete_from_system)
        self.action_close_tab = QAction("Close Tab", self)
        self.action_close_tab.triggered.connect(self._close_current_editor)
        self.action_exit = QAction("Exit", self)
        self.action_exit.triggered.connect(self.close)

    def _build_window_controls(self) -> None:
        menu_bar = QMenuBar(self)
        file_menu = menu_bar.addMenu("File")
        file_menu.addAction(self.action_create_project)
        file_menu.addAction(self.action_open_project)
        file_menu.addAction(self.action_close_project)
        file_menu.addSeparator()
        file_menu.addAction(self.action_new_file)
        file_menu.addSeparator()
        file_menu.addAction(self.action_exit)

        edit_menu = menu_bar.addMenu("Edit")
        edit_menu.addAction(self.action_toggle_edit)
        edit_menu.addAction(self.action_save)
        edit_menu.addAction(self.action_close_tab)

        self.add_window_left_control(menu_bar)

        quick_actions = QWidget(self)
        quick_actions.setObjectName("WorkbenchQuickActions")
        quick_layout = QHBoxLayout(quick_actions)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(6)
        for action in (
            self.action_new_file,
            self.action_toggle_edit,
            self.action_save,
            self.action_close_tab,
        ):
            button = QToolButton(quick_actions)
            button.setDefaultAction(action)
            quick_layout.addWidget(button)
        self.add_window_right_control(quick_actions)

        self.project_badge = QLabel("No project", self)
        self.project_badge.setObjectName("WorkbenchProjectBadge")
        self.add_window_right_control(self.project_badge)

    def _build_workbench_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        summary = QFrame(host)
        summary.setObjectName("WorkbenchSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.setSpacing(10)

        self.current_document_label = QLabel("Open a live or tracked file from the explorer.", summary)
        self.current_document_label.setObjectName("WorkbenchCurrentDocument")
        self.current_document_label.setWordWrap(True)
        summary_layout.addWidget(self.current_document_label, 1)

        self.workspace_hint_label = QLabel("Drag tabs toward pane edges to split the workspace.", summary)
        self.workspace_hint_label.setObjectName("WorkbenchHint")
        summary_layout.addWidget(self.workspace_hint_label, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(summary)

        main_splitter = QSplitter(Qt.Orientation.Horizontal, host)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(6)
        root.addWidget(main_splitter, 1)

        main_splitter.addWidget(self._build_sidebar(main_splitter))
        main_splitter.addWidget(self._build_workspace_panel(main_splitter))
        main_splitter.addWidget(self._build_inspector_panel(main_splitter))
        main_splitter.setSizes([280, 840, 320])

    def _build_sidebar(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("WorkbenchSidebar")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Explorer", panel)
        title.setObjectName("WorkbenchSectionTitle")
        layout.addWidget(title)

        hint = QLabel("Right-click workspace and live items for import, diff, backup, push, and delete actions.", panel)
        hint.setWordWrap(True)
        hint.setObjectName("WorkbenchContextHint")
        layout.addWidget(hint)

        self.tree = QTreeWidget(panel)
        self.tree.setHeaderLabels(["Workspaces / Live roots"])
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.itemSelectionChanged.connect(self._refresh_action_states)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        layout.addWidget(self.tree, 1)
        return panel

    def _build_workspace_panel(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("WorkbenchWorkspacePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        action_row = QWidget(panel)
        action_row.setObjectName("WorkbenchActionRow")
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        for action in (
            self.action_toggle_edit,
            self.action_save,
            self.action_new_file,
            self.action_close_tab,
        ):
            button = QToolButton(action_row)
            button.setDefaultAction(action)
            action_layout.addWidget(button)
        action_layout.addStretch(1)
        layout.addWidget(action_row)

        self.workspace = WorkbenchWorkspace(self, panel)
        self.workspace.stateChanged.connect(self._refresh_ui_for_active_editor)
        layout.addWidget(self.workspace, 1)
        return panel

    def _build_inspector_panel(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("WorkbenchInspector")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Inspector", panel)
        title.setObjectName("WorkbenchSectionTitle")
        layout.addWidget(title)

        self.lbl_meta = QLabel("", panel)
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.lbl_meta)

        context_hint = QLabel("Use the explorer context menu for import, diff, backup, push, remove, and delete actions.", panel)
        context_hint.setWordWrap(True)
        context_hint.setObjectName("WorkbenchContextHint")
        layout.addWidget(context_hint)

        backups_title = QLabel("Backups", panel)
        backups_title.setObjectName("WorkbenchSectionTitle")
        layout.addWidget(backups_title)

        self.list_backups_widget = QListWidget(panel)
        self.list_backups_widget.itemSelectionChanged.connect(self._refresh_action_states)
        layout.addWidget(self.list_backups_widget, 1)

        backup_actions = QWidget(panel)
        backup_actions_layout = QVBoxLayout(backup_actions)
        backup_actions_layout.setContentsMargins(0, 0, 0, 0)
        backup_actions_layout.setSpacing(6)

        for action in (self.action_restore_draft, self.action_restore_live):
            button = QPushButton(action.text(), backup_actions)
            button.clicked.connect(lambda _checked=False, bound_action=action: bound_action.trigger())
            backup_actions_layout.addWidget(button)
        layout.addWidget(backup_actions)

        return panel

    def _apply_local_style(self) -> None:
        self.setStyleSheet(
            """
            #WorkbenchSidebar, #WorkbenchWorkspacePanel, #WorkbenchInspector, #WorkbenchSummary, #WorkbenchEditorHeader {
                border: 1px solid palette(mid);
                border-radius: 10px;
            }
            #WorkbenchSectionTitle {
                font-weight: 700;
                font-size: 14px;
            }
            #WorkbenchProjectBadge, #WorkbenchEditorKind {
                padding: 4px 10px;
                border: 1px solid palette(mid);
                border-radius: 999px;
                font-weight: 600;
            }
            #WorkbenchHint, #WorkbenchEditorPath {
                color: palette(mid);
            }
            #WorkbenchContextHint {
                color: palette(mid);
            }
            """
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_editor(self) -> SessionEditorPane | None:
        editor = self.workspace.current_editor()
        return editor if isinstance(editor, SessionEditorPane) else None

    def _all_open_editors(self) -> list[SessionEditorPane]:
        return [editor for editor in self.workspace.all_editors() if isinstance(editor, SessionEditorPane)]

    def _tree_key_for_editor(self, editor: SessionEditorPane) -> str | None:
        if editor.tracked_dir:
            return f"tracked:{editor.tracked_dir}"
        if editor.live_path:
            return f"live:{editor.live_path}"
        return None

    def _set_project_root(self, project_root: Path | None) -> None:
        self._project_root = project_root.resolve() if isinstance(project_root, Path) else None
        badge_text = self._project_root.name if self._project_root else "No project"
        self.project_badge.setText(badge_text)
        self._populate_tree()
        self._refresh_ui_for_active_editor()

    def _set_status(self, message: str) -> None:
        self.statusBar().showMessage(str(message or ""), 6000)

    def _file_dialog_directory(self) -> str:
        if self._project_root is not None:
            return str(self._project_root)
        return str(DEFAULT_PROJECTS_ROOT)

    def _default_live_directory(self) -> str:
        editor = self._current_editor()
        if editor is not None and editor.live_path:
            return str(Path(editor.live_path).expanduser().parent)
        for root in fs.LIVE_ROOTS:
            candidate = Path(root).expanduser()
            if candidate.is_dir():
                return str(candidate)
        return str(Path.home())

    def _choose_directory(self, caption: str, directory: str | Path | None = None) -> str:
        selected, _starred = FileDialog.getExistingDirectory(
            self,
            caption=caption,
            directory=str(directory or self._file_dialog_directory()),
            starred_paths_settings=self._file_dialog_settings,
        )
        return str(selected or "").strip()

    def _workspace_target_dir_from_context(self) -> str | None:
        data = self._current_tree_item_data()
        if isinstance(data, dict):
            kind = str(data.get("type") or "").strip()
            if kind in {"root", "tracked_root", "tracked_group"}:
                target_dir = str(data.get("path") or "").strip()
                if target_dir:
                    return target_dir
            if kind == "tracked":
                live_path = str(data.get("live_path") or "").strip()
                if live_path:
                    return str(Path(live_path).expanduser().parent)
            if kind == "file":
                live_path = str(data.get("path") or "").strip()
                if live_path:
                    return str(Path(live_path).expanduser().parent)

        editor = self._current_editor()
        if editor is not None and editor.live_path:
            return str(Path(editor.live_path).expanduser().parent)
        return None

    def _select_tree_item_for_editor(self, editor: SessionEditorPane | None) -> None:
        if editor is None:
            return
        key = self._tree_key_for_editor(editor)
        if not key:
            return
        item = self._tree_items_by_key.get(key)
        if item is None:
            return
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    def _register_editor(self, editor: SessionEditorPane) -> SessionEditorPane:
        editor.stateChanged.connect(self._refresh_ui_for_active_editor)
        editor.destroyed.connect(self._refresh_ui_for_active_editor)
        return editor

    def _tracked_key_for_dir(self, tracked_dir: str | Path) -> str:
        return str(Path(tracked_dir).name)

    def _tracked_editor_payload(self, tracked_dir: str) -> tuple[str | None, dict[str, object], str]:
        normalized = str(Path(tracked_dir).expanduser())
        meta_path = Path(normalized) / "meta.json"
        meta_text = meta_path.read_text(encoding="utf-8") if meta_path.exists() else "{}"
        metadata: dict[str, object] = {"meta_text": meta_text}
        live_path = None
        title = Path(normalized).name
        try:
            meta = json.loads(meta_text)
        except Exception:
            meta = {}

        live_path_value = str(meta.get("original_live_path") or "").strip()
        if live_path_value:
            live_path = live_path_value
            title = Path(live_path).name or title
            metadata["exists_live"] = bool(meta.get("exists_live", False))
            try:
                live_info = fs.read_live_file(live_path)
                metadata["size"] = live_info.get("size")
                metadata["mtime"] = live_info.get("mtime")
            except Exception:
                metadata["mtime"] = "(missing)"
        return live_path, metadata, title

    def _refresh_tracked_editor_metadata(self, editor: SessionEditorPane | None) -> None:
        if editor is None or not editor.tracked_dir:
            return
        live_path, metadata, _title = self._tracked_editor_payload(editor.tracked_dir)
        editor.live_path = live_path
        editor._metadata = metadata
        editor.path_label.setText(editor._display_path())
        editor.path_label.setToolTip(editor._display_path())

    def _project_tracked_dirs(self) -> list[Path]:
        if self._project_root is None:
            return []
        try:
            return project_api.tracked_file_dirs(self._project_root)
        except Exception:
            return []

    def _tracked_target_path(self, tracked_dir: str | Path) -> Path | None:
        tracked_path = Path(tracked_dir).expanduser()
        meta_path = tracked_path / "meta.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        live_path = str(meta.get("original_live_path") or "").strip()
        if not live_path:
            return None
        return Path(live_path).expanduser()

    def _best_live_root_for_path(self, target_path: str | Path) -> Path | None:
        candidate = Path(target_path).expanduser()
        best_match: Path | None = None
        for root in fs.LIVE_ROOTS:
            root_path = Path(root).expanduser()
            try:
                candidate.relative_to(root_path)
            except ValueError:
                continue
            if best_match is None or len(root_path.parts) > len(best_match.parts):
                best_match = root_path
        return best_match

    def _tracked_dir_for_live_path(self, live_path: str | Path) -> str | None:
        return self._tracked_dirs_by_live_path.get(str(Path(live_path).expanduser()))

    def _find_open_tracked_editor(self, tracked_dir: str | Path) -> SessionEditorPane | None:
        normalized = str(Path(tracked_dir).expanduser())
        for editor in self._all_open_editors():
            if editor.tracked_dir == normalized:
                return editor
        return None

    def _tracked_draft_contents(self, tracked_dir: str | Path) -> str:
        open_editor = self._find_open_tracked_editor(tracked_dir)
        if open_editor is not None:
            return open_editor.contents()
        return fs.read_draft(str(tracked_dir))

    def _tracked_state_for_dir(self, tracked_dir: str | Path) -> dict[str, str | bool | None]:
        tracked_path = Path(tracked_dir).expanduser()
        live_path = self._tracked_target_path(tracked_path)
        state: dict[str, str | bool | None] = {
            "tracked_dir": str(tracked_path),
            "live_path": str(live_path) if live_path is not None else None,
            "status": "missing_live",
            "label": "Live target missing",
            "exists_live": False,
        }
        if live_path is None:
            state["label"] = "Tracked file has no live target"
            return state
        if not live_path.exists():
            return state
        try:
            live_contents = str(fs.read_live_file(str(live_path)).get("contents", ""))
            draft_contents = self._tracked_draft_contents(tracked_path)
        except Exception:
            state["label"] = "Unable to compare against live target"
            return state
        state["exists_live"] = True
        if draft_contents == live_contents:
            state["status"] = "synced"
            state["label"] = "Matches live system"
            return state
        state["status"] = "different"
        state["label"] = "Differs from live system"
        return state

    def _apply_tree_item_state(
        self,
        item: QTreeWidgetItem,
        *,
        tooltip: str,
        status: str | None = None,
    ) -> None:
        default_brush = self.tree.palette().brush(self.tree.foregroundRole())
        amber_brush = QBrush(QColor("#d7a43b"))
        missing_brush = QBrush(QColor("#8e97a3"))
        item.setToolTip(0, str(tooltip or ""))
        if status == "different":
            item.setForeground(0, amber_brush)
            return
        if status == "missing_live":
            item.setForeground(0, missing_brush)
            return
        item.setForeground(0, default_brush)

    def _refresh_tree_item_states(self) -> None:
        for key, item in self._tree_items_by_key.items():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                continue
            kind = str(data.get("type") or "").strip()
            if kind == "tracked":
                tracked_dir = str(data.get("path") or "").strip()
                if not tracked_dir:
                    continue
                state = self._tracked_state_for_dir(tracked_dir)
                live_path = str(state.get("live_path") or "").strip()
                tooltip = str(state.get("label") or "Tracked file")
                if live_path:
                    tooltip = f"{tooltip}\n{live_path}"
                self._apply_tree_item_state(item, tooltip=tooltip, status=str(state.get("status") or ""))
                continue
            if kind == "file":
                live_path = str(data.get("path") or "").strip()
                tracked_dir = str(data.get("tracked_dir") or "").strip()
                if tracked_dir:
                    state = self._tracked_state_for_dir(tracked_dir)
                    tooltip = (
                        f"Live system file\n{state.get('label') or 'Tracked in workspace'}\n"
                        f"Tracked draft: {tracked_dir}"
                    )
                    self._apply_tree_item_state(item, tooltip=tooltip, status=str(state.get("status") or ""))
                    continue
                self._apply_tree_item_state(
                    item,
                    tooltip=f"Live system file\nNot tracked in the current project.\n{live_path}",
                )

    def _current_tree_item_data(self) -> dict[str, object] | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _tracked_dir_from_tree_data(self, data: dict[str, object] | None) -> str | None:
        if not isinstance(data, dict):
            return None
        kind = str(data.get("type") or "").strip()
        if kind == "tracked":
            tracked_dir = str(data.get("path") or "").strip()
            return tracked_dir or None
        if kind == "file":
            tracked_dir = str(data.get("tracked_dir") or "").strip()
            return tracked_dir or None
        return None

    def _open_tracked_editor_for_path(self, tracked_dir: str | Path) -> SessionEditorPane | None:
        normalized = str(Path(tracked_dir).expanduser())
        try:
            self._open_tracked_editor(normalized)
        except Exception as exc:
            MessageDialog.warning(self, "Open Tracked File", str(exc))
            return None
        editor = self._current_editor()
        if editor is None or editor.tracked_dir != normalized:
            return None
        return editor

    def _open_live_editor_for_path(self, live_path: str | Path) -> None:
        try:
            self._open_live_editor(str(Path(live_path).expanduser()))
        except Exception as exc:
            MessageDialog.warning(self, "Open Live File", str(exc))

    def _save_tracked_dir(self, tracked_dir: str | Path) -> None:
        if self._open_tracked_editor_for_path(tracked_dir) is None:
            return
        self.on_save_draft()

    def _show_diff_for_tracked_dir(self, tracked_dir: str | Path) -> None:
        editor = self._open_tracked_editor_for_path(tracked_dir)
        if editor is None:
            return
        self._set_action_checked(self.action_show_diff, True)
        self.on_toggle_diff(True)

    def _push_tracked_dir(self, tracked_dir: str | Path) -> None:
        if self._open_tracked_editor_for_path(tracked_dir) is None:
            return
        self.on_push()

    def _create_backup_for_tracked_dir(self, tracked_dir: str | Path) -> None:
        if self._open_tracked_editor_for_path(tracked_dir) is None:
            return
        self.on_create_backup()

    def _remove_tracked_dir_from_project(self, tracked_dir: str | Path) -> None:
        if self._open_tracked_editor_for_path(tracked_dir) is None:
            return
        self.on_remove_from_project()

    def _delete_tracked_dir_from_system(self, tracked_dir: str | Path) -> None:
        if self._open_tracked_editor_for_path(tracked_dir) is None:
            return
        self.on_delete_from_system()

    def _add_menu_action(self, menu: QMenu, label: str, callback) -> None:
        action = menu.addAction(label)
        action.triggered.connect(callback)

    def on_tree_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        self._refresh_action_states()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return

        kind = str(data.get("type") or "").strip()
        tracked_dir = self._tracked_dir_from_tree_data(data)
        live_path = str(data.get("path") or "").strip() if kind == "file" else ""
        if kind == "tracked" and not live_path:
            tracked_live = self._tracked_target_path(str(data.get("path") or "").strip())
            live_path = str(tracked_live) if tracked_live is not None else ""

        menu = QMenu(self)

        if kind in {"root", "tracked_root", "tracked_group", "tracked", "file"} and self._project_root is not None:
            menu.addAction(self.action_new_file)

        if kind == "tracked":
            self._add_menu_action(menu, "Open Draft", lambda: self._open_tracked_editor_for_path(str(data.get("path") or "")))
            if live_path:
                self._add_menu_action(menu, "Open Live File", lambda: self._open_live_editor_for_path(live_path))
            menu.addSeparator()
            self._add_menu_action(menu, "Save Draft", lambda: self._save_tracked_dir(str(data.get("path") or "")))
            self._add_menu_action(menu, "Show Diff", lambda: self._show_diff_for_tracked_dir(str(data.get("path") or "")))
            self._add_menu_action(menu, "Create Backup", lambda: self._create_backup_for_tracked_dir(str(data.get("path") or "")))
            self._add_menu_action(menu, "Push Draft to Live", lambda: self._push_tracked_dir(str(data.get("path") or "")))
            menu.addSeparator()
            self._add_menu_action(menu, "Remove from Project", lambda: self._remove_tracked_dir_from_project(str(data.get("path") or "")))
            self._add_menu_action(menu, "Delete from System", lambda: self._delete_tracked_dir_from_system(str(data.get("path") or "")))

        if kind == "file":
            self._add_menu_action(menu, "Open Live File", lambda: self._open_live_editor_for_path(live_path))
            if tracked_dir:
                self._add_menu_action(menu, "Open Draft", lambda: self._open_tracked_editor_for_path(tracked_dir))
                menu.addSeparator()
                self._add_menu_action(menu, "Show Diff", lambda: self._show_diff_for_tracked_dir(tracked_dir))
                self._add_menu_action(menu, "Create Backup", lambda: self._create_backup_for_tracked_dir(tracked_dir))
                self._add_menu_action(menu, "Push Draft to Live", lambda: self._push_tracked_dir(tracked_dir))
                menu.addSeparator()
                self._add_menu_action(menu, "Remove from Project", lambda: self._remove_tracked_dir_from_project(tracked_dir))
                self._add_menu_action(menu, "Delete from System", lambda: self._delete_tracked_dir_from_system(tracked_dir))
            else:
                menu.addSeparator()
                menu.addAction(self.action_import)

        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def _focus_editor(self, editor: SessionEditorPane) -> None:
        self.workspace.focus_editor(editor)
        editor.editor.setFocus()
        self._refresh_ui_for_active_editor()

    def _open_live_editor(self, path: str) -> None:
        normalized = str(Path(path).expanduser())
        existing, _tabs = self.workspace.find_editor(f"live:{normalized}")
        if isinstance(existing, SessionEditorPane):
            self._focus_editor(existing)
            return
        info = fs.read_live_file(normalized)
        editor = self._register_editor(
            SessionEditorPane(
                editor_id=f"live:{normalized}",
                title=Path(normalized).name,
                contents=str(info.get("contents", "")),
                live_path=normalized,
                tracked_dir=None,
                editable=False,
                metadata={"size": info.get("size"), "mtime": info.get("mtime")},
            )
        )
        self.workspace.add_editor(editor)
        self._focus_editor(editor)

    def _open_tracked_editor(self, tracked_dir: str) -> None:
        normalized = str(Path(tracked_dir).expanduser())
        existing, _tabs = self.workspace.find_editor(f"tracked:{normalized}")
        if isinstance(existing, SessionEditorPane):
            self._refresh_tracked_editor_metadata(existing)
            self._focus_editor(existing)
            return

        live_path, metadata, title = self._tracked_editor_payload(normalized)

        editor = self._register_editor(
            SessionEditorPane(
                editor_id=f"tracked:{normalized}",
                title=title,
                contents=fs.read_draft(normalized),
                live_path=live_path,
                tracked_dir=normalized,
                editable=True,
                metadata=metadata,
            )
        )
        self.workspace.add_editor(editor)
        self._focus_editor(editor)

    def _populate_tree(self) -> None:
        self.tree.clear()
        self._tree_items_by_key.clear()
        self._tracked_dirs_by_live_path.clear()

        workspaces_root = QTreeWidgetItem(["Workspaces"])
        workspaces_root.setData(0, Qt.ItemDataRole.UserRole, {"type": "workspaces_root"})
        self.tree.addTopLevelItem(workspaces_root)

        if self._project_root is not None:
            project_item = QTreeWidgetItem([str(self._project_root)])
            project_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {"type": "project", "path": str(self._project_root)},
            )
            workspaces_root.addChild(project_item)
            self._tree_items_by_key[f"project:{self._project_root}"] = project_item

            group_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
            for root in fs.LIVE_ROOTS:
                root_key = ("tracked_root", root)
                root_item = QTreeWidgetItem([root])
                root_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"type": "tracked_root", "path": root},
                )
                project_item.addChild(root_item)
                group_nodes[root_key] = root_item

            tracked_entries: list[tuple[int, str, Path, Path | None, Path | None]] = []
            for tracked in self._project_tracked_dirs():
                target_path = self._tracked_target_path(tracked)
                live_root_path = self._best_live_root_for_path(target_path) if target_path is not None else None
                root_index = len(fs.LIVE_ROOTS)
                if live_root_path is not None:
                    try:
                        root_index = fs.LIVE_ROOTS.index(str(live_root_path))
                    except ValueError:
                        root_index = len(fs.LIVE_ROOTS)
                sort_key = str(target_path or tracked.name).lower()
                tracked_entries.append((root_index, sort_key, tracked, target_path, live_root_path))
                if target_path is not None:
                    self._tracked_dirs_by_live_path[str(target_path)] = str(tracked)
            tracked_entries.sort(key=lambda item: (item[0], item[1]))

            unknown_group: QTreeWidgetItem | None = None

            for _root_index, _sort_key, tracked, target_path, live_root_path in tracked_entries:
                parent_item = project_item
                leaf_label = tracked.name

                if target_path is not None:
                    if live_root_path is not None:
                        root_key = ("tracked_root", str(live_root_path))
                        root_item = group_nodes[root_key]
                        parent_item = root_item
                        relative_parent = target_path.relative_to(live_root_path).parent
                        relative_parts = tuple(part for part in relative_parent.parts if part not in {"", "."})
                        prefix: tuple[str, ...] = root_key
                        current_group_path = live_root_path
                    else:
                        root_label = "Other targets"
                        root_key = ("tracked_root", root_label)
                        root_item = group_nodes.get(root_key)
                        if root_item is None:
                            root_item = QTreeWidgetItem([root_label])
                            root_item.setData(
                                0,
                                Qt.ItemDataRole.UserRole,
                                {"type": "tracked_root", "path": ""},
                            )
                            group_nodes[root_key] = root_item
                            project_item.addChild(root_item)
                        parent_item = root_item
                        relative_parts = tuple(part for part in target_path.parent.parts if part not in {"", "."})
                        prefix = root_key
                        current_group_path = Path("/")

                    for part in relative_parts:
                        prefix = (*prefix, part)
                        current_group_path = current_group_path / part
                        group_item = group_nodes.get(prefix)
                        if group_item is None:
                            label = part
                            group_item = QTreeWidgetItem([label])
                            group_item.setData(
                                0,
                                Qt.ItemDataRole.UserRole,
                                {"type": "tracked_group", "path": str(current_group_path)},
                            )
                            group_nodes[prefix] = group_item
                            parent_group = group_nodes[prefix[:-1]]
                            parent_group.addChild(group_item)
                        parent_item = group_item
                    leaf_label = target_path.name or tracked.name
                else:
                    if unknown_group is None:
                        unknown_group = QTreeWidgetItem(["(unknown target)"])
                        unknown_group.setData(
                            0,
                            Qt.ItemDataRole.UserRole,
                            {"type": "tracked_group", "path": ""},
                        )
                        project_item.addChild(unknown_group)
                    parent_item = unknown_group

                child = QTreeWidgetItem([leaf_label])
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {
                        "type": "tracked",
                        "path": str(tracked),
                        "live_path": str(target_path) if target_path is not None else "",
                    },
                )
                parent_item.addChild(child)
                self._tree_items_by_key[f"tracked:{tracked}"] = child
        else:
            empty_item = QTreeWidgetItem(["(no project open)"])
            empty_item.setDisabled(True)
            workspaces_root.addChild(empty_item)

        live_root = QTreeWidgetItem(["Live roots"])
        live_root.setData(0, Qt.ItemDataRole.UserRole, {"type": "live_roots_root"})
        self.tree.addTopLevelItem(live_root)

        for root in fs.LIVE_ROOTS:
            root_item = QTreeWidgetItem([root])
            root_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "root", "path": root})
            live_root.addChild(root_item)
            try:
                for child in fs.list_root_children(root):
                    child_item = QTreeWidgetItem([child.name])
                    tracked_dir = self._tracked_dir_for_live_path(child)
                    child_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        {"type": "file", "path": str(child), "tracked_dir": tracked_dir or ""},
                    )
                    root_item.addChild(child_item)
                    self._tree_items_by_key[f"live:{child}"] = child_item
            except Exception:
                continue

        self.tree.expandItem(workspaces_root)
        if self._project_root is not None:
            self.tree.expandItem(project_item)
        self.tree.expandItem(live_root)
        self._refresh_tree_item_states()

    def _refresh_backups_list(self, editor: SessionEditorPane | None) -> None:
        selected_path = None
        current_item = self.list_backups_widget.currentItem()
        if current_item is not None:
            current_data = current_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(current_data, dict):
                selected_path = str(current_data.get("path") or "").strip() or None
        self.list_backups_widget.clear()
        if editor is None or not editor.tracked_dir:
            return
        try:
            for item in fs.list_backups(editor.tracked_dir):
                label = f"{item['name']} - {item.get('mtime', '')} - {item.get('size', 0)} bytes"
                widget_item = QListWidgetItem(label)
                widget_item.setData(Qt.ItemDataRole.UserRole, item)
                self.list_backups_widget.addItem(widget_item)
                if selected_path and str(item.get("path") or "").strip() == selected_path:
                    self.list_backups_widget.setCurrentItem(widget_item)
        except Exception as exc:
            MessageDialog.warning(self, "Backups", f"Unable to read backups.\n\n{exc}")

    def _refresh_action_states(self) -> None:
        editor = self._current_editor()
        has_editor = editor is not None
        tracked = has_editor and editor.is_tracked()
        has_backup = self.list_backups_widget.currentItem() is not None

        self.action_toggle_edit.setEnabled(bool(tracked))
        self.action_save.setEnabled(bool(tracked))
        self.action_show_diff.setEnabled(has_editor)
        self.action_new_file.setEnabled(self._project_root is not None)
        self.action_import.setEnabled(bool(self._selected_live_path_from_context()))
        self.action_create_backup.setEnabled(bool(tracked))
        self.action_restore_draft.setEnabled(bool(tracked and has_backup))
        self.action_restore_live.setEnabled(bool(tracked and has_backup))
        self.action_push.setEnabled(bool(tracked))
        self.action_remove_from_project.setEnabled(bool(tracked and self._project_root is not None))
        self.action_delete_from_system.setEnabled(bool(tracked))
        self.action_close_tab.setEnabled(has_editor)
        self.action_close_project.setEnabled(self._project_root is not None)

    def _refresh_ui_for_active_editor(self) -> None:
        editor = self._current_editor()
        self._select_tree_item_for_editor(editor)
        self._refresh_tree_item_states()
        self._refresh_backups_list(editor)
        self._refresh_action_states()

        dirty_count = sum(1 for item in self._all_open_editors() if item.is_dirty())
        title = "PyTPO Session Workbench"
        if self._project_root is not None:
            title = f"{title} [{self._project_root.name}]"
        if dirty_count:
            title = f"{title} *{dirty_count}"
        self.setWindowTitle(title)

        if editor is None:
            self.current_document_label.setText("Open a live or tracked file from the explorer.")
            self.lbl_meta.setText("No active document.")
            self._set_action_checked(self.action_toggle_edit, False)
            self._set_action_checked(self.action_show_diff, False)
            return

        state_bits = ["editable" if editor.is_editable() else "read-only"]
        if editor.is_dirty():
            state_bits.append("unsaved")
        if editor.diff_visible():
            state_bits.append("diff visible")
        self.current_document_label.setText(f"{editor.tab_title()} | {', '.join(state_bits)}")
        self.lbl_meta.setText(editor.metadata_text())
        self._set_action_checked(self.action_toggle_edit, editor.is_editable())
        self._set_action_checked(self.action_show_diff, editor.diff_visible())

    def _set_action_checked(self, action: QAction, checked: bool) -> None:
        blocked = action.blockSignals(True)
        action.setChecked(bool(checked))
        action.blockSignals(blocked)

    def _selected_live_path_from_context(self) -> str | None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(data, dict) and data.get("type") == "file":
            text = str(data.get("path") or "").strip()
            return text or None
        editor = self._current_editor()
        if isinstance(editor, SessionEditorPane) and editor.live_path and not editor.is_tracked():
            return editor.live_path
        return None

    def _selected_backup(self) -> dict[str, object] | None:
        item = self.list_backups_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _close_tracked_editors_for_path(self, tracked_dir: str) -> bool:
        normalized = str(Path(tracked_dir).expanduser())
        for editor in list(self._all_open_editors()):
            if editor.tracked_dir != normalized:
                continue
            if not self.workspace.close_editor(editor, self):
                return False
        return True

    def _close_current_editor(self) -> None:
        self.workspace.close_current_editor(self)

    def _save_editor(self, editor: SessionEditorPane, *, notify: bool) -> bool:
        if not editor.tracked_dir:
            if notify:
                MessageDialog.warning(self, "Save Draft", "Open a tracked file first.")
            return False
        try:
            fs.save_draft(editor.tracked_dir, editor.contents())
        except Exception as exc:
            if notify:
                MessageDialog.critical(self, "Save Draft", str(exc))
            return False
        editor.mark_clean()
        self._refresh_ui_for_active_editor()
        self._set_status("Draft saved.")
        return True

    def _autosave_dirty_editors(self) -> None:
        failures: list[str] = []
        for editor in self._all_open_editors():
            if not editor.is_tracked() or not editor.is_dirty():
                continue
            try:
                fs.save_draft(editor.tracked_dir or "", editor.contents())
            except Exception as exc:
                failures.append(f"{editor.tab_title()}: {exc}")
                continue
            editor.mark_clean()
        if failures:
            self._set_status(f"Autosave failed for {len(failures)} tab(s).")
        elif any(editor.is_tracked() for editor in self._all_open_editors()):
            self._refresh_ui_for_active_editor()

    def _project_name_prompt(self, default_name: str) -> str | None:
        name, accepted = TextInputDialog.get_text(
            self,
            "Project Name",
            "Enter a project name (no slashes).",
            text=default_name,
            ok_text="Create",
        )
        if not accepted:
            return None
        if "/" in name or "\\" in name:
            MessageDialog.warning(self, "Project Name", "Use a project name without path separators.")
            return None
        return name

    # ------------------------------------------------------------------
    # Project actions
    # ------------------------------------------------------------------

    def on_create_project(self) -> None:
        parent_dir = self._choose_directory(
            "Choose parent folder for new project",
            directory=self._project_root or DEFAULT_PROJECTS_ROOT,
        )
        if not parent_dir:
            return
        name = self._project_name_prompt("session-workspace")
        if not name:
            return
        project_dir = Path(parent_dir) / name
        try:
            project_api.create_project(project_dir, name, description="")
        except Exception as exc:
            MessageDialog.critical(self, "Create Project", str(exc))
            return
        self._set_project_root(project_dir)
        self._set_status(f"Project created at {project_dir}")

    def on_open_project(self) -> None:
        project_dir = self._choose_directory(
            "Choose existing project folder",
            directory=self._project_root or DEFAULT_PROJECTS_ROOT,
        )
        if not project_dir:
            return
        project_file = Path(project_dir) / "project.json"
        if not project_file.exists():
            MessageDialog.warning(self, "Open Project", "Selected folder does not contain project.json.")
            return
        try:
            project_api.load_project(Path(project_dir))
        except Exception as exc:
            MessageDialog.critical(self, "Open Project", str(exc))
            return
        self._set_project_root(Path(project_dir))
        self._set_status(f"Project opened: {project_dir}")

    def on_close_project(self) -> None:
        for editor in list(self._all_open_editors()):
            if not editor.tracked_dir:
                continue
            if not self.workspace.close_editor(editor, self):
                return
        self._set_project_root(None)
        self._set_status("Project closed.")

    def on_create_workspace_file(self) -> None:
        if self._project_root is None:
            MessageDialog.warning(self, "Create Workspace File", "Open a project before creating a new workspace file.")
            return

        target_dir = self._workspace_target_dir_from_context()
        if not target_dir:
            MessageDialog.warning(
                self,
                "Create Workspace File",
                "Select a live root or target folder in the explorer first. New files stay in the project until you explicitly push them.",
            )
            return

        file_name, accepted = TextInputDialog.get_text(
            self,
            "New File Name",
            f"Enter the new file name for:\n{target_dir}",
            ok_text="Create",
        )
        if not accepted:
            return
        if "/" in file_name or "\\" in file_name:
            MessageDialog.warning(self, "New File Name", "Use a file name without path separators.")
            return

        live_target_path = str(Path(target_dir).expanduser() / file_name)
        try:
            tracked_dir = fs.create_file_in_project(self._project_root, live_target_path)
            project_api.add_tracked_file(self._project_root, Path(tracked_dir).name)
        except Exception as exc:
            MessageDialog.critical(self, "Create Workspace File", str(exc))
            return

        self._set_project_root(self._project_root)
        self._open_tracked_editor(tracked_dir)
        self._set_status(f"Created tracked workspace file for {file_name}")

    # ------------------------------------------------------------------
    # Explorer actions
    # ------------------------------------------------------------------

    def on_tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = str(data.get("type") or "").strip()
        if kind == "file":
            try:
                self._open_live_editor(str(data.get("path") or ""))
            except Exception as exc:
                MessageDialog.warning(self, "Open Live File", str(exc))
            return
        if kind == "tracked":
            try:
                self._open_tracked_editor(str(data.get("path") or ""))
            except Exception as exc:
                MessageDialog.warning(self, "Open Tracked File", str(exc))
            return
        if kind == "project":
            project_path = Path(str(data.get("path") or ""))
            try:
                project = project_api.load_project(project_path)
                description = str(project.get("description") or "").strip() or "(no description)"
                self.lbl_meta.setText(f"Project: {project_path}\n\n{description}")
            except Exception as exc:
                self.lbl_meta.setText(f"Project: {project_path}\n\nUnable to read project.json:\n{exc}")

    def on_import_clicked(self) -> None:
        live_path = self._selected_live_path_from_context()
        if not live_path:
            MessageDialog.warning(self, "Import Live File", "Select a live file in the explorer or open one in a tab first.")
            return

        target_project = self._project_root
        if target_project is None:
            selected = self._choose_directory(
                "Choose project folder (or a folder to create a new project)",
                directory=DEFAULT_PROJECTS_ROOT,
            )
            if not selected:
                return
            target_project = Path(selected)
            if not (target_project / "project.json").exists():
                should_create = MessageDialog.question(
                    self,
                    "Project Missing",
                    "No project.json exists in that folder.\nCreate a new project here?",
                    accept_text="Create",
                    reject_text="Cancel",
                )
                if not should_create:
                    return
                try:
                    project_api.create_project(target_project, target_project.name, description="")
                except Exception as exc:
                    MessageDialog.critical(self, "Create Project", str(exc))
                    return

        try:
            tracked_dir = fs.import_file_to_project(live_path, target_project)
            project_api.add_tracked_file(target_project, Path(tracked_dir).name)
        except Exception as exc:
            MessageDialog.critical(self, "Import Live File", str(exc))
            return

        self._set_project_root(target_project)
        self._open_tracked_editor(tracked_dir)
        self._set_status(f"Imported {Path(live_path).name} into project.")

    def on_remove_from_project(self) -> None:
        editor = self._current_editor()
        if editor is None or not editor.tracked_dir or self._project_root is None:
            MessageDialog.warning(self, "Remove from Project", "Open a tracked file in a project first.")
            return

        tracked_dir = str(editor.tracked_dir)
        tracked_key = self._tracked_key_for_dir(tracked_dir)
        should_remove = MessageDialog.question(
            self,
            "Remove from Project",
            (
                f"Remove {tracked_key} from this project?\n\n"
                "The live target will not be deleted.\n"
                "Workspace drafts, history, and backups will stay on disk."
            ),
            accept_text="Remove",
            reject_text="Cancel",
        )
        if not should_remove:
            return

        if not self._close_tracked_editors_for_path(tracked_dir):
            return

        try:
            project_api.remove_tracked_file(self._project_root, tracked_key)
        except Exception as exc:
            MessageDialog.critical(self, "Remove from Project", str(exc))
            return

        self._set_project_root(self._project_root)
        self._set_status(f"Removed {tracked_key} from the project.")

    def on_delete_from_system(self) -> None:
        editor = self._current_editor()
        if editor is None or not editor.tracked_dir:
            MessageDialog.warning(self, "Delete from System", "Open a tracked file first.")
            return

        if editor.is_dirty() and not self._save_editor(editor, notify=False):
            MessageDialog.critical(self, "Delete from System", "Failed to save the draft before delete.")
            return

        live_target = str(editor.live_path or "").strip()
        if not live_target:
            MessageDialog.warning(self, "Delete from System", "This tracked file does not have a live target path.")
            return

        confirmed = DangerCodeDialog.confirm(
            self,
            title="Delete from System",
            warning_html=(
                "<b>Warning:</b> this will remove the live file from the system.<br><br>"
                f"Live target:<br><code>{live_target}</code><br><br>"
                "Project drafts, history, and backups will stay in the workspace."
            ),
            confirm_text="Delete Live File",
        )
        if not confirmed:
            return

        if not self._run_live_install(
            lambda try_escalate=False: fs.delete_live_file(
                editor.tracked_dir or "",
                escalate_helper=str(ESCALATE_HELPER_PATH),
                try_escalate=try_escalate,
            ),
            success_title="Delete from System",
            prompt_title="Permission Required",
        ):
            return
        self._refresh_tracked_editor_metadata(editor)
        self._set_project_root(self._project_root)
        self._refresh_backups_list(editor)
        self._refresh_ui_for_active_editor()

    # ------------------------------------------------------------------
    # Active editor actions
    # ------------------------------------------------------------------

    def on_toggle_edit(self, checked: bool) -> None:
        editor = self._current_editor()
        if editor is None:
            self._set_action_checked(self.action_toggle_edit, False)
            return
        if not editor.is_tracked():
            editor.set_edit_mode(False)
            self._set_action_checked(self.action_toggle_edit, False)
            MessageDialog.warning(self, "Edit Mode", "Live system files are read-only. Import or open the tracked draft to edit.")
            return
        if not checked and editor.is_tracked() and editor.is_dirty():
            if not self._save_editor(editor, notify=False):
                self._set_action_checked(self.action_toggle_edit, True)
                MessageDialog.warning(self, "Edit Mode", "Unable to save the draft before leaving edit mode.")
                return
        editor.set_edit_mode(checked)
        self._refresh_ui_for_active_editor()

    def on_save_draft(self) -> None:
        editor = self._current_editor()
        if editor is None:
            MessageDialog.warning(self, "Save Draft", "Open a tracked file first.")
            return
        self._save_editor(editor, notify=True)

    def on_toggle_diff(self, checked: bool) -> None:
        editor = self._current_editor()
        if editor is None:
            self._set_action_checked(self.action_show_diff, False)
            return
        if not checked:
            editor.hide_diff()
            self._refresh_ui_for_active_editor()
            return

        left_text = ""
        right_text = editor.contents()
        if editor.live_path:
            try:
                left_text = str(fs.read_live_file(editor.live_path).get("contents", ""))
            except Exception:
                left_text = ""
        label = "draft" if editor.is_tracked() else "buffer"
        editor.set_diff_text(fs.diff_text(left_text, right_text, a_label="live", b_label=label))
        self._refresh_ui_for_active_editor()

    def on_create_backup(self) -> None:
        editor = self._current_editor()
        if editor is None or not editor.tracked_dir:
            MessageDialog.warning(self, "Create Backup", "Open a tracked file first.")
            return
        if editor.is_dirty() and not self._save_editor(editor, notify=False):
            MessageDialog.warning(self, "Create Backup", "Save failed before backup creation.")
            return
        try:
            backup_path = fs.create_backup(editor.tracked_dir)
        except Exception as exc:
            MessageDialog.critical(self, "Create Backup", str(exc))
            return
        self._refresh_backups_list(editor)
        self._set_status(f"Backup created: {Path(backup_path).name}")

    def on_restore_to_draft(self) -> None:
        editor = self._current_editor()
        backup = self._selected_backup()
        if editor is None or not editor.tracked_dir:
            MessageDialog.warning(self, "Restore Backup", "Open a tracked file first.")
            return
        if backup is None:
            MessageDialog.warning(self, "Restore Backup", "Select a backup first.")
            return
        try:
            result = fs.restore_backup_to_draft(editor.tracked_dir, str(backup.get("path") or ""))
        except Exception as exc:
            MessageDialog.critical(self, "Restore Backup", str(exc))
            return
        if not result.get("ok"):
            MessageDialog.critical(self, "Restore Backup", str(result.get("message") or "Restore failed."))
            return
        editor.replace_contents(fs.read_draft(editor.tracked_dir), editable=True)
        self._refresh_backups_list(editor)
        self._refresh_ui_for_active_editor()
        self._set_status(str(result.get("message") or "Backup restored."))

    def on_restore_to_live(self) -> None:
        editor = self._current_editor()
        backup = self._selected_backup()
        if editor is None or not editor.tracked_dir:
            MessageDialog.warning(self, "Restore to Live", "Open a tracked file first.")
            return
        if backup is None:
            MessageDialog.warning(self, "Restore to Live", "Select a backup first.")
            return
        if not self._run_live_install(
            lambda try_escalate=False: fs.restore_backup_to_live(
                editor.tracked_dir or "",
                str(backup.get("path") or ""),
                escalate_helper=str(ESCALATE_HELPER_PATH),
                try_escalate=try_escalate,
            ),
            success_title="Restore to Live",
            prompt_title="Permission Required",
        ):
            return
        self._refresh_tracked_editor_metadata(editor)
        self._refresh_backups_list(editor)
        self._refresh_ui_for_active_editor()

    def on_push(self) -> None:
        editor = self._current_editor()
        if editor is None or not editor.tracked_dir:
            MessageDialog.warning(self, "Push Draft", "Open a tracked file first.")
            return
        if editor.is_dirty() and not self._save_editor(editor, notify=False):
            MessageDialog.critical(self, "Push Draft", "Failed to save the draft before push.")
            return
        if not self._run_live_install(
            lambda try_escalate=False: fs.push_draft_to_live(
                editor.tracked_dir or "",
                escalate_helper=str(ESCALATE_HELPER_PATH),
                try_escalate=try_escalate,
            ),
            success_title="Push Draft",
            prompt_title="Permission Required",
        ):
            return
        self._refresh_tracked_editor_metadata(editor)
        self._refresh_backups_list(editor)
        self._refresh_ui_for_active_editor()

    def _run_live_install(self, runner, *, success_title: str, prompt_title: str) -> bool:
        try:
            result = runner(False)
        except Exception as exc:
            MessageDialog.critical(self, success_title, str(exc))
            return False
        if result.get("ok"):
            self._set_status(str(result.get("message") or success_title))
            return True
        if result.get("needs_elevation"):
            run_helper = MessageDialog.question(
                self,
                prompt_title,
                f"{result.get('message')}\n\nRun the privileged helper now?",
                accept_text="Run Helper",
                reject_text="Cancel",
            )
            if not run_helper:
                return False
            try:
                elevated_result = runner(True)
            except Exception as exc:
                MessageDialog.critical(self, success_title, str(exc))
                return False
            if elevated_result.get("ok"):
                self._set_status(str(elevated_result.get("message") or success_title))
                return True
            MessageDialog.critical(self, success_title, str(elevated_result.get("message") or "Operation failed."))
            return False
        MessageDialog.critical(self, success_title, str(result.get("message") or "Operation failed."))
        return False

    # ------------------------------------------------------------------
    # Close handling
    # ------------------------------------------------------------------

    def confirm_close_editor(self, editor: QWidget, _parent: QWidget | None = None) -> bool:
        if not isinstance(editor, SessionEditorPane):
            return True
        if editor.is_tracked() and editor.is_dirty():
            if self._save_editor(editor, notify=False):
                return True
            return MessageDialog.question(
                self,
                "Discard Draft Changes",
                f"Unable to save {editor.tab_title()}.\nDiscard unsaved changes and close it?",
                accept_text="Discard",
                reject_text="Cancel",
            )
        if not editor.is_tracked() and editor.is_dirty():
            return MessageDialog.question(
                self,
                "Discard Scratch Changes",
                f"Discard scratch edits in {editor.tab_title()}?",
                accept_text="Discard",
                reject_text="Cancel",
            )
        return True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self.workspace.request_close_all(self):
            event.ignore()
            return
        super().closeEvent(event)
