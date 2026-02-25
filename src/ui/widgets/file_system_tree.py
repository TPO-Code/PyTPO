
import json
import os
import shutil
from typing import Callable, Optional, Any, Dict, List, cast

from PySide6.QtCore import (
    Qt, QAbstractItemModel, QModelIndex, QMimeData, QPoint, Signal, QObject
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QTreeView, QAbstractItemView, QStyle,
    QWidget
)

from src.ui.icons.file_icon_provider import FileIconProvider


class _FsNode:
    def __init__(
            self,
            path: str,
            name: str,
            is_dir: bool,
            parent: Optional["_FsNode"] = None,
    ):
        self.id = path
        self.path = path
        self.name = name
        self.is_dir = is_dir
        self.parent = parent
        self.children: List["_FsNode"] = []
        self.children_loaded = False

    def child_at(self, row: int) -> Optional["_FsNode"]:
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def row_of_child(self, child: "_FsNode") -> int:
        try:
            return self.children.index(child)
        except ValueError:
            return -1


class FileSystemTreeModel(QAbstractItemModel):
    MIME_TYPE = "application/x-pytpo-fs-node-path"

    filesystemError = Signal(str, str)   # title, message
    filesystemMoved = Signal(str, str)   # old_path, new_path

    def __init__(
            self,
            root_path: str,
            exclude_dir_predicate: Optional[Callable[[str], bool]] = None,
            exclude_path_predicate: Optional[Callable[[str, bool], bool]] = None,
            parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._exclude_dir_predicate = exclude_dir_predicate
        self._exclude_path_predicate = exclude_path_predicate
        self._include_excluded = False
        self._root_path = self._canonical(root_path)
        self._icon_provider = FileIconProvider()
        self._root_node = _FsNode(self._root_path, os.path.basename(self._root_path) or self._root_path, True, None)
        self._root_node.children_loaded = False
        self._path_map: Dict[str, _FsNode] = {self._root_path: self._root_node}
        self._git_tint_enabled = False
        self._git_colors: Dict[str, str] = {
            "clean": "#7fbf7f",
            "dirty": "#e69f6b",
            "untracked": "#c8c8c8",
        }
        self._git_file_states: Dict[str, str] = {}
        self._git_folder_states: Dict[str, str] = {}
        self.refresh_tree(include_excluded=False)

    # ---------- Public API ----------

    def root_path(self) -> str:
        return self._root_path

    def set_root_path(self, root_path: str):
        self._root_path = self._canonical(root_path)
        self.refresh_tree(include_excluded=False)

    def refresh_tree(self, include_excluded: bool = False):
        self._include_excluded = bool(include_excluded)
        self.beginResetModel()
        self._root_node = _FsNode(
            self._root_path,
            os.path.basename(self._root_path) or self._root_path,
            True,
            None,
            )
        self._path_map = {self._root_path: self._root_node}
        # During reset we must not emit insert/remove row notifications.
        children = self._scan_children(self._root_path)
        for child in children:
            child.parent = self._root_node
            self._root_node.children.append(child)
            self._path_map[child.path] = child
        self._root_node.children_loaded = True
        self.endResetModel()

    def set_git_tinting(self, *, enabled: bool, colors: Dict[str, str]):
        self._git_tint_enabled = bool(enabled)
        merged = dict(self._git_colors)
        for key in ("clean", "dirty", "untracked"):
            val = str(colors.get(key, merged.get(key, "")) or "").strip()
            if val:
                merged[key] = val
        self._git_colors = merged
        self._emit_foreground_update()

    def set_git_status_maps(self, *, file_states: Dict[str, str], folder_states: Dict[str, str]):
        self._git_file_states = {self._canonical(path): str(state) for path, state in file_states.items()}
        self._git_folder_states = {self._canonical(path): str(state) for path, state in folder_states.items()}
        self._emit_foreground_update()

    def refresh_subtree(self, path: str, include_excluded: Optional[bool] = None):
        target = self._canonical(path)
        if include_excluded is not None:
            self._include_excluded = bool(include_excluded)

        node = self._path_map.get(target)
        if node is None:
            self.refresh_tree(include_excluded=self._include_excluded)
            return
        if not node.is_dir:
            node = node.parent if node.parent is not None else self._root_node
        if node is None:
            self.refresh_tree(include_excluded=self._include_excluded)
            return

        if node == self._root_node:
            self.refresh_tree(include_excluded=self._include_excluded)
            return

        parent_index = self.index_from_node(node.parent)
        row = node.parent.row_of_child(node) if node.parent else 0
        if row < 0:
            self.refresh_tree(include_excluded=self._include_excluded)
            return

        self.beginRemoveRows(parent_index, row, row)
        self._unregister_node_recursive(node)
        removed = node.parent.children.pop(row)
        self.endRemoveRows()

        replacement = _FsNode(removed.path, removed.name, removed.is_dir, removed.parent)
        self.beginInsertRows(parent_index, row, row)
        node.parent.children.insert(row, replacement)
        self._path_map[replacement.path] = replacement
        self.endInsertRows()

    def ensure_path_visible(self, path: str) -> bool:
        target = self._canonical(path)
        if target == self._root_path:
            return True
        if not self._is_within_root(target):
            return False

        rel = os.path.relpath(target, self._root_path)
        if rel in (".", ""):
            return True
        parts = [p for p in rel.split(os.sep) if p]

        node = self._root_node
        current = self._root_path
        for part in parts:
            if not node.children_loaded:
                idx = self.index_from_node(node)
                self.fetchMore(idx)
            current = self._canonical(os.path.join(current, part))
            child = self._path_map.get(current)
            if child is None:
                return False
            node = child
        return True

    def index_from_path(self, path: str) -> QModelIndex:
        return self.index_from_node(self._path_map.get(self._canonical(path)))

    def path_from_index(self, index: QModelIndex) -> Optional[str]:
        if not index.isValid():
            return None
        node = cast(_FsNode, index.internalPointer())
        return node.path

    def metadata_from_index(self, index: QModelIndex) -> Dict[str, Any]:
        if not index.isValid():
            return {}
        node = cast(_FsNode, index.internalPointer())
        return self._metadata_for_path(node.path, node.is_dir)

    def is_dir_path(self, path: str) -> bool:
        node = self._path_map.get(self._canonical(path))
        if node:
            return bool(node.is_dir)
        return os.path.isdir(path)

    # ---------- QAbstractItemModel ----------

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = cast(_FsNode, parent.internalPointer()) if parent.isValid() else self._root_node
        child_node = parent_node.child_at(row)
        if child_node is None:
            return QModelIndex()
        return self.createIndex(row, column, child_node)

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node = cast(_FsNode, index.internalPointer())
        parent_node = node.parent
        if parent_node is None or parent_node == self._root_node:
            return QModelIndex()
        if parent_node.parent is None:
            return QModelIndex()
        row = parent_node.parent.row_of_child(parent_node)
        if row < 0:
            return QModelIndex()
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        node = cast(_FsNode, parent.internalPointer()) if parent.isValid() else self._root_node
        return len(node.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        node = cast(_FsNode, index.internalPointer())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return node.name
        if role == Qt.ItemDataRole.DecorationRole:
            style = QApplication.style()
            if node.is_dir:
                return style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            icon = self._icon_provider.icon_for_file_name(node.name)
            if icon is not None:
                return icon
            return style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        if role == Qt.ItemDataRole.UserRole:
            return self._metadata_for_path(node.path, node.is_dir)
        if role == Qt.ItemDataRole.ForegroundRole:
            if not self._git_tint_enabled:
                return None
            state = self._git_folder_states.get(node.path) if node.is_dir else self._git_file_states.get(node.path)
            if not state:
                return None
            color_text = str(self._git_colors.get(state, "") or "").strip()
            color = QColor(color_text)
            if not color.isValid():
                return None
            return QBrush(color)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return "Project"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
        node = cast(_FsNode, index.internalPointer())
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if node.is_dir:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return True
        node = cast(_FsNode, parent.internalPointer())
        return bool(node.is_dir)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = cast(_FsNode, parent.internalPointer()) if parent.isValid() else self._root_node
        if not node.is_dir:
            return False
        if node.children_loaded:
            return False
        return os.path.isdir(node.path)

    def fetchMore(self, parent: QModelIndex):
        node = cast(_FsNode, parent.internalPointer()) if parent.isValid() else self._root_node
        if not node.is_dir:
            return
        if node.children_loaded:
            return
        self._reload_node_children(node)

    # ---------- Drag/Drop ----------

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> List[str]:
        return [self.MIME_TYPE]

    def mimeData(self, indexes: List[QModelIndex]) -> QMimeData:
        mime_data = QMimeData()
        if not indexes:
            return mime_data
        raw_paths: list[str] = []
        seen: set[str] = set()
        for index in indexes:
            if not index.isValid() or index.column() != 0:
                continue
            node = cast(_FsNode, index.internalPointer())
            path = self._canonical(node.path)
            if path in seen:
                continue
            seen.add(path)
            raw_paths.append(path)
        src_paths = self._filter_nested_paths(raw_paths)
        if not src_paths:
            return mime_data
        payload = json.dumps(src_paths).encode("utf-8")
        mime_data.setData(self.MIME_TYPE, payload)
        return mime_data

    def canDropMimeData(
            self,
            data: QMimeData,
            action: Qt.DropAction,
            row: int,
            column: int,
            parent: QModelIndex,
    ) -> bool:
        if action != Qt.DropAction.MoveAction or not data.hasFormat(self.MIME_TYPE):
            return False
        src_paths = self._decode_mime_paths(data)
        if not src_paths:
            return False

        dest_dir = self._destination_dir_from_drop(parent)
        if not dest_dir or not os.path.isdir(dest_dir):
            return False

        dest_dir = self._canonical(dest_dir)
        src_set = {self._canonical(path) for path in src_paths}
        planned_destinations: set[str] = set()
        for src_path in src_paths:
            src_path = self._canonical(src_path)
            if src_path == self._root_path:
                return False
            if not os.path.exists(src_path):
                return False

            if dest_dir == src_path:
                return False
            if os.path.isdir(src_path) and self._is_within(src_path, dest_dir):
                return False

            dest_path = self._canonical(os.path.join(dest_dir, os.path.basename(src_path)))
            if dest_path == src_path:
                return False
            if os.path.exists(dest_path) and dest_path not in src_set:
                return False
            if dest_path in planned_destinations:
                return False
            planned_destinations.add(dest_path)
        return True

    def dropMimeData(
            self,
            data: QMimeData,
            action: Qt.DropAction,
            row: int,
            column: int,
            parent: QModelIndex,
    ) -> bool:
        if not self.canDropMimeData(data, action, row, column, parent):
            if data.hasFormat(self.MIME_TYPE):
                self.filesystemError.emit("Move Failed", "Invalid move target.")
            return False

        src_paths = self._decode_mime_paths(data)
        if not src_paths:
            self.filesystemError.emit("Move Failed", "No source path provided.")
            return False

        dest_dir = self._destination_dir_from_drop(parent)
        if not dest_dir:
            self.filesystemError.emit("Move Failed", "Invalid destination directory.")
            return False

        dest_dir = self._canonical(dest_dir)
        moved_pairs: list[tuple[str, str]] = []
        for src_path in src_paths:
            src_path = self._canonical(src_path)
            dest_path = self._canonical(os.path.join(dest_dir, os.path.basename(src_path)))
            try:
                shutil.move(src_path, dest_path)
            except Exception as exc:
                self.filesystemError.emit("Move Failed", f"Could not move path:\n{exc}")
                break
            moved_pairs.append((src_path, dest_path))

        self.refresh_tree(include_excluded=self._include_excluded)
        for src_path, dest_path in moved_pairs:
            self.filesystemMoved.emit(src_path, dest_path)
        if not moved_pairs:
            return False
        return True

    # ---------- Internals ----------

    def index_from_node(self, node: Optional[_FsNode]) -> QModelIndex:
        if node is None or node == self._root_node or node.parent is None:
            return QModelIndex()
        row = node.parent.row_of_child(node)
        if row < 0:
            return QModelIndex()
        return self.createIndex(row, 0, node)

    def _reload_node_children(self, node: _FsNode):
        children = self._scan_children(node.path)
        parent_index = self.index_from_node(node)

        old_count = len(node.children)
        if old_count > 0:
            self.beginRemoveRows(parent_index, 0, old_count - 1)
            for child in list(node.children):
                self._unregister_node_recursive(child)
            node.children.clear()
            self.endRemoveRows()

        if children:
            self.beginInsertRows(parent_index, 0, len(children) - 1)
            for child in children:
                child.parent = node
                node.children.append(child)
                self._path_map[child.path] = child
            self.endInsertRows()

        node.children_loaded = True

    def _scan_children(self, folder_path: str) -> List[_FsNode]:
        result: List[_FsNode] = []
        if not os.path.isdir(folder_path):
            return result

        try:
            with os.scandir(folder_path) as it:
                entries = list(it)
        except Exception:
            return result

        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except Exception:
                continue
            abs_path = self._canonical(entry.path)
            if not self._include_excluded:
                if self._exclude_path_predicate:
                    try:
                        if self._exclude_path_predicate(abs_path, is_dir):
                            continue
                    except Exception:
                        pass
                if is_dir and self._exclude_dir_predicate:
                    try:
                        if self._exclude_dir_predicate(abs_path):
                            continue
                    except Exception:
                        pass

            node = _FsNode(path=abs_path, name=entry.name, is_dir=is_dir, parent=None)
            result.append(node)
        return result

    def _unregister_node_recursive(self, node: _FsNode):
        for child in list(node.children):
            self._unregister_node_recursive(child)
        self._path_map.pop(node.path, None)

    def _emit_foreground_update(self):
        def _walk(parent_index: QModelIndex):
            rows = self.rowCount(parent_index)
            for row in range(rows):
                idx = self.index(row, 0, parent_index)
                if not idx.isValid():
                    continue
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.ForegroundRole])
                node = cast(_FsNode, idx.internalPointer())
                if node is not None and node.children_loaded:
                    _walk(idx)

        _walk(QModelIndex())

    def _destination_dir_from_drop(self, parent: QModelIndex) -> Optional[str]:
        if not parent.isValid():
            return self._root_path
        node = cast(_FsNode, parent.internalPointer())
        if node.is_dir:
            return node.path
        if node.parent:
            return node.parent.path
        return self._root_path

    def _metadata_for_path(self, path: str, is_dir: bool) -> Dict[str, Any]:
        return {
            "path": self._canonical(path),
            "relative_path": self._relative_to_root(path),
            "is_dir": bool(is_dir),
        }

    def _relative_to_root(self, path: str) -> str:
        cpath = self._canonical(path)
        if not self._is_within_root(cpath):
            return cpath
        rel = os.path.relpath(cpath, self._root_path)
        return "." if rel in (".", "") else rel

    def _is_within_root(self, path: str) -> bool:
        return self._is_within(self._root_path, path)

    def _is_within(self, ancestor: str, path: str) -> bool:
        try:
            return os.path.commonpath([self._canonical(ancestor), self._canonical(path)]) == self._canonical(ancestor)
        except Exception:
            return False

    def _canonical(self, path: str) -> str:
        try:
            return os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        except Exception:
            return os.path.abspath(os.path.expanduser(path))

    def _decode_mime_paths(self, data: QMimeData) -> List[str]:
        raw = bytes(data.data(self.MIME_TYPE))
        if not raw:
            return []

        parsed: list[str] = []
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            payload = raw.decode("utf-8", errors="replace").strip()

        if isinstance(payload, list):
            for item in payload:
                text = str(item or "").strip()
                if text:
                    parsed.append(self._canonical(text))
        elif isinstance(payload, str):
            text = payload.strip()
            if text:
                parsed.append(self._canonical(text))

        return self._filter_nested_paths(parsed)

    def _filter_nested_paths(self, paths: List[str]) -> List[str]:
        ordered = sorted({self._canonical(path) for path in paths if isinstance(path, str) and path.strip()}, key=lambda p: (len(p), p.lower()))
        result: List[str] = []
        for path in ordered:
            if any(path == root or path.startswith(root + os.sep) for root in result):
                continue
            result.append(path)
        return result

class FileSystemTreeWidget(QTreeView):
    fileOpenRequested = Signal(str)
    pathContextMenuRequested = Signal(object, QPoint)  # path | None, global_pos
    operationError = Signal(str, str)                 # title, message
    pathMoved = Signal(str, str)                      # old_path, new_path

    def __init__(
            self,
            root_path: str,
            exclude_dir_predicate: Optional[Callable[[str], bool]] = None,
            exclude_path_predicate: Optional[Callable[[str, bool], bool]] = None,
            parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._model = FileSystemTreeModel(
            root_path=root_path,
            exclude_dir_predicate=exclude_dir_predicate,
            exclude_path_predicate=exclude_path_predicate,
            parent=self,
        )
        self.setModel(self._model)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.doubleClicked.connect(self._on_double_clicked)
        self.expanded.connect(self._on_expanded)
        self._model.filesystemError.connect(self.operationError)
        self._model.filesystemMoved.connect(self._on_model_path_moved)
        self._pending_expanded_paths_for_move: Optional[set[str]] = None

    def root_path(self) -> str:
        return self._model.root_path()

    def set_root_path(self, root_path: str):
        self._model.set_root_path(root_path)

    def set_git_tinting(self, *, enabled: bool, colors: Dict[str, str]):
        self._model.set_git_tinting(enabled=enabled, colors=colors)

    def set_git_status_maps(self, *, file_states: Dict[str, str], folder_states: Dict[str, str]):
        self._model.set_git_status_maps(file_states=file_states, folder_states=folder_states)

    def selected_paths(self) -> List[str]:
        indexes = [idx for idx in self.selectedIndexes() if idx.isValid()]
        seen: set[str] = set()
        result: List[str] = []
        for index in indexes:
            path = self._model.path_from_index(index)
            if not isinstance(path, str):
                continue
            cpath = self._model._canonical(path)
            if cpath in seen:
                continue
            seen.add(cpath)
            result.append(cpath)
        return result

    def selected_path(self) -> Optional[str]:
        indexes = self.selectedIndexes()
        if not indexes:
            return None
        return self._model.path_from_index(indexes[0])

    def metadata_for_path(self, path: str) -> Dict[str, Any]:
        index = self._model.index_from_path(path)
        if not index.isValid():
            is_dir = os.path.isdir(path)
            cpath = self._model._canonical(path)
            return {
                "path": cpath,
                "relative_path": cpath,
                "is_dir": is_dir,
            }
        return self._model.metadata_from_index(index)

    def path_from_index(self, index: QModelIndex) -> Optional[str]:
        return self._model.path_from_index(index)

    def refresh_project(self, include_excluded: bool = False):
        expanded_paths = self._collect_expanded_paths()
        selected = self.selected_path()
        self._model.refresh_tree(include_excluded=include_excluded)
        self._restore_expanded_paths(expanded_paths)
        if selected:
            self.select_path(selected)

    def refresh_subtree(self, path: str, include_excluded: Optional[bool] = None):
        expanded_paths = self._collect_expanded_paths()
        selected = self.selected_path()
        self._model.refresh_subtree(path, include_excluded=include_excluded)
        self._restore_expanded_paths(expanded_paths)
        if selected:
            self.select_path(selected)

    def expanded_paths(self) -> set[str]:
        return self._collect_expanded_paths()

    def select_path(self, path: str):
        if not path:
            return
        if not self._model.ensure_path_visible(path):
            return
        index = self._model.index_from_path(path)
        if not index.isValid():
            return

        node = cast(_FsNode, index.internalPointer())
        chain: List[QModelIndex] = []
        parent = node.parent
        while parent is not None and parent.parent is not None:
            chain.append(self._model.index_from_node(parent))
            parent = parent.parent
        for pidx in reversed(chain):
            if pidx.isValid():
                self.expand(pidx)

        self.setCurrentIndex(index)
        self.scrollTo(index)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        path = self._model.path_from_index(index) if index.isValid() else None
        self.pathContextMenuRequested.emit(path, event.globalPos())
        event.accept()

    def _on_expanded(self, index: QModelIndex):
        if self._model.canFetchMore(index):
            self._model.fetchMore(index)

    def dropEvent(self, event):
        self._pending_expanded_paths_for_move = self._collect_expanded_paths()
        super().dropEvent(event)
        if not event.isAccepted():
            self._pending_expanded_paths_for_move = None

    def _on_double_clicked(self, index: QModelIndex):
        if not index.isValid():
            return
        meta = self._model.metadata_from_index(index)
        path = meta.get("path")
        if not isinstance(path, str) or not path:
            return
        is_dir = bool(meta.get("is_dir"))
        if is_dir:
            self.setExpanded(index, not self.isExpanded(index))
            return
        self.fileOpenRequested.emit(path)

    def _on_model_path_moved(self, old_path: str, new_path: str):
        expanded = self._pending_expanded_paths_for_move
        self._pending_expanded_paths_for_move = None

        if expanded:
            old_prefix = self._model._canonical(old_path)
            new_prefix = self._model._canonical(new_path)
            remapped: set[str] = set()
            for path in expanded:
                cpath = self._model._canonical(path)
                if cpath == old_prefix:
                    remapped.add(new_prefix)
                    continue
                prefix = old_prefix + os.sep
                if cpath.startswith(prefix):
                    suffix = cpath[len(old_prefix):].lstrip(os.sep)
                    remapped.add(self._model._canonical(os.path.join(new_prefix, suffix)))
                    continue
                remapped.add(cpath)
            self._restore_expanded_paths(remapped)
        self.pathMoved.emit(old_path, new_path)

    def _collect_expanded_paths(self) -> set[str]:
        expanded: set[str] = set()

        def walk(parent_index: QModelIndex):
            rows = self._model.rowCount(parent_index)
            for row in range(rows):
                idx = self._model.index(row, 0, parent_index)
                if not idx.isValid():
                    continue
                path = self._model.path_from_index(idx)
                if isinstance(path, str) and self.isExpanded(idx):
                    expanded.add(self._model._canonical(path))
                node = cast(_FsNode, idx.internalPointer())
                if node is not None and node.children_loaded:
                    walk(idx)

        walk(QModelIndex())
        return expanded

    def _restore_expanded_paths(self, paths: set[str]):
        if not paths:
            return
        ordered = sorted(paths, key=lambda p: (p.count(os.sep), p.lower()))
        for path in ordered:
            if not self._model.ensure_path_visible(path):
                continue
            idx = self._model.index_from_path(path)
            if idx.isValid():
                self.expand(idx)


# --- 5. Demo Application ---
