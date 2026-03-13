from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytpo.ui.codex_session_store import (
    CodexSessionRecord,
    delete_codex_sessions,
    list_codex_sessions,
    session_preview_text,
)
from TPOPyside.dialogs.custom_dialog import DialogWindow


class CodexSessionsDialog(DialogWindow):
    def __init__(
        self,
        *,
        project_dir: Path | None,
        active_session_id: str | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self._project_dir = project_dir
        self._active_session_id = str(active_session_id or "").strip()
        self._sessions: list[CodexSessionRecord] = []
        self._sessions_by_id: dict[str, CodexSessionRecord] = {}
        self.selected_session_id = ""

        self.setWindowTitle("Manage Codex Sessions")
        self.resize(1080, 680)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Scope"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Current Project", "project")
        self.scope_combo.addItem("All Projects", "all")
        if self._project_dir is None:
            self.scope_combo.setCurrentIndex(1)
            self.scope_combo.setEnabled(False)
        controls.addWidget(self.scope_combo, 0)
        controls.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by prompt, project, model, or session id")
        controls.addWidget(self.search_edit, 1)
        self.refresh_btn = QPushButton("Refresh")
        controls.addWidget(self.refresh_btn)
        root.addLayout(controls)

        self.summary_label = QLabel("")
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.summary_label)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Prompt", "Updated", "Model", "Session ID"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAllColumnsShowFocus(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        root.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.attach_btn = QPushButton("Attach")
        self.delete_btn = QPushButton("Delete Selected")
        self.close_btn = QPushButton("Close")
        self.attach_btn.setDefault(True)
        actions.addWidget(self.attach_btn)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

        self.scope_combo.currentIndexChanged.connect(self._reload_sessions)
        self.search_edit.textChanged.connect(self._rebuild_tree)
        self.refresh_btn.clicked.connect(self._reload_sessions)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.attach_btn.clicked.connect(self._attach_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.close_btn.clicked.connect(self.reject)

        self._reload_sessions()

    def _scoped_project_dir(self) -> Path | None:
        mode = str(self.scope_combo.currentData() or "all").strip().lower()
        if mode == "project":
            return self._project_dir
        return None

    def _group_label(self, cwd: str) -> str:
        raw = str(cwd or "").strip()
        if not raw:
            return "Unknown Project"
        try:
            project_path = Path(raw).expanduser().resolve(strict=False)
        except Exception:
            project_path = Path(raw).expanduser()
        if self._project_dir is not None:
            try:
                if project_path == self._project_dir:
                    return f"{project_path} (current project)"
            except Exception:
                pass
        return str(project_path)

    def _session_matches_search(self, session: CodexSessionRecord) -> bool:
        needle = str(self.search_edit.text() or "").strip().casefold()
        if not needle:
            return True
        haystack = "\n".join(
            [
                str(session.first_user_message or ""),
                str(session.cwd or ""),
                str(session.model or ""),
                str(session.session_id or ""),
            ]
        ).casefold()
        return needle in haystack

    def _selected_sessions(self) -> list[CodexSessionRecord]:
        selected: list[CodexSessionRecord] = []
        seen_ids: set[str] = set()
        for item in self.tree.selectedItems():
            session_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip()
            if not session_id:
                continue
            session = self._sessions_by_id.get(session_id)
            if session is None or session.session_id in seen_ids:
                continue
            seen_ids.add(session.session_id)
            selected.append(session)
        return selected

    def _update_summary_label(self, visible_sessions: list[CodexSessionRecord]) -> None:
        scope_label = "all local sessions" if self._scoped_project_dir() is None else "current project sessions"
        self.summary_label.setText(f"Showing {len(visible_sessions)} {scope_label}.")

    def _rebuild_tree(self) -> None:
        selected_ids = {session.session_id for session in self._selected_sessions()}
        self.tree.clear()
        visible = [session for session in self._sessions if self._session_matches_search(session)]
        self._update_summary_label(visible)

        grouped: dict[str, list[CodexSessionRecord]] = defaultdict(list)
        for session in visible:
            grouped[self._group_label(session.cwd)].append(session)

        for group_label in sorted(grouped.keys(), key=str.casefold):
            sessions = grouped[group_label]
            group_item = QTreeWidgetItem([f"{group_label} ({len(sessions)})", "", "", ""])
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(group_item)
            for session in sessions:
                summary = session_preview_text(
                    session.first_user_message or f"Session {session.session_id[:8]}...",
                    max_chars=78,
                )
                if session.session_id == self._active_session_id:
                    summary = f"{summary} [active]"
                child = QTreeWidgetItem(
                    [
                        summary,
                        session.updated_at.strftime("%Y-%m-%d %H:%M"),
                        str(session.model or "") or "Default",
                        session.session_id,
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, session.session_id)
                group_item.addChild(child)
                if session.session_id in selected_ids:
                    child.setSelected(True)
            group_item.setExpanded(True)

        self._update_buttons()

    def _reload_sessions(self) -> None:
        self._sessions = list_codex_sessions(limit=None, project_dir=self._scoped_project_dir())
        self._sessions_by_id = {session.session_id: session for session in self._sessions}
        self._rebuild_tree()

    def _update_buttons(self) -> None:
        has_selection = bool(self._selected_sessions())
        self.attach_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip():
            self._attach_selected()

    def _attach_selected(self) -> None:
        sessions = self._selected_sessions()
        if not sessions:
            return
        self.selected_session_id = sessions[0].session_id
        self.accept()

    def _delete_selected(self) -> None:
        sessions = self._selected_sessions()
        if not sessions:
            return
        count = len(sessions)
        active_selected = any(session.session_id == self._active_session_id for session in sessions)
        detail = "Delete the selected Codex session log files?"
        if active_selected:
            detail = "Delete the selected Codex session log files? The active attached session is included."
        answer = QMessageBox.question(
            self,
            "Delete Codex Sessions",
            f"Delete {count} selected session(s)?\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        _deleted, failures = delete_codex_sessions([session.log_path for session in sessions])
        if failures:
            QMessageBox.warning(
                self,
                "Delete Codex Sessions",
                "Some sessions could not be deleted:\n\n" + "\n".join(failures[:12]),
            )
        self._reload_sessions()
