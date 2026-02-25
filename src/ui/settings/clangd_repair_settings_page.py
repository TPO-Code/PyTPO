from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from src.lang_cpp.clangd_repair import repair_clangd_includes


class ClangdRepairSettingsPage(QWidget):
    def __init__(
        self,
        *,
        manager: Any,
        on_runtime_refresh: Callable[[], None] | None = None,
        on_query_driver_updated: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._on_runtime_refresh = on_runtime_refresh
        self._on_query_driver_updated = on_query_driver_updated
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        self.info_label = QLabel(
            "Repairs clangd standard-header include resolution for this project.\n"
            "Flow: query-driver normalization first, then .clangd include patch if still needed."
        )
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        self.repair_btn = QPushButton("Repair Clangd Includes")
        self.repair_btn.clicked.connect(self._run_repair)
        root.addWidget(self.repair_btn)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addStretch(1)

    def has_pending_settings_changes(self) -> bool:
        return False

    def apply_settings_changes(self) -> list[str]:
        return []

    def _run_repair(self) -> None:
        project_root = str(self._manager.paths.project_root)
        cpp_cfg = self._manager.get("c_cpp", scope_preference="project", default={})
        if not isinstance(cpp_cfg, dict):
            cpp_cfg = {}
        clangd_path = str(cpp_cfg.get("clangd_path") or "clangd").strip() or "clangd"
        query_driver = str(cpp_cfg.get("query_driver") or "").strip()
        compile_mode = str(cpp_cfg.get("compile_commands_mode") or "auto").strip().lower()
        compile_path = str(cpp_cfg.get("compile_commands_path") or "").strip()

        probe_file = self._preferred_probe_file()
        self.repair_btn.setEnabled(False)
        try:
            result = repair_clangd_includes(
                project_root=project_root,
                clangd_path=clangd_path,
                query_driver=query_driver,
                compile_commands_mode=compile_mode,
                compile_commands_path=compile_path,
                target_file_path=probe_file,
            )
        finally:
            self.repair_btn.setEnabled(True)

        if result.query_driver_changed and result.query_driver:
            self._manager.set("c_cpp.query_driver", result.query_driver, "project")
            self._manager.save_all(scopes={"project"}, only_dirty=True)
            if callable(self._on_query_driver_updated):
                try:
                    self._on_query_driver_updated(result.query_driver)
                except Exception:
                    pass

        if result.wrote_clangd_file:
            self._set_status(
                f"{result.message}\n\nUpdated: {result.clangd_file_path}",
                error=not result.ok,
            )
        else:
            self._set_status(result.message, error=not result.ok)

        if callable(self._on_runtime_refresh):
            try:
                self._on_runtime_refresh()
            except Exception:
                pass

    def _preferred_probe_file(self) -> str:
        files = self._manager.get("open_editors", scope_preference="project", default=[])
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("file_path") or "").strip()
                if not path:
                    continue
                suffix = Path(path).suffix.lower()
                if suffix in {".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp", ".inl", ".c", ".cc", ".cpp", ".cxx"}:
                    return path
        return ""

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

