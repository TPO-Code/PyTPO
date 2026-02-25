import os
import sys
from pathlib import Path
from typing import Set

from PySide6.QtCore import (
    Qt,
    QTimer,
    QModelIndex,
    QSignalBlocker,
    QSortFilterProxyModel,
    QObject,
    Signal,
    QRunnable,
    QThreadPool,
    QUrl,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeView,
    QLineEdit,
    QSplitter,
    QFileSystemModel,
    QLabel,
    QToolButton,
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
try:
    from .markdown_viewer_widget import MarkdownViewerWidget, MDHeadFlags
except Exception:
    # Allow running this file directly for local testing.
    from markdown_viewer_widget import MarkdownViewerWidget, MDHeadFlags


def is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def is_binary_file(path: Path, sample_size: int = 4096) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(sample_size)
        return b"\x00" in chunk
    except Exception:
        return True


# ---------------------------------------------------------
# Search worker (independent from QFileSystemModel laziness)
# ---------------------------------------------------------
class SearchSignals(QObject):
    finished = Signal(int, set, set)  # request_id, matched_files, matched_dirs
    failed = Signal(int, str)


class SearchWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        root: Path,
        term: str,
        max_bytes: int = 2 * 1024 * 1024,
        skip_dirs: Set[str] | None = None,
    ):
        super().__init__()
        self.request_id = request_id
        self.root = root
        self.term = term.lower().strip()
        self.max_bytes = max_bytes
        self.skip_dirs = skip_dirs or {".git", ".venv", "__pycache__", "node_modules", ".tide"}
        self.signals = SearchSignals()

    def run(self):
        matched_files: Set[str] = set()
        matched_dirs: Set[str] = set()

        try:
            if not self.term:
                self.signals.finished.emit(self.request_id, matched_files, matched_dirs)
                return

            for dirpath, dirnames, filenames in os.walk(self.root):
                # In-place prune for performance and correctness
                dirnames[:] = [d for d in dirnames if d not in self.skip_dirs]

                dir_path = Path(dirpath)
                dir_path_str = str(dir_path.resolve())

                # Directory name match
                if self.term in dir_path.name.lower():
                    matched_dirs.add(dir_path_str)

                for name in filenames:
                    if not str(name).lower().endswith(".md"):
                        continue
                    file_path = dir_path / name
                    file_path_str = str(file_path.resolve())

                    # Filename match
                    if self.term in name.lower():
                        matched_files.add(file_path_str)
                        # include parents so tree path stays visible
                        p = file_path.parent.resolve()
                        while is_within_root(p, self.root):
                            matched_dirs.add(str(p))
                            if p == self.root:
                                break
                            p = p.parent
                        continue

                    # Content match
                    try:
                        st = file_path.stat()
                        if st.st_size > self.max_bytes:
                            continue
                        if is_binary_file(file_path):
                            continue

                        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                        if self.term in content.lower():
                            matched_files.add(file_path_str)
                            p = file_path.parent.resolve()
                            while is_within_root(p, self.root):
                                matched_dirs.add(str(p))
                                if p == self.root:
                                    break
                                p = p.parent
                    except Exception:
                        # Skip unreadable/problem files
                        continue

            self.signals.finished.emit(self.request_id, matched_files, matched_dirs)
        except Exception as e:
            self.signals.failed.emit(self.request_id, str(e))


# ---------------------------------------------------------
# Proxy model: browsing + strict root + match membership
# ---------------------------------------------------------
class SearchAwareProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path: Path | None = None
        self.search_term: str = ""
        self.matched_files: Set[str] = set()
        self.matched_dirs: Set[str] = set()
        self.skip_dirs: Set[str] = {".git", ".venv", "__pycache__", "node_modules", ".tide"}
        self._markdown_dir_cache: dict[str, bool] = {}
        self.setRecursiveFilteringEnabled(True)

    def set_root_path(self, root: Path):
        self.root_path = root.resolve()
        self._markdown_dir_cache.clear()

    def set_search_term(self, text: str):
        self.search_term = (text or "").strip().lower()
        self.invalidateFilter()

    def set_matches(self, files: Set[str], dirs: Set[str]):
        self.matched_files = files
        self.matched_dirs = dirs
        self.invalidateFilter()

    def clear_matches(self):
        self.matched_files.clear()
        self.matched_dirs.clear()
        self.invalidateFilter()

    @staticmethod
    def _is_markdown_file(path: Path) -> bool:
        return str(path.suffix).lower() == ".md"

    def _directory_contains_markdown(self, path: Path) -> bool:
        key = str(path)
        cached = self._markdown_dir_cache.get(key)
        if cached is not None:
            return cached

        try:
            child_dirs: list[Path] = []
            for entry in os.scandir(path):
                name = str(entry.name or "")
                if name in self.skip_dirs:
                    continue

                if entry.is_file(follow_symlinks=False) and name.lower().endswith(".md"):
                    self._markdown_dir_cache[key] = True
                    return True

                if entry.is_dir(follow_symlinks=False):
                    child_dirs.append(Path(entry.path).resolve())

            for child in child_dirs:
                if self._directory_contains_markdown(child):
                    self._markdown_dir_cache[key] = True
                    return True
        except Exception:
            self._markdown_dir_cache[key] = False
            return False

        self._markdown_dir_cache[key] = False
        return False

    def _source_path(self, index: QModelIndex) -> Path:
        src = self.sourceModel()
        return Path(src.filePath(index)).resolve()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src = self.sourceModel()
        idx = src.index(source_row, 0, source_parent)
        if not idx.isValid():
            return False

        path = self._source_path(idx)
        p = str(path)
        is_dir = src.isDir(idx)

        # Strict lock to root
        if self.root_path is not None and not is_within_root(path, self.root_path):
            return False

        if is_dir:
            if not self._directory_contains_markdown(path):
                return False
        else:
            if not self._is_markdown_file(path):
                return False

        # No search term: show all under root
        if not self.search_term:
            return True

        if is_dir:
            # show matched dirs (and dirs that are ancestors of matched dirs/files)
            if p in self.matched_dirs:
                return True

            # ancestor check so root path to result remains visible
            for d in self.matched_dirs:
                if d.startswith(p + os.sep) or d == p:
                    return True
            for f in self.matched_files:
                if f.startswith(p + os.sep):
                    return True
            return False

        # file row
        return p in self.matched_files


# ---------------------------------------------------------
# Main viewer
# ---------------------------------------------------------
class DocumentationViewer(QWidget):
    def __init__(self, doc_root_path: str, parent=None):
        super().__init__(parent)

        self.doc_root = Path(doc_root_path).resolve()
        self.doc_root.mkdir(parents=True, exist_ok=True)
        self.current_file: Path | None = None
        self._nav_history: list[tuple[Path, str]] = []
        self._nav_index: int = -1
        self._navigating_history: bool = False

        self.setWindowTitle("Documentation Viewer")
        self.resize(1100, 680)

        self.thread_pool = QThreadPool.globalInstance()
        self.request_id = 0

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.perform_search)

        self.setup_ui()
        self.setup_models()
        self.set_tree_root()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.splitter)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        nav_row = QHBoxLayout()
        self.btn_back = QToolButton()
        self.btn_back.setText("←")
        self.btn_back.setToolTip("Back")
        self.btn_back.clicked.connect(self.go_back)

        self.btn_forward = QToolButton()
        self.btn_forward.setText("→")
        self.btn_forward.setToolTip("Forward")
        self.btn_forward.clicked.connect(self.go_forward)

        nav_row.addWidget(self.btn_back)
        nav_row.addWidget(self.btn_forward)
        nav_row.addStretch(1)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search filenames or content...")
        self.search_bar.setClearButtonEnabled(True)

        self.tree_view = QTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setUniformRowHeights(True)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: gray; font-size: 11px;")

        left_layout.addLayout(nav_row)
        left_layout.addWidget(self.search_bar)
        left_layout.addWidget(self.tree_view, 1)
        left_layout.addWidget(self.status_label)

        self.content_viewer = MarkdownViewerWidget()
        self.content_viewer.setHeadFlags(MDHeadFlags.search)
        self.content_viewer.linkActivated.connect(self.on_content_link_activated)

        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(self.content_viewer)
        self.splitter.setStretchFactor(0, 35)
        self.splitter.setStretchFactor(1, 65)
        self._update_navigation_buttons()

    def setup_models(self):
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setReadOnly(True)
        self.source_root_index = self.fs_model.setRootPath(str(self.doc_root))

        self.proxy_model = SearchAwareProxyModel(self)
        self.proxy_model.setSourceModel(self.fs_model)
        self.proxy_model.set_root_path(self.doc_root)

        self.tree_view.setModel(self.proxy_model)

        self.search_bar.textChanged.connect(self.on_search_text_changed)
        self.tree_view.selectionModel().currentChanged.connect(self.on_file_selected)

        # Keep root anchored after filter updates
        self.proxy_model.modelReset.connect(self.set_tree_root)
        self.proxy_model.layoutChanged.connect(self.set_tree_root)

        for i in range(1, 4):
            self.tree_view.hideColumn(i)

    def set_tree_root(self):
        proxy_root = self.proxy_model.mapFromSource(self.source_root_index)
        if proxy_root.isValid():
            self.tree_view.setRootIndex(proxy_root)

    def on_search_text_changed(self, _text):
        self.search_timer.start()

    def perform_search(self):
        text = self.search_bar.text().strip()
        self.request_id += 1
        rid = self.request_id

        if not text:
            self.proxy_model.set_search_term("")
            self.proxy_model.clear_matches()
            self.set_tree_root()
            self.tree_view.collapseAll()
            self.status_label.setText("Ready")
            return

        self.status_label.setText(f"Searching for '{text}'...")

        self.proxy_model.set_search_term(text)
        worker = SearchWorker(
            request_id=rid,
            root=self.doc_root,
            term=text,
            skip_dirs={".git", ".venv", "__pycache__", "node_modules", ".tide"},
        )
        worker.signals.finished.connect(self.on_search_finished)
        worker.signals.failed.connect(self.on_search_failed)
        self.thread_pool.start(worker)

    def on_search_finished(self, rid: int, matched_files: set, matched_dirs: set):
        # Ignore stale results from older searches
        if rid != self.request_id:
            return

        self.proxy_model.set_matches(matched_files, matched_dirs)
        self.set_tree_root()
        self.tree_view.expandAll()
        self.status_label.setText(
            f"Found {len(matched_files)} file match(es) in '{self.search_bar.text().strip()}'"
        )

    def on_search_failed(self, rid: int, message: str):
        if rid != self.request_id:
            return
        self.status_label.setText(f"Search failed: {message}")

    def on_file_selected(self, current: QModelIndex, _previous: QModelIndex):
        src_index = self.proxy_model.mapToSource(current)
        if not src_index.isValid():
            return

        file_path = Path(self.fs_model.filePath(src_index)).resolve()

        if self.fs_model.isDir(src_index):
            return

        self._navigate_to_file(
            file_path,
            push_history=not self._navigating_history,
            select_tree=False,
        )

    def _display_markdown_file(self, file_path: Path, *, anchor: str = "") -> None:
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            base_url = QUrl.fromLocalFile(str(file_path.parent))
            base_text = base_url.toString()
            if base_text and not base_text.endswith("/"):
                base_url = QUrl(base_text + "/")
            self.content_viewer.setMarkdown(content, base_url=base_url)
            self.current_file = file_path
            if anchor:
                self.content_viewer.scroll_to_anchor(anchor)
        except Exception as e:
            self.content_viewer.setMarkdown(f"Could not read file:\n{e}")
            return

        # Highlight search term safely
        try:
            self.highlight_term(self.proxy_model.search_term)
        except Exception as e:
            self.status_label.setText(f"Highlight warning: {e}")

    def highlight_term(self, term: str):
        if not term:
            return
        self.content_viewer.find(term)

    def _select_file_in_tree(self, file_path: Path) -> None:
        source_index = self.fs_model.index(str(file_path))
        if not source_index.isValid():
            return

        proxy_index = self.proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid() and self.proxy_model.search_term:
            self.search_bar.blockSignals(True)
            self.search_bar.clear()
            self.search_bar.blockSignals(False)
            self.proxy_model.set_search_term("")
            self.proxy_model.clear_matches()
            self.set_tree_root()
            proxy_index = self.proxy_model.mapFromSource(source_index)

        if not proxy_index.isValid():
            return

        parent = proxy_index.parent()
        while parent.isValid():
            self.tree_view.expand(parent)
            parent = parent.parent()
        selection_model = self.tree_view.selectionModel()
        if selection_model is not None:
            blocker = QSignalBlocker(selection_model)
            self.tree_view.setCurrentIndex(proxy_index)
            del blocker
        else:
            self.tree_view.setCurrentIndex(proxy_index)
        self.tree_view.scrollTo(proxy_index)

    def on_content_link_activated(self, url: QUrl) -> None:
        if not isinstance(url, QUrl) or not url.isValid():
            return

        target_path = ""
        if url.scheme() == "file":
            target_path = url.toLocalFile()
        elif not url.scheme():
            if not url.path():
                anchor = str(url.fragment() or "").strip()
                if anchor and isinstance(self.current_file, Path):
                    self.content_viewer.scroll_to_anchor(anchor)
                    self._push_history_entry(self.current_file, anchor)
                return
            base_dir = self.current_file.parent if isinstance(self.current_file, Path) else self.doc_root
            target_path = str((base_dir / url.path()).resolve())
        else:
            # Other schemes are intentionally ignored in embedded docs.
            return

        if not target_path:
            return

        anchor = str(url.fragment() or "").strip()
        self._navigate_to_file(Path(target_path), anchor=anchor, push_history=True, select_tree=True)

    def _update_navigation_buttons(self) -> None:
        self.btn_back.setEnabled(self._nav_index > 0)
        self.btn_forward.setEnabled(0 <= self._nav_index < len(self._nav_history) - 1)

    def _push_history_entry(self, file_path: Path, anchor: str = "") -> None:
        target = (file_path.resolve(), str(anchor or "").strip())
        if 0 <= self._nav_index < len(self._nav_history):
            if self._nav_history[self._nav_index] == target:
                self._update_navigation_buttons()
                return

        if self._nav_index < len(self._nav_history) - 1:
            self._nav_history = self._nav_history[: self._nav_index + 1]
        self._nav_history.append(target)
        self._nav_index = len(self._nav_history) - 1
        self._update_navigation_buttons()

    def _navigate_to_file(
        self,
        file_path: Path,
        *,
        anchor: str = "",
        push_history: bool = True,
        select_tree: bool = True,
    ) -> bool:
        target = file_path.resolve()
        if target.is_dir():
            target = (target / "README.md").resolve()
        if not target.exists():
            self.status_label.setText(f"Link target not found: {target}")
            return False
        if not is_within_root(target, self.doc_root):
            self.status_label.setText("Blocked link outside docs root.")
            return False

        if select_tree:
            self._select_file_in_tree(target)

        self._display_markdown_file(target, anchor=anchor)
        if push_history:
            self._push_history_entry(target, anchor)
        return True

    def go_back(self) -> None:
        if self._nav_index <= 0:
            self._update_navigation_buttons()
            return
        self._nav_index -= 1
        self._update_navigation_buttons()
        target, anchor = self._nav_history[self._nav_index]
        self._navigating_history = True
        try:
            self._navigate_to_file(target, anchor=anchor, push_history=False, select_tree=True)
        finally:
            self._navigating_history = False

    def go_forward(self) -> None:
        if self._nav_index >= len(self._nav_history) - 1:
            self._update_navigation_buttons()
            return
        self._nav_index += 1
        self._update_navigation_buttons()
        target, anchor = self._nav_history[self._nav_index]
        self._navigating_history = True
        try:
            self._navigate_to_file(target, anchor=anchor, push_history=False, select_tree=True)
        finally:
            self._navigating_history = False


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Point this to your docs root
    root = "./docs"

    w = DocumentationViewer(root)
    w.show()
    sys.exit(app.exec())
