"""
A reusable PySide6 tree widget for managing hierarchical items with a clean API,
drag & drop, and externally-provided context menus.
"""
import sys
import uuid
import json
from typing import  Optional, Any, Dict, List, cast, Union

from PySide6.QtCore import (
    Qt, QAbstractItemModel, QModelIndex, QMimeData, QPoint, Signal, QObject
)
from PySide6.QtGui import QFont, QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QTreeView, QAbstractItemView, QStyle, QMainWindow, QMenu,
    QWidget
)

# --- 1. Internal Data Representation ---

class TreeNode:
    """A helper class to represent a node within the tree model's internal structure."""
    def __init__(self, node_id: str, node_type: str, name: str,
                 metadata: Optional[Dict[str, Any]] = None,
                 parent: Optional['TreeNode'] = None):
        self.id = node_id
        self.type = node_type  # "group" or "item"
        self.name = name
        self.metadata = metadata if metadata is not None else {}
        self.parent = parent
        self.children: List['TreeNode'] = []

    def child_at(self, row: int) -> Optional['TreeNode']:
        """Returns the child at a specific row, or None if the row is invalid."""
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def row_of_child(self, child: 'TreeNode') -> int:
        """Returns the row index of a given child node."""
        try:
            return self.children.index(child)
        except ValueError:
            return -1

    def insert_child(self, child: 'TreeNode', row: int):
        """Inserts a child at a specific row."""
        self.children.insert(row, child)
        child.parent = self

    def remove_child(self, row: int) -> 'TreeNode':
        """Removes and returns the child at a specific row."""
        child = self.children.pop(row)
        child.parent = None
        return child

    def to_dict(self) -> Dict[str, Any]:
        """Converts the node and its descendants to a JSON-friendly dictionary."""
        data = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "metadata": self.metadata,
        }
        if self.type == "group":
            data["children"] = [child.to_dict() for child in self.children]
        return data


# --- 2. The Custom Tree Model ---

class HierarchicalTreeModel(QAbstractItemModel):
    """
    An item model for representing a hierarchical structure of nodes.

    This model manages the tree data, handles drag & drop logic, and provides
    methods to manipulate the tree structure programmatically.
    """
    MIME_TYPE = "application/x-treenode-id"

    # Signals for the host application to react to model changes
    node_moved = Signal(str, object, int)  # node_id, new_parent_id, new_index
    node_renamed = Signal(str, str)     # node_id, new_name

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._root_node = TreeNode(node_id="--root--", node_type="group", name="Root")
        self._id_map: Dict[str, TreeNode] = {}

    def _clear(self):
        """Clears the entire model."""
        self.beginResetModel()
        self._root_node = TreeNode(node_id="--root--", node_type="group", name="Root")
        self._id_map = {}
        self.endResetModel()

    def _find_node(self, node_id: Optional[str]) -> Optional[TreeNode]:
        """Finds a node by its ID, returning the root for None."""
        if node_id is None:
            return self._root_node
        return self._id_map.get(node_id)

    def _is_descendant(self, node: TreeNode, potential_ancestor: TreeNode) -> bool:
        """Checks if 'node' is a descendant of 'potential_ancestor'."""
        parent = node.parent
        while parent is not None:
            if parent == potential_ancestor:
                return True
            parent = parent.parent
        return False

    # --- Public API for Widget ---

    def load_tree_data(self, data: List[Dict[str, Any]]):
        """
        Replaces the entire tree with data from a nested dictionary structure.
        """
        self.beginResetModel()
        self._root_node = TreeNode(node_id="--root--", node_type="group", name="Root")
        self._id_map = {}

        def _recursive_build(parent_node: TreeNode, child_dicts: List[Dict[str, Any]]):
            for child_data in child_dicts:
                node_id = child_data.get("id", str(uuid.uuid4()))
                if node_id in self._id_map:
                    # In a real app, you might want to log a warning here
                    print(f"Warning: Duplicate ID '{node_id}' found. Generating a new one.")
                    node_id = str(uuid.uuid4())

                node = TreeNode(
                    node_id=node_id,
                    node_type=child_data.get("type", "item"),
                    name=child_data.get("name", "Unnamed"),
                    metadata=child_data.get("metadata", {}),
                    parent=parent_node
                )
                self._id_map[node.id] = node
                parent_node.children.append(node)

                if node.type == "group" and "children" in child_data:
                    _recursive_build(node, child_data["children"])

        _recursive_build(self._root_node, data)
        self.endResetModel()

    def get_tree_data(self) -> List[Dict[str, Any]]:
        """
        Returns the entire tree structure as a nested list of dictionaries.
        """
        return [child.to_dict() for child in self._root_node.children]

    def add_node(self, parent_id: Optional[str], node_type: str, name: str,
                 metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Adds a new node (group or item) to the tree.

        Returns:
            The ID of the newly created node.
        """
        parent_node = self._find_node(parent_id)
        if not parent_node or parent_node.type != "group":
            raise ValueError(f"Parent with ID '{parent_id}' is not a valid group.")

        new_id = str(uuid.uuid4())
        new_node = TreeNode(
            node_id=new_id,
            node_type=node_type,
            name=name,
            metadata=metadata,
            parent=parent_node
        )

        row = len(parent_node.children)
        parent_index = self.index_from_node(parent_node)

        self.beginInsertRows(parent_index, row, row)
        parent_node.children.append(new_node)
        self._id_map[new_id] = new_node
        self.endInsertRows()

        return new_id

    def remove_node(self, node_id: str) -> bool:
        """Removes a node and all its descendants from the tree."""
        node = self._find_node(node_id)
        if not node or not node.parent:
            return False

        parent_node = node.parent
        row = parent_node.row_of_child(node)
        parent_index = self.index_from_node(parent_node)

        self.beginRemoveRows(parent_index, row, row)

        # Recursively remove all descendant IDs from the map
        def _remove_from_map(n: TreeNode):
            if n.id in self._id_map:
                del self._id_map[n.id]
            for child in n.children:
                _remove_from_map(child)

        _remove_from_map(node)
        parent_node.remove_child(row)

        self.endRemoveRows()
        return True

    def move_node(self, node_id: str, new_parent_id: Optional[str], new_row: int) -> bool:
        """Programmatically moves a node to a new parent and position."""
        node = self._find_node(node_id)
        new_parent = self._find_node(new_parent_id)

        if not node or not new_parent or new_parent.type != "group" or not node.parent:
            return False

        if self._is_descendant(new_parent, node):
            # Prevent moving a node into one of its own children
            return False

        old_parent = node.parent
        old_row = old_parent.row_of_child(node)

        # Adjust destination row if moving within the same parent
        if old_parent == new_parent and new_row > old_row:
            new_row -= 1

        source_parent_index = self.index_from_node(old_parent)
        dest_parent_index = self.index_from_node(new_parent)

        if not self.beginMoveRows(source_parent_index, old_row, old_row, dest_parent_index, new_row):
            return False

        moved_node = old_parent.remove_child(old_row)
        new_parent.insert_child(moved_node, new_row)

        self.endMoveRows()

        self.node_moved.emit(node_id, new_parent_id, new_row)
        return True

    def index_from_node(self, node: Optional[TreeNode]) -> QModelIndex:
        """Creates a QModelIndex for a given TreeNode."""
        if not node or node == self._root_node or not node.parent:
            return QModelIndex()

        return self.createIndex(node.parent.row_of_child(node), 0, node)

    # --- QAbstractItemModel Implementation ---

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        parent_node = parent.internalPointer() if parent.isValid() else self._root_node
        child_node = parent_node.child_at(row)

        if child_node:
            return self.createIndex(row, column, child_node)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        child_node = cast(TreeNode, index.internalPointer())
        parent_node = child_node.parent

        if parent_node is None or parent_node == self._root_node:
            return QModelIndex()

        if parent_node.parent is None:
            # This should not happen if parent_node is not the root
            return QModelIndex()

        return self.createIndex(parent_node.parent.row_of_child(parent_node), 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        parent_node = parent.internalPointer() if parent.isValid() else self._root_node
        return len(parent_node.children) if parent_node else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        node = cast(TreeNode, index.internalPointer())

        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            return node.name

        if role == Qt.ItemDataRole.DecorationRole:
            style = QApplication.style()
            if node.type == "group":
                return style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            return style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        if role == Qt.ItemDataRole.FontRole and node.type == "group":
            font = QFont()
            font.setBold(True)
            return font

        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False

        node = cast(TreeNode, index.internalPointer())
        new_name = str(value)

        if node.name != new_name:
            node.name = new_name
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
            self.node_renamed.emit(node.id, new_name)
            return True

        return False

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return "Hierarchy"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            # Flags for the root area (for dropping on empty space)
            return Qt.ItemFlag.ItemIsDropEnabled

        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | \
                     Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsEditable

        node = cast(TreeNode, index.internalPointer())
        if node.type == "group":
            base_flags |= Qt.ItemFlag.ItemIsDropEnabled

        return base_flags

    # --- Drag & Drop Implementation ---

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> List[str]:
        return [self.MIME_TYPE]

    def mimeData(self, indexes: List[QModelIndex]) -> QMimeData:
        mime_data = QMimeData()
        if not indexes:
            return mime_data

        # Only support single-item drag
        node = cast(TreeNode, indexes[0].internalPointer())
        mime_data.setData(self.MIME_TYPE, node.id.encode())
        return mime_data

    def canDropMimeData(self, data: QMimeData, action: Qt.DropAction,
                        row: int, column: int, parent: QModelIndex) -> bool:
        if not data.hasFormat(self.MIME_TYPE) or action != Qt.DropAction.MoveAction:
            return False

        source_id = data.data(self.MIME_TYPE).data().decode()
        source_node = self._find_node(source_id)
        if not source_node:
            return False

        # If parent is invalid, we are dropping on the root.
        dest_parent_node = cast(TreeNode, parent.internalPointer()) if parent.isValid() else self._root_node

        # Disallow dropping an item onto itself or its own children.
        if dest_parent_node == source_node or self._is_descendant(dest_parent_node, source_node):
            return False

        return True

    def dropMimeData(self, data: QMimeData, action: Qt.DropAction,
                     row: int, column: int, parent: QModelIndex) -> bool:
        if not self.canDropMimeData(data, action, row, column, parent):
            return False

        source_id = data.data(self.MIME_TYPE).data().decode()
        dest_parent_node = cast(TreeNode, parent.internalPointer()) if parent.isValid() else self._root_node
        dest_parent_id = dest_parent_node.id if dest_parent_node != self._root_node else None

        # If row is -1, it means drop ON the parent, so append to end.
        if row == -1:
            row = len(dest_parent_node.children)

        return self.move_node(source_id, dest_parent_id, row)


# --- 3. The Reusable QTreeView Widget ---

class ReusableTreeWidget(QTreeView):
    """
    A reusable tree widget for managing hierarchical items.

    This widget provides a clean API for data manipulation, supports internal
    drag & drop, and emits signals for host application integration, including
    custom context menus.
    """
    # --- Signals ---
    nodeContextMenuRequested = Signal(object, QPoint)  # node_id or None, global_pos
    nodeSelectionChanged = Signal(object)            # node_id or None
    nodeRenamed = Signal(str, str)                   # node_id, new_name
    nodeMoved = Signal(str, object, int)             # node_id, new_parent_id, new_index

    def __init__(self, parent: Optional[QWidget] = None):
        """Initializes the tree widget."""
        super().__init__(parent)

        self._model = HierarchicalTreeModel(self)
        self.setModel(self._model)

        # Configure view behavior
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHeaderHidden(True)


        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        # Connect internal signals to emit our public signals
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._model.node_renamed.connect(self.nodeRenamed)
        self._model.node_moved.connect(self.nodeMoved)

    def _get_id_from_index(self, index: QModelIndex) -> Optional[str]:
        """Helper to get a node's ID from a model index."""
        if index.isValid():
            node = cast(TreeNode, index.internalPointer())
            return node.id
        return None

    # --- Public API ---

    def set_tree(self, data: List[Dict[str, Any]]):
        """
        Replaces the entire tree content from a nested list of node dictionaries.

        Each dictionary must have 'id', 'type', and 'name' keys. Groups can
        have a 'children' key.
        """
        self._model.load_tree_data(data)
        self.expandAll()

    def get_tree(self) -> List[Dict[str, Any]]:
        """
        Returns the current tree structure as a nested list of dictionaries,
        suitable for JSON serialization.
        """
        return self._model.get_tree_data()

    def add_group(self, parent_id: Optional[str], name: str,
                  metadata: Optional[Dict[str, Any]] = None) -> str:
        """Adds a new group node. Returns the new node's ID."""
        return self._model.add_node(parent_id, "group", name, metadata)

    def add_item(self, parent_id: Optional[str], name: str,
                 metadata: Optional[Dict[str, Any]] = None) -> str:
        """Adds a new leaf item node. Returns the new node's ID."""
        return self._model.add_node(parent_id, "item", name, metadata)

    def rename_node(self, node_id: str, new_name: str):
        """Programmatically renames a node."""
        node = self._model._find_node(node_id)
        if not node:
            return

        index = self._model.index_from_node(node)
        if index.isValid():
            self._model.setData(index, new_name, Qt.ItemDataRole.EditRole)

    def remove_node(self, node_id: str):
        """Removes a node from the tree."""
        self._model.remove_node(node_id)

    def move_node(self, node_id: str, new_parent_id: Optional[str], new_index: int):
        """Programmatically moves a node."""
        self._model.move_node(node_id, new_parent_id, new_index)

    # --- Event Handlers ---

    def contextMenuEvent(self, event):
        """
        Overrides the default context menu to emit a signal instead.
        """
        index = self.indexAt(event.pos())
        node_id = self._get_id_from_index(index)
        self.nodeContextMenuRequested.emit(node_id, event.globalPos())
        event.accept()

    def _on_selection_changed(self, selected, deselected):
        """
        Handles the view's selection change to emit a cleaner signal.
        """
        indexes = self.selectedIndexes()
        node_id = self._get_id_from_index(indexes[0]) if indexes else None
        self.nodeSelectionChanged.emit(node_id)


if __name__ == "__main__":

    # Example data structure
    EXAMPLE_DATA = [
        {
            "id": "group1",
            "type": "group",
            "name": "Git Commands",
            "metadata": {"icon": "git.png"},
            "children": [
                {"id": "item1", "type": "item", "name": "Status", "metadata": {}},
                {"id": "item2", "type": "item", "name": "Pull", "metadata": {}},
                {"id": "item3", "type": "item", "name": "Push", "metadata": {}},
            ]
        },
        {
            "id": "group2",
            "type": "group",
            "name": "Python",
            "metadata": {},
            "children": [
                {
                    "id": "group3",
                    "type": "group",
                    "name": "Environments",
                    "metadata": {},
                    "children": [
                        {"id": "item4", "type": "item", "name": "Create venv", "metadata": {}}
                    ]
                },
                {"id": "item5", "type": "item", "name": "Run Linter", "metadata": {}}
            ]
        },
        {"id": "item6", "type": "item", "name": "Loose Script", "metadata": {"path": "/dev/null"}}
    ]

    class DemoWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Reusable Tree Widget Demo")
            self.setGeometry(100, 100, 400, 600)

            self.tree_widget = ReusableTreeWidget()
            self.setCentralWidget(self.tree_widget)

            # Populate with initial data
            self.tree_widget.set_tree(EXAMPLE_DATA)

            # --- Connect signals to handlers ---
            self.tree_widget.nodeContextMenuRequested.connect(self.show_custom_context_menu)
            self.tree_widget.nodeSelectionChanged.connect(
                lambda nid: print(f"INFO: Selection changed to node: {nid}")
            )
            self.tree_widget.nodeRenamed.connect(
                lambda nid, name: print(f"INFO: Node '{nid}' renamed to '{name}'")
            )
            self.tree_widget.nodeMoved.connect(
                lambda nid, pid, idx: print(f"INFO: Node '{nid}' moved to parent '{pid}' at index {idx}")
            )

            # --- Demonstrate getting data back ---
            print("--- Initial Tree Data (JSON compatible) ---")
            print(json.dumps(self.tree_widget.get_tree(), indent=2))
            print("-" * 40)

        def show_custom_context_menu(self, node_id: Optional[str], global_pos: QPoint):
            """This is the host application's context menu implementation."""
            menu = QMenu()

            if node_id:
                # Clicked on an existing node
                rename_action = menu.addAction("Rename Node")
                rename_action.triggered.connect(
                    lambda: self.tree_widget.edit(self.tree_widget.selectedIndexes()[0])
                )

                remove_action = menu.addAction("Remove Node")
                remove_action.triggered.connect(
                    lambda: self.tree_widget.remove_node(node_id)
                )
                menu.addSeparator()

            # Actions that can target a node or empty space
            add_item_action = menu.addAction("Add Item...")
            add_group_action = menu.addAction("Add Group...")

            # Determine the parent for new nodes
            target_parent_id = None
            if node_id:
                node = self.tree_widget._model._find_node(node_id)
                if node and node.type == "group":
                    target_parent_id = node_id
                elif node and node.parent:
                    target_parent_id = node.parent.id if node.parent != self.tree_widget._model._root_node else None

            add_item_action.triggered.connect(
                lambda: self.tree_widget.add_item(target_parent_id, "New Item")
            )
            add_group_action.triggered.connect(
                lambda: self.tree_widget.add_group(target_parent_id, "New Group")
            )

            menu.exec(global_pos)

    app = QApplication(sys.argv)
    window = DemoWindow()
    window.show()
    sys.exit(app.exec())
