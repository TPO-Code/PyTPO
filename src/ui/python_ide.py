import os
import re
import shutil
import sys
import time
import tomllib
import weakref
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QFileSystemWatcher, QPoint, QSize, Qt, QTimer, QUrl, QByteArray
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QFontDatabase, QIcon, QTextCursor
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSizePolicy, QSpinBox, QStackedWidget, QTabWidget, QToolButton, QWidget

from src.ai.context_assembler import ContextAssembler
from src.ai.inline_controller import InlineSuggestionController
from src.ai.openai_compatible_client import OpenAICompatibleClient
from src.ai.settings_schema import normalize_ai_settings
from src.core.keybindings import get_action_sequence, normalize_keybindings
from src.formatting import CodeFormattingRegistry, FormatRequest
from src.formatting.providers import (
    CPP_FORMAT_EXTENSIONS,
    CPP_FORMAT_LANGUAGE_IDS,
    PYTHON_FORMAT_EXTENSIONS,
    PYTHON_FORMAT_LANGUAGE_IDS,
    RUST_FORMAT_EXTENSIONS,
    RUST_FORMAT_LANGUAGE_IDS,
    CppClangFormatProvider,
    PythonRuffFormatProvider,
    RustFormatProvider,
)
from src.git.github_auth import GitHubAuthStore
from src.git.github_share_service import GitHubShareService
from src.git.git_service import GitService
from src.instance_coordinator import ProjectInstanceServer, request_project_activation
from src.lang_cpp import CppLanguagePack
from src.lang_cpp.clangd_repair import missing_std_header_from_diagnostic, repair_clangd_includes
from src.lang_rust import RustLanguagePack
from src.settings_manager import SettingsManager
from src.services.document_outline_service import build_document_outline
from src.services.project_policy_service import ProjectPolicyService

from src.ui.console_run_manager import ConsoleRunManager
from src.ui.completion_manager import CompletionManager
from src.ui.controllers import (
    ActionRegistry,
    DiagnosticsController,
    ExecutionController,
    ExplorerController,
    GitWorkflowController,
    LanguageIntelligenceController,
    LanguageServiceHub,
    ProjectContext,
    ProjectLifecycleController,
    SearchController,
    ThemeController,
    VersionControlController,
    WorkspaceController,
)
from src.ui.custom_window import Window
from src.ui.dialogs.find_in_files_dialog import FindInFilesDialog
from src.ui.dialogs.file_dialog_bridge import get_save_file_name
from src.ui.settings_dialog import SettingsDialog as ScopedSettingsDialog, create_default_settings_schema

from src.ui.editor_workspace import EditorTabs, EditorWidget, EditorWorkspace
from src.ui.lint_manager import LintManager
from src.ui.widgets.file_system_tree import FileSystemTreeWidget
from src.ui.widgets.terminal_widget import TerminalWidget
from src.ui.widgets.problems_panel import ProblemsPanel
from src.ui.widgets.symbol_outline_panel import SymbolOutlinePanel
from src.ui.widgets.usages_panel import UsagesPanel
from src.ui.widgets.welcome_screen import WelcomeScreenWidget
from TPOPyside.widgets.tdoc_support import (
    PROJECT_MARKER_FILENAME,
    TDocDocumentWidget,
    TDocProjectIndex,
    collect_tdoc_diagnostics,
    is_tdoc_document_path,
    is_tdoc_related_path,
    parse_file_link,
    resolve_tdoc_root_for_path,
)

try:
    from shiboken6 import isValid as _is_qobject_valid
except Exception:
    def _is_qobject_valid(_obj) -> bool:
        return True


class SettingsDialog(QDialog):
    def __init__(
        self,
        font_size: int = 10,
        theme_name: str = "Dark",
        lint_cfg: dict | None = None,
        completion_cfg: dict | None = None,
        use_native_chrome: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("IDE Settings")
        self.resize(480, 560)

        layout = QFormLayout(self)
        self.font_size_edit = QSpinBox()
        self.font_size_edit.setRange(6, 48)
        self.font_size_edit.setValue(int(font_size))
        self.theme_edit = QLineEdit(theme_name)
        self.use_native_chrome = QCheckBox()
        self.use_native_chrome.setChecked(bool(use_native_chrome))

        lint = lint_cfg if isinstance(lint_cfg, dict) else {}
        backend = str(lint.get("backend", "ruff")).strip().lower()
        fallback = str(lint.get("fallback_backend", "ast")).strip().lower()
        if backend not in {"ruff", "pyflakes", "ast"}:
            backend = "ruff"
        if fallback not in {"none", "ruff", "pyflakes", "ast"}:
            fallback = "ast"

        self.lint_enabled = QCheckBox()
        self.lint_enabled.setChecked(bool(lint.get("enabled", True)))

        self.lint_backend = QComboBox()
        self.lint_backend.addItems(["ruff", "pyflakes", "ast"])
        self.lint_backend.setCurrentText(backend)

        self.lint_fallback = QComboBox()
        self.lint_fallback.addItems(["none", "ruff", "pyflakes", "ast"])
        self.lint_fallback.setCurrentText(fallback)

        self.lint_run_on_idle = QCheckBox()
        self.lint_run_on_idle.setChecked(bool(lint.get("run_on_idle", True)))

        self.lint_run_on_save = QCheckBox()
        self.lint_run_on_save.setChecked(bool(lint.get("run_on_save", True)))

        self.lint_respect_excludes = QCheckBox()
        self.lint_respect_excludes.setChecked(bool(lint.get("respect_excludes", True)))

        self.lint_debounce_ms = QSpinBox()
        self.lint_debounce_ms.setRange(100, 5000)
        self.lint_debounce_ms.setValue(int(lint.get("debounce_ms", 600)))

        completion = completion_cfg if isinstance(completion_cfg, dict) else {}
        completion_backend = str(completion.get("backend", "jedi")).strip().lower()
        if completion_backend != "jedi":
            completion_backend = "jedi"

        self.completion_enabled = QCheckBox()
        self.completion_enabled.setChecked(bool(completion.get("enabled", True)))

        self.completion_backend = QComboBox()
        self.completion_backend.addItems(["jedi"])
        self.completion_backend.setCurrentText(completion_backend)

        self.completion_respect_excludes = QCheckBox()
        self.completion_respect_excludes.setChecked(bool(completion.get("respect_excludes", True)))

        self.completion_auto_trigger = QCheckBox()
        self.completion_auto_trigger.setChecked(bool(completion.get("auto_trigger", True)))

        self.completion_auto_after_dot = QCheckBox()
        self.completion_auto_after_dot.setChecked(bool(completion.get("auto_trigger_after_dot", True)))

        self.completion_min_chars = QSpinBox()
        self.completion_min_chars.setRange(1, 10)
        self.completion_min_chars.setValue(int(completion.get("auto_trigger_min_chars", 2)))

        self.completion_debounce_ms = QSpinBox()
        self.completion_debounce_ms.setRange(40, 3000)
        self.completion_debounce_ms.setValue(int(completion.get("debounce_ms", 180)))

        self.completion_max_items = QSpinBox()
        self.completion_max_items.setRange(5, 1000)
        self.completion_max_items.setValue(int(completion.get("max_items", 500)))

        self.completion_case_sensitive = QCheckBox()
        self.completion_case_sensitive.setChecked(bool(completion.get("case_sensitive", False)))

        self.completion_show_signatures = QCheckBox()
        self.completion_show_signatures.setChecked(bool(completion.get("show_signatures", True)))

        self.completion_show_right_label = QCheckBox()
        self.completion_show_right_label.setChecked(bool(completion.get("show_right_label", True)))

        self.completion_show_doc_tooltip = QCheckBox()
        self.completion_show_doc_tooltip.setChecked(bool(completion.get("show_doc_tooltip", True)))

        self.completion_doc_tooltip_delay = QSpinBox()
        self.completion_doc_tooltip_delay.setRange(120, 1200)
        self.completion_doc_tooltip_delay.setValue(int(completion.get("doc_tooltip_delay_ms", 180)))

        layout.addRow("Font Size:", self.font_size_edit)
        layout.addRow("Theme Name:", self.theme_edit)
        layout.addRow("Use Native Window Chrome:", self.use_native_chrome)
        layout.addRow("----- Lint -----", QLabel(""))
        layout.addRow("Lint Enabled:", self.lint_enabled)
        layout.addRow("Lint Backend:", self.lint_backend)
        layout.addRow("Fallback Backend:", self.lint_fallback)
        layout.addRow("Run On Idle:", self.lint_run_on_idle)
        layout.addRow("Run On Save:", self.lint_run_on_save)
        layout.addRow("Debounce (ms):", self.lint_debounce_ms)
        layout.addRow("Respect Excludes:", self.lint_respect_excludes)
        layout.addRow("----- Completion -----", QLabel(""))
        layout.addRow("Completion Enabled:", self.completion_enabled)
        layout.addRow("Completion Backend:", self.completion_backend)
        layout.addRow("Respect Excludes:", self.completion_respect_excludes)
        layout.addRow("Auto Trigger:", self.completion_auto_trigger)
        layout.addRow("Auto Trigger After '.':", self.completion_auto_after_dot)
        layout.addRow("Auto Trigger Min Chars:", self.completion_min_chars)
        layout.addRow("Debounce (ms):", self.completion_debounce_ms)
        layout.addRow("Max Items:", self.completion_max_items)
        layout.addRow("Case Sensitive:", self.completion_case_sensitive)
        layout.addRow("Show Signatures:", self.completion_show_signatures)
        layout.addRow("Show Right Label:", self.completion_show_right_label)
        layout.addRow("Show Doc Tooltip:", self.completion_show_doc_tooltip)
        layout.addRow("Doc Tooltip Delay (ms):", self.completion_doc_tooltip_delay)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[int, str, dict, dict, bool]:
        fs = int(self.font_size_edit.value())
        theme = self.theme_edit.text().strip() or "Dark"
        lint_cfg = {
            "enabled": bool(self.lint_enabled.isChecked()),
            "backend": str(self.lint_backend.currentText()).strip().lower(),
            "fallback_backend": str(self.lint_fallback.currentText()).strip().lower(),
            "run_on_idle": bool(self.lint_run_on_idle.isChecked()),
            "run_on_save": bool(self.lint_run_on_save.isChecked()),
            "debounce_ms": int(self.lint_debounce_ms.value()),
            "respect_excludes": bool(self.lint_respect_excludes.isChecked()),
        }
        completion_cfg = {
            "enabled": bool(self.completion_enabled.isChecked()),
            "respect_excludes": bool(self.completion_respect_excludes.isChecked()),
            "auto_trigger": bool(self.completion_auto_trigger.isChecked()),
            "auto_trigger_after_dot": bool(self.completion_auto_after_dot.isChecked()),
            "auto_trigger_min_chars": int(self.completion_min_chars.value()),
            "debounce_ms": int(self.completion_debounce_ms.value()),
            "max_items": int(self.completion_max_items.value()),
            "case_sensitive": bool(self.completion_case_sensitive.isChecked()),
            "backend": str(self.completion_backend.currentText()).strip().lower() or "jedi",
            "show_signatures": bool(self.completion_show_signatures.isChecked()),
            "show_right_label": bool(self.completion_show_right_label.isChecked()),
            "show_doc_tooltip": bool(self.completion_show_doc_tooltip.isChecked()),
            "doc_tooltip_delay_ms": int(self.completion_doc_tooltip_delay.value()),
        }
        return fs, theme, lint_cfg, completion_cfg, bool(self.use_native_chrome.isChecked())


class PythonIDE(Window):
    APP_NAME = "PyTPO"
    PROJECT_JSON = ".tide/project.json"
    IDE_SETTINGS_DIRNAME = ".pytpo"
    NO_PROJECT_MODE_ENV = "PYTPO_NO_PROJECT_MODE"
    FORCE_NO_PROJECT_ARG = "--no-project"
    NO_PROJECT_DIRNAME = "no-project-workspace"
    NO_PROJECT_INSTANCE_ID = "__no_project__"
    THEMES_DIRNAME = "themes"
    THEME_EXTENSION = ".qss"
    PYTHON_SOURCE_SUFFIXES = (".py", ".pyw", ".pyi")
    FONT_SIZE_MIN = 6
    FONT_SIZE_MAX = 48
    EDITOR_FONT_FALLBACKS = (
        "Cascadia Code",
        "Consolas",
        "JetBrains Mono",
        "Fira Code",
        "Courier New",
        "Monospace",
    )

    @classmethod
    def no_project_instance_key(cls) -> str:
        return cls.NO_PROJECT_INSTANCE_ID

    @classmethod
    def app_icon_path(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "icons" / "app_icon.png"

    def __init__(self):
        requested_root = os.path.realpath(QDir.currentPath())
        ide_app_dir = self._default_ide_app_dir()
        self._migrate_legacy_ide_settings_file(ide_app_dir)
        self.no_project_mode = os.environ.get(self.NO_PROJECT_MODE_ENV, "").strip() == "1"
        if self.no_project_mode:
            workspace = Path(ide_app_dir) / self.NO_PROJECT_DIRNAME
            workspace.mkdir(parents=True, exist_ok=True)
            project_root = str(workspace)
        else:
            project_root = requested_root
        self.settings_manager = SettingsManager(
            project_root=project_root,
            ide_app_dir=ide_app_dir,
            project_persistent=not self.no_project_mode,
        )
        self.settings_manager.load_all()
        self._settings_load_errors: dict[str, str] = {}

        use_native_chrome = bool(
            self.settings_manager.get(
                "window.use_native_chrome",
                scope_preference="ide",
                default=False,
            )
        )
        if os.environ.get("PYTPO_FORCE_NATIVE_CHROME", "").strip() == "1":
            use_native_chrome = True

        super().__init__(use_native_chrome=use_native_chrome)
        self.setWindowTitle(self.APP_NAME)
        # Code-level override for custom title text visibility.
        # Set to None to follow `window.show_title_in_custom_toolbar` from settings.
        self._custom_title_text_visibility_override: bool | None = False
        self.set_title_text_visible(bool(self._custom_title_text_visibility_override))
        self.resize(1280, 800)

        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.GroupedDragging
        )

        self.project_root = self._canonical_path(project_root)
        self.project_config_path = self._canonical_path(str(self.settings_manager.project_path))
        self.ide_app_dir = self._canonical_path(str(self.settings_manager.paths.ide_app_dir))
        self.ide_settings_path = self._canonical_path(str(self.settings_manager.ide_path))
        self.settings = self.settings_manager.compat

        self.config = self.load_or_create_project_config()
        self._apply_application_identity()
        desired_chrome = bool(self.config.get("window", {}).get("use_native_chrome", False))
        if self.use_native_chrome != desired_chrome:
            self.set_chrome_mode(desired_chrome)
        self.font_size = int(
            self.settings_manager.get(
                "font_size",
                scope_preference="ide",
                default=self.config.get("font_size", 10),
            )
        )
        self.font_family = self._resolve_editor_font_family(
            self.settings_manager.get(
                "font_family",
                scope_preference="ide",
                default=self.config.get("font_family", ""),
            )
        )
        self.theme_name = str(
            self.settings_manager.get(
                "theme",
                scope_preference="ide",
                default=self.config.get("theme", "Dark"),
            )
        )
        self.project_policy_service = ProjectPolicyService(
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            rel_to_project=self._rel_to_project,
            path_has_prefix=self._path_has_prefix,
            resolve_path_from_project=self._resolve_path_from_project,
            resolve_path_from_project_no_symlink_resolve=self._resolve_path_from_project_no_symlink_resolve,
            normalize_rel=self._normalize_rel,
        )

        self.terminal: TerminalWidget | None = None
        self.console_tabs: QTabWidget | None = None
        self.console_run_manager: ConsoleRunManager | None = None
        self.problems_panel: ProblemsPanel | None = None
        self.dock_problems: QDockWidget | None = None
        self.usages_panel: UsagesPanel | None = None
        self.dock_usages: QDockWidget | None = None
        self.symbol_outline_panel: SymbolOutlinePanel | None = None
        self.dock_outline: QDockWidget | None = None

        self.lint_manager = LintManager(
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            resolve_interpreter=self.resolve_interpreter,
            is_path_excluded=self.is_path_excluded,
            follow_symlinks_provider=self._lint_follow_symlinks,
            parent=self,
        )
        self.completion_manager = CompletionManager(
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            resolve_interpreter=self.resolve_interpreter,
            is_path_excluded=self.is_path_excluded,
            parent=self,
        )
        self.cpp_language_pack = CppLanguagePack(
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            parent=self,
        )
        self.rust_language_pack = RustLanguagePack(
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            path_has_prefix=self._path_has_prefix,
            parent=self,
        )
        self.code_formatting_registry = CodeFormattingRegistry()
        self.code_formatting_registry.register_provider(
            CppClangFormatProvider(
                canonicalize=self._canonical_path,
                path_has_prefix=self._path_has_prefix,
            ),
            language_ids=set(CPP_FORMAT_LANGUAGE_IDS),
            extensions=set(CPP_FORMAT_EXTENSIONS),
        )
        self.code_formatting_registry.register_provider(
            PythonRuffFormatProvider(
                canonicalize=self._canonical_path,
                path_has_prefix=self._path_has_prefix,
            ),
            language_ids=set(PYTHON_FORMAT_LANGUAGE_IDS),
            extensions=set(PYTHON_FORMAT_EXTENSIONS),
        )
        self.code_formatting_registry.register_provider(
            RustFormatProvider(
                canonicalize=self._canonical_path,
                path_has_prefix=self._path_has_prefix,
            ),
            language_ids=set(RUST_FORMAT_LANGUAGE_IDS),
            extensions=set(RUST_FORMAT_EXTENSIONS),
        )
        self.language_service_hub = LanguageServiceHub(parent=self)
        self.register_language_provider(
            self.completion_manager,
            language_ids={"python", "py"},
            default=True,
        )
        self.register_language_provider(
            self.cpp_language_pack,
            language_ids={"c", "cpp"},
            default=False,
        )
        self.register_language_provider(
            self.rust_language_pack,
            language_ids={"rust"},
            default=False,
        )
        self.cpp_language_pack.update_project_settings(self._cpp_config())
        self.rust_language_pack.update_project_settings(self._rust_config())
        self._ai_provider_client = OpenAICompatibleClient()
        self._ai_context_assembler = ContextAssembler(project_root=self.project_root, canonicalize=self._canonical_path)
        self.inline_suggestion_controller = InlineSuggestionController(
            provider_client=self._ai_provider_client,
            context_assembler=self._ai_context_assembler,
            parent=self,
        )
        self._diagnostics_by_file: dict[str, list[dict]] = {}
        self._tdoc_diagnostics_by_root: dict[str, dict[str, list[dict]]] = {}
        self._tdoc_validation_timers: dict[str, QTimer] = {}
        self._tdoc_pending_paths_by_root: dict[str, str] = {}
        self._lint_hooked_editors: set[str] = set()
        self._word_wrap_enabled_file_types: set[str] = set()
        self._completion_next_token = 0
        self._completion_latest_by_editor: dict[str, int] = {}
        self._completion_request_meta: dict[int, dict] = {}
        self._signature_next_token = 0
        self._signature_latest_by_editor: dict[str, int] = {}
        self._signature_request_meta: dict[int, dict] = {}
        self._definition_next_token = 0
        self._definition_latest_by_editor: dict[str, int] = {}
        self._definition_request_meta: dict[int, dict] = {}
        self._usages_next_token = 0
        self._usages_request_meta: dict[int, dict] = {}
        self._active_usages_token = 0
        self._rename_request_meta: dict[int, dict] = {}
        self._active_rename_token = 0
        self._outline_last_editor_id = ""
        self._outline_last_revision = -1
        self._outline_request_token = 0
        self._outline_active_token = 0
        self._outline_refresh_timer = QTimer(self)
        self._outline_refresh_timer.setSingleShot(True)
        self._outline_refresh_timer.setInterval(220)
        self._outline_refresh_timer.timeout.connect(self._refresh_symbol_outline_panel)

        self._startup_done = False
        self._startup_running = False
        self._ad_hoc_terminal_counter = 0
        self._ad_hoc_terminal_keys: set[str] = set()
        self.recent_projects_menu: QMenu | None = None
        self._center_stack: QStackedWidget | None = None
        self._welcome_view: WelcomeScreenWidget | None = None
        self._welcome_saved_state: QByteArray | None = None
        self._welcome_saved_visibility: dict[str, bool] = {}
        self._window_geometry_restored = False
        self._dock_layout_restored = False
        self._panel_toggle_actions: list[QAction] = []
        self._act_build_current: QAction | None = None
        self._act_build_and_run_current: QAction | None = None
        self._act_run_current: QAction | None = None
        self._act_rerun_current: QAction | None = None
        self._act_stop_current: QAction | None = None
        self._act_new_terminal: QAction | None = None
        self._act_close_terminal: QAction | None = None
        self._act_close_project: QAction | None = None
        self._act_format_file: QAction | None = None
        self._act_format_selection: QAction | None = None
        self._act_rename_symbol: QAction | None = None
        self._act_extract_variable: QAction | None = None
        self._act_extract_method: QAction | None = None
        self._run_python_config_menu: QMenu | None = None
        self._run_python_config_action_group: QActionGroup | None = None
        self._run_cargo_config_menu: QMenu | None = None
        self._run_cargo_config_action_group: QActionGroup | None = None
        self._run_build_config_menu: QMenu | None = None
        self._run_build_config_action_group: QActionGroup | None = None
        self._find_in_files_dialog: FindInFilesDialog | None = None
        self._find_results_dock: QDockWidget | None = None
        self._documentation_viewer: QWidget | None = None
        self._instance_server: ProjectInstanceServer | None = None
        self._skip_close_save_prompt_once = False
        self._toolbar_missing_icon_keys: set[str] = set()
        self._toolbar_controls_host: QWidget | None = None
        self._toolbar_build_btn: QToolButton | None = None
        self._toolbar_build_run_btn: QToolButton | None = None
        self._toolbar_tdoc_index_btn: QToolButton | None = None
        self._toolbar_run_btn: QToolButton | None = None
        self._toolbar_run_menu: QMenu | None = None
        self._toolbar_stop_btn: QToolButton | None = None
        self._toolbar_stop_menu: QMenu | None = None
        self._toolbar_settings_btn: QToolButton | None = None
        self._tree_clipboard_paths: list[str] = []
        self._tree_clipboard_mode: str = "copy"
        self._import_symbol_probe_cache: dict[tuple[str, str], bool] = {}
        self._import_module_probe_cache: dict[str, bool] = {}
        self._qt_symbol_namespace_cache: dict[str, list[tuple[str, str]]] = {}
        self._project_symbol_file_exports: dict[str, tuple[str, set[str], int, int]] = {}
        self._project_symbol_modules_by_name: dict[str, list[str]] = {}
        self._ai_recent_files: list[str] = []
        self._toolbar_ai_checkbox: QCheckBox | None = None
        self._toolbar_ai_toggle_guard = False
        self._lsp_noise_notice_shown = False
        self._clangd_std_header_prompt_keys: set[str] = set()
        self._clangd_repair_active = False
        self._last_status_debug_message = ""
        self._last_status_debug_at = 0.0
        self._status_git_branch_label: QLabel | None = None
        self._github_auth_store = GitHubAuthStore(self.ide_app_dir)
        self.git_service = GitService(
            canonicalize=self._canonical_path,
            ide_app_dir=self.ide_app_dir,
            github_token_provider=lambda: self._github_auth_store.get(),
            use_token_for_git_provider=lambda: bool(
                self.settings_manager.get("github.use_token_for_git", scope_preference="ide", default=True)
            ),
        )
        self.github_share_service = GitHubShareService(git_service=self.git_service)
        self._git_repo_root: str | None = None
        self._git_current_branch: str = ""
        self._git_file_states: dict[str, str] = {}
        self._git_folder_states: dict[str, str] = {}
        self._git_refresh_inflight = False
        self._git_refresh_requested = False

        self._external_file_signatures: dict[str, tuple[bool, int, int]] = {}
        self._external_conflict_signatures: dict[str, tuple[bool, int, int]] = {}
        self._project_config_reload_pending = False
        self._project_config_reload_honor_open_editors = False
        self._project_config_reload_source = ""
        self._project_config_reload_active = False
        self._project_fs_watcher: QFileSystemWatcher | None = None
        self._project_fs_watched_dirs: set[str] = set()
        self._project_fs_pending_dirs: set[str] = set()
        self._project_fs_focus_refresh_at = 0.0
        self._project_fs_refresh_timer = QTimer(self)
        self._project_fs_refresh_timer.setSingleShot(True)
        self._project_fs_refresh_timer.setInterval(260)
        self._project_fs_refresh_timer.timeout.connect(self._flush_project_fs_refreshes)
        self._project_fs_watch_sync_timer = QTimer(self)
        self._project_fs_watch_sync_timer.setSingleShot(True)
        self._project_fs_watch_sync_timer.setInterval(280)
        self._project_fs_watch_sync_timer.timeout.connect(self._sync_project_fs_watches)

        self._word_wrap_enabled_file_types = self._load_word_wrap_enabled_file_types()

        self.project_context = ProjectContext(
            project_root=self.project_root,
            settings_manager=self.settings_manager,
            canonicalize=self._canonical_path,
            rel_to_project=self._rel_to_project,
            is_path_excluded=self.is_path_excluded,
            lint_follow_symlinks_provider=self._lint_follow_symlinks,
            config_provider=lambda: self.config,
            resolve_folder_policy=self.resolve_folder_policy,
            resolve_interpreter=self.resolve_interpreter,
        )
        self.workspace_controller = WorkspaceController(self, parent=self)
        self.execution_controller = ExecutionController(self)
        self.language_intelligence_controller = LanguageIntelligenceController(self)
        self.project_lifecycle_controller = ProjectLifecycleController(self)
        self.git_workflow_controller = GitWorkflowController(self)
        self.theme_controller = ThemeController(self)
        self.search_controller = SearchController(self, self.project_context, parent=self)
        self.diagnostics_controller = DiagnosticsController(self, self.project_context)
        self.action_registry = ActionRegistry
        self.explorer_controller = ExplorerController(self, None)

        self.setup_editor_workspace_service()
        self.setup_project_explorer()
        self.explorer_controller.tree = self.tree
        self._setup_project_fs_watcher()
        self.version_control_controller = VersionControlController(self, self.git_service, self.tree, parent=self)
        self.version_control_controller.statusChanged.connect(self._on_git_status_changed)
        self.setup_bottom_panels()
        self._setup_status_bar_widgets()
        self._bind_status_bar_debug_mirror()
        self.setup_menus()
        self._restore_window_and_dock_layout()
        self._report_settings_load_errors(source="startup")
        self._setup_instance_server()
        if not self.no_project_mode:
            self._remember_recent_project(self.project_root, save=True)
        self._configure_autosave_timer()

        self.lint_manager.fileDiagnosticsUpdated.connect(self._on_file_diagnostics_updated)
        self.lint_manager.fileDiagnosticsCleared.connect(self._on_file_diagnostics_cleared)
        self.lint_manager.allDiagnosticsCleared.connect(self._on_all_diagnostics_cleared)
        self.lint_manager.problemCountChanged.connect(self._on_problem_count_changed)
        self.lint_manager.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 2500))
        self.lint_manager.update_settings(self._lint_config())
        self.language_service_hub.completionReady.connect(self._on_completion_result_ready)
        self.language_service_hub.signatureReady.connect(self._on_signature_result_ready)
        self.language_service_hub.definitionReady.connect(self._on_definition_result_ready)
        self.language_service_hub.referencesProgress.connect(self._on_references_progress)
        self.language_service_hub.referencesReady.connect(self._on_references_done)
        self.language_service_hub.statusMessage.connect(self._on_language_service_status_message)
        self.language_service_hub.update_settings(self._completion_config())
        self.cpp_language_pack.diagnosticsUpdated.connect(self._on_cpp_file_diagnostics_updated)
        self.rust_language_pack.diagnosticsUpdated.connect(self._on_rust_file_diagnostics_updated)
        self.inline_suggestion_controller.suggestionReady.connect(self._on_ai_inline_suggestion_ready)
        self.inline_suggestion_controller.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 2500))
        self.inline_suggestion_controller.update_settings(self._ai_assist_config())
        self._apply_git_tinting_config()
        self._configure_git_poll_timer()
        self.schedule_git_status_refresh(delay_ms=80)
        if self.no_project_mode:
            self._enter_no_project_ui_mode(save_snapshot=False)
        self._bind_global_action_recompute_hooks()
        self._refresh_runtime_action_states()

        self.statusBar().showMessage("Ready")

    # ---------- Startup ----------

    def showEvent(self, event):
        super().showEvent(event)
        if self._startup_done or self._startup_running:
            return
        self._startup_running = True
        QTimer.singleShot(0, self._run_startup_pipeline)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.schedule_git_status_refresh(delay_ms=60)
            self._maybe_refresh_project_tree_on_focus()

    def _run_startup_pipeline(self):
        if not self._window_geometry_restored:
            self.resize(1280, 800)

        self.restore_open_files_only()
        self._attach_all_editor_lint_hooks()
        self._normalize_editor_docks()
        self.refresh_project_tree()

        if not self._dock_layout_restored:
            self.apply_default_layout()
            QTimer.singleShot(80, self.apply_default_layout)
        self._seed_external_file_watch_state()
        self._external_file_watch_timer.start()
        self.schedule_git_status_refresh(delay_ms=80, force=True)
        if self.no_project_mode:
            self._enter_no_project_ui_mode()
        elif self.console_tabs is not None and self.console_tabs.count() == 0:
            self.new_terminal_tab()
        self._refresh_runtime_action_states()
        self._schedule_symbol_outline_refresh(immediate=True)

        self._startup_running = False
        self._startup_done = True

    def _rebuild_base_docks(self):
        # The docks are already created and tabified in setup_bottom_panels().
        # Avoid remove/re-add/tabify churn on startup; it can trigger Qt crashes.
        for dock, area in (
            (self.dock_project, Qt.LeftDockWidgetArea),
            (self.dock_debug, Qt.BottomDockWidgetArea),
            (self.dock_terminal, Qt.BottomDockWidgetArea),
            (self.dock_problems, Qt.BottomDockWidgetArea),
            (self.dock_usages, Qt.BottomDockWidgetArea),
            (self.dock_outline, Qt.RightDockWidgetArea),
        ):
            if dock is None:
                continue
            try:
                if dock.isFloating():
                    dock.setFloating(False)
            except RuntimeError:
                continue
            try:
                if self.dockWidgetArea(dock) == Qt.NoDockWidgetArea:
                    self.addDockWidget(area, dock)
            except RuntimeError:
                continue

        self.dock_project.show()
        self.dock_debug.show()
        self.dock_terminal.hide()
        if self.dock_problems is not None:
            self.dock_problems.hide()
        if self.dock_usages is not None:
            self.dock_usages.hide()
        if self.dock_outline is not None:
            self.dock_outline.hide()

    def _normalize_editor_docks(self):
        # Compatibility no-op for workspace-based editors.
        edocks = self.editor_workspace.editor_docks()
        for d in edocks:
            d.setFloating(True)
            d.hide()
        prev = None
        for d in edocks:
            d.setFloating(False)
            self.addDockWidget(Qt.RightDockWidgetArea, d)
            d.show()
            if prev is not None:
                self.tabifyDockWidget(prev, d)
            prev = d
        if edocks:
            edocks[0].raise_()

    # ---------- Setup ----------

    def setup_editor_workspace_service(self):
        self.editor_workspace = EditorWorkspace(self)
        self.editor_workspace.set_editor_font_defaults(
            font_size=int(self.font_size),
            font_family=str(self.font_family or "").strip(),
        )
        if self.no_project_mode:
            self._center_stack = QStackedWidget(self)
            self._welcome_view = WelcomeScreenWidget(self)
            self._welcome_view.openProjectFolderRequested.connect(self.open_project_dialog)
            self._welcome_view.newProjectRequested.connect(self.open_new_project_dialog)
            self._welcome_view.cloneRepositoryRequested.connect(self.open_clone_repository_dialog)
            self._welcome_view.createFromTemplateRequested.connect(self.open_new_project_dialog)
            self._welcome_view.openRecentProjectRequested.connect(self.open_project_path)
            self._welcome_view.removeRecentProjectRequested.connect(self._remove_recent_project_path)
            self._welcome_view.revealRecentProjectRequested.connect(self._reveal_recent_project_path)
            self._welcome_view.clearRecentProjectsRequested.connect(self.clear_recent_projects)
            self._center_stack.addWidget(self._welcome_view)
            self._center_stack.addWidget(self.editor_workspace)
            self._center_stack.setCurrentWidget(self._welcome_view)
            self.set_content_widget(self._center_stack)
            self._refresh_welcome_recent_projects()
            return
        self.set_content_widget(self.editor_workspace)

    def _refresh_welcome_recent_projects(self) -> None:
        welcome = self._welcome_view
        if welcome is None:
            return
        welcome.set_recent_projects(self._recent_projects())

    def _remove_recent_project_path(self, path: str) -> None:
        target = self._canonical_path(path)
        keep = [entry for entry in self._recent_projects() if entry.lower() != target.lower()]
        self._set_recent_projects(keep, save=True)
        self.statusBar().showMessage("Removed from recent projects.", 1400)

    def _reveal_recent_project_path(self, path: str) -> None:
        target = self._canonical_path(path)
        reveal_dir = target if os.path.isdir(target) else self._canonical_path(os.path.dirname(target))
        if not os.path.isdir(reveal_dir):
            self.statusBar().showMessage("Project path is not available on disk.", 1800)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(reveal_dir))

    def setup_project_explorer(self):
        self.dock_project = QDockWidget(self._project_explorer_title(), self)
        self.dock_project.setObjectName("dock_project")
        self.dock_project.setAllowedAreas(Qt.LeftDockWidgetArea)
        self.dock_project.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self.dock_project.setMinimumWidth(220)

        self.tree = FileSystemTreeWidget(
            root_path=self.project_root,
            exclude_path_predicate=self._is_tree_path_excluded,
            parent=self,
        )
        self.tree.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.tree.fileOpenRequested.connect(self.open_file)
        self.tree.pathContextMenuRequested.connect(self._show_project_tree_context_menu)
        self.tree.operationError.connect(self._show_tree_error)
        self.tree.pathMoved.connect(self._on_tree_path_moved)

        act_tree_copy = QAction("Copy", self.tree)
        act_tree_copy.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_tree_copy.triggered.connect(self._copy_tree_selection)
        self.tree.addAction(act_tree_copy)
        self._act_tree_copy = act_tree_copy

        act_tree_cut = QAction("Cut", self.tree)
        act_tree_cut.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_tree_cut.triggered.connect(self._cut_tree_selection)
        self.tree.addAction(act_tree_cut)
        self._act_tree_cut = act_tree_cut

        act_tree_paste = QAction("Paste", self.tree)
        act_tree_paste.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_tree_paste.triggered.connect(self._paste_tree_into_selection)
        self.tree.addAction(act_tree_paste)
        self._act_tree_paste = act_tree_paste

        act_tree_rename = QAction("Rename", self.tree)
        act_tree_rename.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_tree_rename.triggered.connect(self._rename_tree_selection)
        self.tree.addAction(act_tree_rename)
        self._act_tree_rename = act_tree_rename

        act_tree_delete = QAction("Delete", self.tree)
        act_tree_delete.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_tree_delete.triggered.connect(self._delete_tree_selection)
        self.tree.addAction(act_tree_delete)
        self._act_tree_delete = act_tree_delete

        self.dock_project.setWidget(self.tree)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_project)

    def _setup_project_fs_watcher(self) -> None:
        if self.no_project_mode:
            return
        if not isinstance(getattr(self, "tree", None), FileSystemTreeWidget):
            return
        watcher = QFileSystemWatcher(self)
        watcher.directoryChanged.connect(self._on_project_fs_directory_changed)
        self._project_fs_watcher = watcher
        try:
            self.tree.expanded.connect(self._on_tree_folder_expanded)
            self.tree.collapsed.connect(self._on_tree_folder_collapsed)
        except Exception:
            pass
        self._sync_project_fs_watches()

    def _on_tree_folder_expanded(self, index) -> None:
        tree = getattr(self, "tree", None)
        if not isinstance(tree, FileSystemTreeWidget):
            return
        path = tree.path_from_index(index)
        if isinstance(path, str) and os.path.isdir(path):
            self._queue_project_fs_refresh(path)
        self._schedule_project_fs_watch_sync()

    def _on_tree_folder_collapsed(self, _index) -> None:
        self._schedule_project_fs_watch_sync()

    def _on_project_fs_directory_changed(self, path: str) -> None:
        self._queue_project_fs_refresh(path)
        self._schedule_project_fs_watch_sync()

    def _queue_project_fs_refresh(self, path: str) -> None:
        tree = getattr(self, "tree", None)
        if not isinstance(tree, FileSystemTreeWidget):
            return
        if self.no_project_mode:
            return
        cpath = self._canonical_existing_watch_dir(path)
        if not cpath:
            return
        if cpath != self.project_root and self._is_tree_path_excluded(cpath, True):
            return
        self._project_fs_pending_dirs.add(cpath)
        self._project_fs_refresh_timer.start()

    def _flush_project_fs_refreshes(self) -> None:
        pending = set(self._project_fs_pending_dirs)
        self._project_fs_pending_dirs.clear()
        if not pending:
            return
        targets = self._filter_nested_paths(sorted(pending, key=lambda path: (len(path), path.lower())))
        if len(targets) > 14:
            self.refresh_project_tree()
        else:
            for target in targets:
                self.refresh_subtree(target)
        self._schedule_project_fs_watch_sync()

    def _schedule_project_fs_watch_sync(self) -> None:
        if self.no_project_mode:
            return
        self._project_fs_watch_sync_timer.start()

    def _sync_project_fs_watches(self) -> None:
        watcher = self._project_fs_watcher
        if watcher is None:
            return
        desired = self._desired_project_watch_dirs()
        current = {self._canonical_path(path) for path in watcher.directories() if isinstance(path, str)}
        remove_paths = sorted(current - desired)
        if remove_paths:
            try:
                watcher.removePaths(remove_paths)
            except Exception:
                pass
        add_paths = sorted(desired - current)
        if add_paths:
            try:
                watcher.addPaths(add_paths)
            except Exception:
                pass
        self._project_fs_watched_dirs = {
            self._canonical_path(path)
            for path in watcher.directories()
            if isinstance(path, str) and path
        }

    def _desired_project_watch_dirs(self) -> set[str]:
        if self.no_project_mode:
            return set()
        root = self._canonical_existing_watch_dir(self.project_root)
        if not root:
            return set()

        watch_dirs: set[str] = {root}

        try:
            with os.scandir(root) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    cpath = self._canonical_existing_watch_dir(entry.path)
                    if not cpath:
                        continue
                    if self._is_tree_path_excluded(cpath, True):
                        continue
                    watch_dirs.add(cpath)
        except Exception:
            pass

        tree = getattr(self, "tree", None)
        if isinstance(tree, FileSystemTreeWidget):
            for path in tree.expanded_paths():
                cpath = self._canonical_existing_watch_dir(path)
                if not cpath:
                    continue
                if cpath != root and self._is_tree_path_excluded(cpath, True):
                    continue
                watch_dirs.add(cpath)
            selected = tree.selected_path()
            if isinstance(selected, str) and selected:
                base = selected if os.path.isdir(selected) else os.path.dirname(selected)
                cbase = self._canonical_existing_watch_dir(base)
                if cbase:
                    watch_dirs.add(cbase)

        filtered: list[str] = []
        for path in watch_dirs:
            if not self._path_has_prefix(path, root):
                continue
            if not os.path.isdir(path):
                continue
            filtered.append(path)

        filtered.sort(key=lambda path: (path.count(os.sep), len(path), path.lower()))
        max_watch_dirs = 600
        if len(filtered) > max_watch_dirs:
            keep = set(filtered[: max_watch_dirs - 1])
            keep.add(root)
            return keep
        return set(filtered)

    def _canonical_existing_watch_dir(self, path: str) -> str:
        cpath = self._canonical_path(path)
        if not self._path_has_prefix(cpath, self.project_root):
            return ""
        if os.path.isdir(cpath):
            return cpath
        parent = cpath
        root = self._canonical_path(self.project_root)
        while parent and parent != root and not os.path.isdir(parent):
            parent = self._canonical_path(os.path.dirname(parent))
        if os.path.isdir(parent) and self._path_has_prefix(parent, root):
            return parent
        if os.path.isdir(root):
            return root
        return ""

    def _maybe_refresh_project_tree_on_focus(self) -> None:
        if self.no_project_mode or not self._startup_done:
            return
        now = time.monotonic()
        if now - float(self._project_fs_focus_refresh_at) < 4.0:
            return
        self._project_fs_focus_refresh_at = now
        if self._project_fs_pending_dirs:
            self._flush_project_fs_refreshes()
            return
        self.refresh_project_tree()
        self._schedule_project_fs_watch_sync()

    def setup_bottom_panels(self):
        features = (
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )

        self.dock_debug = QDockWidget("Debug Output", self)
        self.dock_debug.setObjectName("dock_debug")
        self.dock_debug.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.dock_debug.setFeatures(features)
        self.dock_debug.setMinimumHeight(90)

        self.debug_output = QPlainTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setPlaceholderText("Debug output...")
        self.debug_output.setMinimumHeight(80)
        self.dock_debug.setWidget(self.debug_output)

        self.dock_terminal = QDockWidget("Terminal", self)
        self.dock_terminal.setObjectName("dock_console")
        self.dock_terminal.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.dock_terminal.setFeatures(features)
        self.dock_terminal.setMinimumHeight(90)

        self.console_tabs = QTabWidget(self)
        self.console_tabs.setDocumentMode(True)
        self.console_tabs.setMovable(True)
        self.console_tabs.setTabsClosable(True)
        self.dock_terminal.setWidget(self.console_tabs)

        self.dock_problems = QDockWidget("Problems (0)", self)
        self.dock_problems.setObjectName("dock_problems")
        self.dock_problems.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.dock_problems.setFeatures(features)
        self.dock_problems.setMinimumHeight(90)

        self.problems_panel = ProblemsPanel(self)
        self.problems_panel.problemActivated.connect(self._on_problem_activated)
        self.problems_panel.importSymbolRequested.connect(self._on_problem_import_symbol_requested)
        self.problems_panel.removeUnusedImportRequested.connect(self._on_problem_remove_unused_import_requested)
        self.problems_panel.addTdocSymbolRequested.connect(self._on_problem_add_tdoc_symbol_requested)
        self.problems_panel.capitalizeTdocSectionRequested.connect(self._on_problem_capitalize_tdoc_section_requested)
        self.problems_panel.clearFileRequested.connect(self._on_problem_clear_file_requested)
        self.problems_panel.clearAllRequested.connect(self._on_problem_clear_all_requested)
        self.problems_panel.countChanged.connect(self._on_problem_count_changed)
        self.dock_problems.setWidget(self.problems_panel)

        self.dock_usages = QDockWidget("Usages", self)
        self.dock_usages.setObjectName("dock_usages")
        self.dock_usages.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.dock_usages.setFeatures(features)
        self.dock_usages.setMinimumHeight(90)

        self.usages_panel = UsagesPanel(self)
        self.usages_panel.usagePreviewRequested.connect(self._on_problem_activated)
        self.usages_panel.usageActivated.connect(self._on_problem_activated)
        self.usages_panel.cancelRequested.connect(self.cancel_find_usages)
        self.dock_usages.setWidget(self.usages_panel)

        self.dock_outline = QDockWidget("Outline", self)
        self.dock_outline.setObjectName("dock_outline")
        self.dock_outline.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock_outline.setFeatures(features)
        self.dock_outline.setMinimumWidth(220)

        self.symbol_outline_panel = SymbolOutlinePanel(self)
        self.symbol_outline_panel.symbolActivated.connect(self._on_outline_symbol_activated)
        self.dock_outline.setWidget(self.symbol_outline_panel)
        self.dock_outline.visibilityChanged.connect(self._on_outline_dock_visibility_changed)

        self.console_run_manager = ConsoleRunManager(
            tab_widget=self.console_tabs,
            canonicalize=self._canonical_path,
            resolve_interpreter=self.resolve_interpreter,
            resolve_run_in=self.resolve_run_in,
            run_config_provider=self._run_config,
            terminal_styler=self._style_terminal_widget,
            active_terminal_changed=self._set_active_terminal,
            traceback_activated=self._on_console_traceback_activated,
            parent=self,
        )
        self.console_run_manager.runStateChanged.connect(self._update_toolbar_run_controls)
        self.console_tabs.currentChanged.connect(lambda _idx: self._update_toolbar_run_controls())
        self.console_tabs.tabCloseRequested.connect(self._on_console_tab_close_requested)

        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_debug)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_problems)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_usages)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_outline)
        self.tabifyDockWidget(self.dock_debug, self.dock_terminal)
        self.tabifyDockWidget(self.dock_terminal, self.dock_problems)
        self.tabifyDockWidget(self.dock_problems, self.dock_usages)
        self.dock_outline.hide()
        self.dock_debug.show()

    def _ensure_debug_dock_visible(self) -> None:
        dock = getattr(self, "dock_debug", None)
        if not isinstance(dock, QDockWidget):
            return
        try:
            if dock.isFloating():
                dock.setFloating(False)
        except Exception:
            pass
        try:
            if self.dockWidgetArea(dock) == Qt.NoDockWidgetArea:
                self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        except Exception:
            pass
        dock.show()
        dock.raise_()

    def _bind_status_bar_debug_mirror(self) -> None:
        bar = self.statusBar()
        if bar is None:
            return
        try:
            bar.messageChanged.connect(self._on_status_bar_message_changed)
        except Exception:
            pass

    def _setup_status_bar_widgets(self) -> None:
        bar = self.statusBar()
        if bar is None:
            return
        existing = getattr(self, "_status_git_branch_label", None)
        if isinstance(existing, QLabel):
            try:
                bar.removeWidget(existing)
            except Exception:
                pass
            existing.deleteLater()
        label = QLabel("", bar)
        label.setObjectName("GitBranchStatusLabel")
        label.setVisible(False)
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bar.addPermanentWidget(label)
        self._status_git_branch_label = label
        self._refresh_git_branch_status_label()

    def _on_git_status_changed(self, _file_states: dict, _folder_states: dict, branch: str) -> None:
        self._refresh_git_branch_status_label(branch=branch)

    def _refresh_git_branch_status_label(self, *, branch: str | None = None) -> None:
        label = self._status_git_branch_label
        if not isinstance(label, QLabel):
            return

        current_branch = str(self._git_current_branch if branch is None else branch).strip()
        repo_root = str(
            getattr(getattr(self, "version_control_controller", None), "_git_repo_root", None)
            or self._git_repo_root
            or ""
        ).strip()
        if not repo_root:
            label.clear()
            label.setToolTip("")
            label.hide()
            return

        shown_branch = current_branch or "(detached)"
        label.setText(f"Git: {shown_branch}")
        label.setToolTip(f"{repo_root}")
        label.show()

    def _on_status_bar_message_changed(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        now = time.monotonic()
        if text == self._last_status_debug_message and (now - self._last_status_debug_at) < 0.75:
            return
        self._last_status_debug_message = text
        self._last_status_debug_at = now
        self._append_debug_output_lines([f"[Status] {text}"], reveal=False)

    def _on_language_service_status_message(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if self._should_suppress_verbose_lsp_status(text):
            if not self._lsp_noise_notice_shown:
                self._lsp_noise_notice_shown = True
                self._append_debug_output_lines(
                    [
                        "[LSP] Suppressing verbose clangd info logs.",
                        "[LSP] Enable C/C++ > Log LSP Traffic To Status Bar for full protocol output.",
                    ],
                    reveal=False,
                )
            return
        self.statusBar().showMessage(text, 2500)

    def _should_suppress_verbose_lsp_status(self, text: str) -> bool:
        if self._cpp_verbose_lsp_logging_enabled():
            self._lsp_noise_notice_shown = False
            return False
        lowered = text.lower()
        if text.startswith("[clangd:"):
            return True
        if re.match(r"^[ivd]\[\d{2}:\d{2}:\d{2}\.", text, flags=re.IGNORECASE):
            return True
        return any(
            marker in lowered
            for marker in (
                "astworker building file",
                "file version went from",
                "<-- textdocument/",
                "queued in worker",
                "built preamble",
                "indexing",
            )
        )

    def _cpp_verbose_lsp_logging_enabled(self) -> bool:
        cfg = self._cpp_config()
        return bool(cfg.get("log_lsp_traffic", False))

    def _report_settings_load_errors(self, *, source: str) -> None:
        errors = dict(self.settings_manager.load_errors())
        if not errors:
            self._settings_load_errors = {}
            return

        pending = errors
        if pending == getattr(self, "_settings_load_errors", {}):
            return
        self._settings_load_errors = pending

        lines: list[str] = []
        project_error = pending.get("project")
        if isinstance(project_error, str) and project_error.strip():
            lines.append(
                f"[Settings][project.json] Load error from {source}: {project_error.strip()}"
            )
        ide_error = pending.get("ide")
        if isinstance(ide_error, str) and ide_error.strip():
            lines.append(
                f"[Settings][ide-settings.json] Load error from {source}: {ide_error.strip()}"
            )
        if not lines:
            return

        self._append_debug_output_lines(lines, reveal=True)

    def _append_debug_output_lines(self, lines: list[str], *, reveal: bool = False) -> None:
        debug_output = getattr(self, "debug_output", None)
        if isinstance(debug_output, QPlainTextEdit):
            for line in lines:
                debug_output.appendPlainText(line)
            cursor = debug_output.textCursor()
            cursor.movePosition(QTextCursor.End)
            debug_output.setTextCursor(cursor)
        if reveal:
            self._ensure_debug_dock_visible()

    def setup_menus(self):
        ActionRegistry.create_actions(self)
        self._apply_runtime_keybindings()

    def _toolbar_icon_roots(self) -> list[Path]:
        base = Path(__file__).resolve().parents[1]
        return [
            base / "icons",
            base / "assets" / "icons",
            base / "ui" / "icons",
            base / "resources" / "icons",
        ]

    def _load_toolbar_icon(self, icon_key: str) -> QIcon:
        key = str(icon_key or "").strip()
        if not key:
            return QIcon()

        extensions = (".svg", ".png", ".ico", ".jpg", ".jpeg")
        for root in self._toolbar_icon_roots():
            for ext in extensions:
                candidate = root / f"{key}{ext}"
                if not candidate.is_file():
                    continue
                icon = QIcon(str(candidate))
                if not icon.isNull():
                    return icon

        if key not in self._toolbar_missing_icon_keys:
            self._toolbar_missing_icon_keys.add(key)
            roots = ", ".join(str(path) for path in self._toolbar_icon_roots())
            print(f"[PyTPO] Missing toolbar icon '{key}'. Checked: {roots}. Falling back to text.")
            checked_candidates = [
                str(root / f"{key}{ext}")
                for root in self._toolbar_icon_roots()
                for ext in extensions
            ]
            self._append_debug_output_lines(
                [
                    f"[Toolbar] Missing icon '{key}'. Falling back to text.",
                    "[Toolbar] Checked files:",
                    *[f"  - {path}" for path in checked_candidates],
                ],
                reveal=False,
            )
        return QIcon()

    def _configure_titlebar_button(
        self,
        button: QToolButton,
        *,
        icon_key: str,
        fallback_text: str,
        tooltip: str,
        kind: str,
    ) -> None:
        button.setObjectName("TitleBarToolButton")
        button.setProperty("kind", kind)
        button.setToolTip(tooltip)
        button.setFixedHeight(34)
        button.setCursor(Qt.PointingHandCursor)
        icon = self._load_toolbar_icon(icon_key)
        icon_loaded = not icon.isNull()
        button.setProperty("icon_loaded", icon_loaded)
        if icon_loaded:
            icon_size = QSize(20, 20)
            if kind == "stop":
                button.setFixedWidth(46)
            else:
                button.setFixedWidth(40)
            button.setText("")
            button.setIcon(icon)
            button.setIconSize(icon_size)
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            if kind == "stop":
                button.setMinimumWidth(70)
            elif kind == "build-run":
                button.setMinimumWidth(96)
            else:
                button.setMinimumWidth(60)
            button.setIconSize(QSize())
            button.setIcon(QIcon())
            button.setText(fallback_text)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)

    def _setup_titlebar_toolbar_controls(self) -> None:
        if self._toolbar_controls_host is not None:
            return

        host = QWidget(self)
        host.setObjectName("TitleBarToolsHost")
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)

        run_btn = QToolButton(host)
        self._configure_titlebar_button(
            run_btn,
            icon_key="run",
            fallback_text="Run",
            tooltip="Run active target (selected config or current file)",
            kind="run",
        )
        run_menu = QMenu(run_btn)
        run_menu.aboutToShow.connect(self.populate_toolbar_python_run_menu)
        run_btn.setMenu(run_menu)
        run_btn.setPopupMode(QToolButton.MenuButtonPopup)
        run_btn.clicked.connect(self.run_primary_python_target)

        build_btn = QToolButton(host)
        self._configure_titlebar_button(
            build_btn,
            icon_key="build",
            fallback_text="Build",
            tooltip="Build Current File (Ctrl+Shift+B)",
            kind="build",
        )
        build_btn.clicked.connect(self.build_current_file)

        build_run_btn = QToolButton(host)
        self._configure_titlebar_button(
            build_run_btn,
            icon_key="build-run",
            fallback_text="Build+Run",
            tooltip="Build + Run Current File (Ctrl+Shift+F5)",
            kind="build-run",
        )
        build_run_btn.clicked.connect(self.build_and_run_current_file)

        tdoc_index_btn = QToolButton(host)
        self._configure_titlebar_button(
            tdoc_index_btn,
            icon_key="index",
            fallback_text="Index",
            tooltip="Build TDOC Index",
            kind="build",
        )
        tdoc_index_btn.clicked.connect(self.build_open_tdoc_indexes)

        stop_btn = QToolButton(host)
        self._configure_titlebar_button(
            stop_btn,
            icon_key="stop",
            fallback_text="Stop",
            tooltip="Stop Current Run (Shift+F5)",
            kind="stop",
        )
        stop_menu = QMenu(stop_btn)
        stop_menu.aboutToShow.connect(self._rebuild_toolbar_stop_menu)
        stop_btn.clicked.connect(self._toolbar_stop_clicked)

        settings_btn = QToolButton(host)
        self._configure_titlebar_button(
            settings_btn,
            icon_key="settings",
            fallback_text="Settings",
            tooltip="Open Settings",
            kind="settings",
        )
        settings_btn.clicked.connect(self.open_settings)

        host_layout.addWidget(run_btn)
        host_layout.addWidget(build_btn)
        host_layout.addWidget(build_run_btn)
        host_layout.addWidget(tdoc_index_btn)
        host_layout.addWidget(stop_btn)
        host_layout.addWidget(settings_btn)

        self._toolbar_controls_host = host
        self._toolbar_build_btn = build_btn
        self._toolbar_build_run_btn = build_run_btn
        self._toolbar_tdoc_index_btn = tdoc_index_btn
        self._toolbar_run_btn = run_btn
        self._toolbar_run_menu = run_menu
        self._toolbar_stop_btn = stop_btn
        self._toolbar_stop_menu = stop_menu
        self._toolbar_settings_btn = settings_btn

        self.add_window_right_control(host)

        ai_chk = QCheckBox("AI Assist", self)
        ai_chk.setObjectName("TitleBarAiAssistToggle")
        ai_chk.setFixedHeight(28)
        ai_chk.setToolTip("Enable AI inline assist")
        ai_chk.toggled.connect(self._on_titlebar_ai_toggle_changed)
        self._toolbar_ai_checkbox = ai_chk
        self.add_window_right_control(ai_chk)

        self._sync_titlebar_ai_toggle()
        self._apply_custom_toolbar_window_settings()

    def _running_script_sessions(self):
        return self.execution_controller._running_script_sessions()

    def _stop_running_session(self, file_key: str) -> None:
        self.execution_controller._stop_running_session(file_key)

    def _stop_all_running_sessions(self) -> None:
        self.execution_controller._stop_all_running_sessions()

    def _rebuild_toolbar_stop_menu(self) -> None:
        self.execution_controller._rebuild_toolbar_stop_menu()

    def _toolbar_stop_clicked(self) -> None:
        self.execution_controller._toolbar_stop_clicked()

    def _has_project_loaded(self) -> bool:
        return not self.no_project_mode

    def _has_runnable_target(self) -> bool:
        if not self._has_project_loaded():
            return False
        current = self.current_editor()
        if isinstance(current, EditorWidget):
            return True
        if self.console_run_manager and self.console_run_manager.active_file_key():
            return True
        return False

    def _bind_global_action_recompute_hooks(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.focusChanged.connect(self._on_global_focus_changed)
        except Exception:
            return

    def _on_global_focus_changed(self, _old, _new) -> None:
        self._refresh_runtime_action_states()
        self._schedule_symbol_outline_refresh(immediate=True)

    def _bind_editor_tab_action_hooks(self) -> None:
        for tabs in self.editor_workspace.all_tabs():
            if not isinstance(tabs, QTabWidget):
                continue
            if bool(tabs.property("_runtime_action_hooked")):
                continue
            tabs.currentChanged.connect(self._on_editor_tabs_current_changed)
            tabs.setProperty("_runtime_action_hooked", True)

    def _on_editor_tabs_current_changed(self, _idx: int) -> None:
        self._refresh_runtime_action_states()
        self._schedule_symbol_outline_refresh(immediate=True)

    def _on_outline_dock_visibility_changed(self, visible: bool) -> None:
        if bool(visible):
            self._schedule_symbol_outline_refresh(immediate=True)
            return
        self._outline_refresh_timer.stop()

    def _schedule_symbol_outline_refresh(self, *, immediate: bool = False) -> None:
        dock = self.dock_outline
        if not isinstance(dock, QDockWidget):
            return
        if not dock.isVisible():
            self._outline_refresh_timer.stop()
            return
        if immediate:
            self._outline_refresh_timer.start(0)
            return
        self._outline_refresh_timer.start()

    def _clear_symbol_outline_panel(self, message: str) -> None:
        panel = self.symbol_outline_panel
        if isinstance(panel, SymbolOutlinePanel):
            panel.clear_outline(str(message or "No symbols."))
        self._outline_last_editor_id = ""
        self._outline_last_revision = -1
        self._outline_active_token = 0

    def _refresh_symbol_outline_panel(self) -> None:
        panel = self.symbol_outline_panel
        if not isinstance(panel, SymbolOutlinePanel):
            return
        dock = self.dock_outline
        if not isinstance(dock, QDockWidget) or not dock.isVisible():
            return

        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self._clear_symbol_outline_panel("No active editor.")
            return

        language_id = str(self.language_intelligence_controller._editor_language_id(ed) or "").strip().lower()
        if language_id not in {"python", "c", "cpp", "rust"}:
            self._clear_symbol_outline_panel("No outline for this file type.")
            return

        editor_id = self._editor_lookup_id(ed)
        try:
            revision = int(ed.document().revision())
        except Exception:
            revision = -1
        if editor_id == self._outline_last_editor_id and revision == self._outline_last_revision:
            return

        file_path = str(getattr(ed, "file_path", "") or "").strip()
        normalized_path = self._canonical_path(file_path) if file_path else ""
        if language_id == "rust":
            self._outline_request_token += 1
            token = int(self._outline_request_token)
            self._outline_active_token = token

            def _on_outline(symbols_obj: object, error: str) -> None:
                if token != int(self._outline_active_token):
                    return
                current = self.current_editor()
                if not isinstance(current, EditorWidget):
                    return
                if self._editor_lookup_id(current) != editor_id:
                    return
                try:
                    if int(current.document().revision()) != revision:
                        return
                except Exception:
                    return
                symbols = symbols_obj if isinstance(symbols_obj, list) else []
                panel.set_outline(file_path=normalized_path, symbols=symbols, error=str(error or ""))
                self._outline_last_editor_id = editor_id
                self._outline_last_revision = revision

            self.rust_language_pack.request_outline_symbols(
                file_path=normalized_path,
                source_text=ed.toPlainText(),
                callback=_on_outline,
            )
            return

        symbols, parse_error = build_document_outline(
            file_path=normalized_path,
            source_text=ed.toPlainText(),
            language_id=language_id,
        )
        panel.set_outline(file_path=normalized_path, symbols=symbols, error=parse_error)
        self._outline_last_editor_id = editor_id
        self._outline_last_revision = revision

    def _on_outline_symbol_activated(self, file_path: str, line: int, column: int) -> None:
        cpath = self._canonical_path(file_path) if str(file_path or "").strip() else ""
        line_num = max(1, int(line or 1))
        col_num = max(1, int(column or 1))
        if cpath and os.path.isfile(cpath):
            self._navigate_to_location(cpath, line_num, col_num)
            return

        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            return
        block = ed.document().findBlockByNumber(line_num - 1)
        if not block.isValid():
            block = ed.document().lastBlock()
        cursor = QTextCursor(block)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.MoveAnchor,
            max(0, col_num - 1),
        )
        ed.setTextCursor(cursor)
        ed.centerCursor()
        self._focus_editor(ed)

    def _dock_state_targets(self) -> list[QDockWidget]:
        docks = [
            self.dock_project,
            self.dock_debug,
            self.dock_terminal,
            self.dock_problems,
            self.dock_usages,
            self.dock_outline,
        ]
        return [dock for dock in docks if isinstance(dock, QDockWidget)]

    @staticmethod
    def _qbytearray_to_b64_text(value: QByteArray) -> str:
        if not isinstance(value, QByteArray) or value.isEmpty():
            return ""
        try:
            return bytes(value.toBase64()).decode("ascii", errors="ignore")
        except Exception:
            return ""

    @staticmethod
    def _qbytearray_from_b64_text(value: object) -> QByteArray:
        text = str(value or "").strip()
        if not text:
            return QByteArray()
        try:
            decoded = QByteArray.fromBase64(text.encode("ascii", errors="ignore"))
            return decoded if isinstance(decoded, QByteArray) else QByteArray()
        except Exception:
            return QByteArray()

    def _window_layout_settings(self) -> dict:
        raw = self.settings_manager.get("window.layout", scope_preference="ide", default={})
        return dict(raw) if isinstance(raw, dict) else {}

    def _restore_window_and_dock_layout(self) -> None:
        cfg = self._window_layout_settings()

        geometry_b64 = cfg.get("geometry_b64")
        geometry = self._qbytearray_from_b64_text(geometry_b64)
        geometry_restored = False
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            try:
                geometry_restored = bool(self.restoreGeometry(geometry))
            except Exception:
                geometry_restored = False
        self._window_geometry_restored = geometry_restored

        dock_state_restored = False
        if not self.no_project_mode:
            state_b64 = cfg.get("dock_state_b64")
            state = self._qbytearray_from_b64_text(state_b64)
            if isinstance(state, QByteArray) and not state.isEmpty():
                try:
                    dock_state_restored = bool(self.restoreState(state))
                except Exception:
                    dock_state_restored = False

            visibility_raw = cfg.get("dock_visibility")
            if isinstance(visibility_raw, dict):
                dock_by_name: dict[str, QDockWidget] = {}
                for dock in self._dock_state_targets():
                    name = str(dock.objectName() or "").strip()
                    if name:
                        dock_by_name[name] = dock
                for key, visible in visibility_raw.items():
                    dock = dock_by_name.get(str(key or "").strip())
                    if dock is None:
                        continue
                    try:
                        dock.setVisible(bool(visible))
                    except Exception:
                        continue

        self._dock_layout_restored = dock_state_restored

    def _persist_window_and_dock_layout(self) -> None:
        cfg = self._window_layout_settings()
        try:
            cfg["geometry_b64"] = self._qbytearray_to_b64_text(self.saveGeometry())
        except Exception:
            pass

        if not self.no_project_mode:
            try:
                cfg["dock_state_b64"] = self._qbytearray_to_b64_text(self.saveState())
            except Exception:
                pass
            cfg["dock_visibility"] = {
                str(dock.objectName() or ""): bool(dock.isVisible())
                for dock in self._dock_state_targets()
                if str(dock.objectName() or "").strip()
            }

        self.settings_manager.set("window.layout", cfg, "ide")
        try:
            self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

    def _enter_no_project_ui_mode(self, *, save_snapshot: bool = True) -> None:
        if save_snapshot and self._welcome_saved_state is None:
            try:
                self._welcome_saved_state = self.saveState()
            except Exception:
                self._welcome_saved_state = None
            self._welcome_saved_visibility = {
                str(dock.objectName() or id(dock)): bool(dock.isVisible())
                for dock in self._dock_state_targets()
            }
        for dock in self._dock_state_targets():
            dock.hide()
        for action in self._panel_toggle_actions:
            action.setEnabled(False)
        if self._center_stack is not None and self._welcome_view is not None:
            self._center_stack.setCurrentWidget(self._welcome_view)

    def _leave_no_project_ui_mode(self) -> None:
        for action in self._panel_toggle_actions:
            action.setEnabled(True)
        if self._center_stack is not None:
            self._center_stack.setCurrentWidget(self.editor_workspace)
        restored = False
        if isinstance(self._welcome_saved_state, QByteArray) and not self._welcome_saved_state.isEmpty():
            try:
                restored = bool(self.restoreState(self._welcome_saved_state))
            except Exception:
                restored = False
        if not restored:
            for dock in self._dock_state_targets():
                key = str(dock.objectName() or id(dock))
                dock.setVisible(bool(self._welcome_saved_visibility.get(key, False)))

    def _refresh_runtime_action_states(self, *, running_count: int | None = None) -> None:
        self._bind_editor_tab_action_hooks()
        if running_count is None:
            running_count = len(self._running_script_sessions())

        project_loaded = self._has_project_loaded()
        active_editor = self.current_editor()
        has_active_editor = isinstance(active_editor, EditorWidget)
        has_selection = bool(has_active_editor and active_editor.textCursor().hasSelection())
        has_python_run_configs = bool(project_loaded and self.execution_controller.has_python_run_configs())
        has_active_python_run_config = bool(
            project_loaded and self.execution_controller.active_python_run_config_name()
        )
        has_rust_run_configs = bool(project_loaded and self.execution_controller.has_rust_run_configs())
        has_active_rust_run_config = bool(
            project_loaded and self.execution_controller.active_rust_run_config_name()
        )
        extract_language = (
            str(self.language_intelligence_controller._effective_refactor_language(active_editor) or "").strip().lower()
            if has_active_editor
            else ""
        )
        can_extract_variable = bool(has_selection and extract_language in {"python", "c", "cpp"})
        can_extract_method = bool(has_selection and extract_language in {"python", "cpp"})
        has_runnable = self._has_runnable_target()
        has_buildable = bool(project_loaded and self.execution_controller.can_build_current_file())
        has_build_and_run = bool(project_loaded and self.execution_controller.can_build_and_run_current_file())
        has_open_tdoc = bool(project_loaded and self._open_tdoc_roots())

        if self._act_build_current is not None:
            self._act_build_current.setEnabled(has_buildable)
            self._act_build_current.setVisible(has_buildable)
        if self._act_build_and_run_current is not None:
            self._act_build_and_run_current.setEnabled(has_build_and_run)
            self._act_build_and_run_current.setVisible(has_build_and_run)
        if self._act_run_current is not None:
            self._act_run_current.setEnabled(
                project_loaded and (has_active_editor or has_active_python_run_config or has_active_rust_run_config)
            )
        if self._act_rerun_current is not None:
            self._act_rerun_current.setEnabled(project_loaded and (has_runnable or running_count > 0))
        if self._act_stop_current is not None:
            self._act_stop_current.setEnabled(project_loaded and running_count > 0)
        if self._act_new_terminal is not None:
            self._act_new_terminal.setEnabled(project_loaded)
        if self._act_close_terminal is not None:
            has_console_tabs = bool(self.console_tabs is not None and self.console_tabs.count() > 0)
            self._act_close_terminal.setEnabled(project_loaded and has_console_tabs)
        if self._act_close_project is not None:
            self._act_close_project.setEnabled(project_loaded)
        if self._act_format_file is not None:
            self._act_format_file.setEnabled(has_active_editor)
        if self._act_format_selection is not None:
            self._act_format_selection.setEnabled(has_active_editor)
        if self._act_rename_symbol is not None:
            self._act_rename_symbol.setEnabled(has_active_editor)
        if self._act_extract_variable is not None:
            self._act_extract_variable.setEnabled(can_extract_variable)
        if self._act_extract_method is not None:
            self._act_extract_method.setEnabled(can_extract_method)

        if self._toolbar_run_btn is not None:
            can_toolbar_run = bool(
                project_loaded
                and (
                    has_active_editor
                    or (has_python_run_configs and has_active_python_run_config)
                    or (has_rust_run_configs and has_active_rust_run_config)
                )
            )
            self._toolbar_run_btn.setEnabled(can_toolbar_run)
        if self._toolbar_build_btn is not None:
            self._toolbar_build_btn.setEnabled(has_buildable)
            self._toolbar_build_btn.setVisible(has_buildable)
        if self._toolbar_build_run_btn is not None:
            self._toolbar_build_run_btn.setEnabled(has_build_and_run)
            self._toolbar_build_run_btn.setVisible(has_build_and_run)
        if self._toolbar_tdoc_index_btn is not None:
            self._toolbar_tdoc_index_btn.setEnabled(has_open_tdoc)
            self._toolbar_tdoc_index_btn.setVisible(has_open_tdoc)
        if self._run_build_config_menu is not None:
            show_build_menu = bool(project_loaded and (has_buildable or has_build_and_run))
            self._run_build_config_menu.menuAction().setVisible(show_build_menu)
        if self._toolbar_controls_host is not None:
            self._toolbar_controls_host.setVisible(project_loaded)
        if self._toolbar_ai_checkbox is not None:
            self._toolbar_ai_checkbox.setVisible(project_loaded)

    def _update_toolbar_run_controls(self) -> None:
        self.execution_controller._update_toolbar_run_controls()

    # ---------- Linting ----------

    def _lint_config(self) -> dict:
        cfg = self.config.get("lint", {})
        return cfg if isinstance(cfg, dict) else {}

    def _lint_visual_config(self) -> dict:
        lint_cfg = self._lint_config()
        visuals = lint_cfg.get("visuals", {}) if isinstance(lint_cfg, dict) else {}
        return visuals if isinstance(visuals, dict) else {}

    def _lint_follow_symlinks(self) -> bool:
        indexing = self._indexing_config()
        return bool(indexing.get("follow_symlinks", False))

    def _completion_config(self) -> dict:
        cfg = self.config.get("completion", {})
        return cfg if isinstance(cfg, dict) else {}

    def _cpp_config(self) -> dict:
        cfg = self.config.get("c_cpp", {})
        return cfg if isinstance(cfg, dict) else {}

    def _rust_config(self) -> dict:
        cfg = self.config.get("rust", {})
        return cfg if isinstance(cfg, dict) else {}

    def _keybindings_config(self) -> dict[str, dict[str, list[str]]]:
        return normalize_keybindings(
            self.settings_manager.get("keybindings", scope_preference="ide", default={})
        )

    def _apply_runtime_keybindings(self) -> None:
        bindings = self._keybindings_config()
        ActionRegistry.apply_keybindings(self)
        try:
            EditorWidget.set_default_keybindings(bindings)
        except Exception:
            pass

        tree_copy = getattr(self, "_act_tree_copy", None)
        if isinstance(tree_copy, QAction):
            tree_copy.setShortcut(
                ",".join(get_action_sequence(bindings, scope="general", action_id="action.tree_copy"))
            )
        tree_cut = getattr(self, "_act_tree_cut", None)
        if isinstance(tree_cut, QAction):
            tree_cut.setShortcut(
                ",".join(get_action_sequence(bindings, scope="general", action_id="action.tree_cut"))
            )
        tree_paste = getattr(self, "_act_tree_paste", None)
        if isinstance(tree_paste, QAction):
            tree_paste.setShortcut(
                ",".join(get_action_sequence(bindings, scope="general", action_id="action.tree_paste"))
            )
        tree_rename = getattr(self, "_act_tree_rename", None)
        if isinstance(tree_rename, QAction):
            tree_rename.setShortcut(
                ",".join(get_action_sequence(bindings, scope="general", action_id="action.rename_symbol"))
            )
        tree_delete = getattr(self, "_act_tree_delete", None)
        if isinstance(tree_delete, QAction):
            tree_delete.setShortcut(
                ",".join(get_action_sequence(bindings, scope="general", action_id="action.tree_delete"))
            )

        for ed in self.editor_workspace.all_editors():
            if not isinstance(ed, EditorWidget):
                continue
            try:
                ed.configure_keybindings(bindings)
            except Exception:
                pass

    def register_language_provider(
        self,
        provider: object,
        *,
        language_ids=None,
        default: bool = False,
    ) -> None:
        self.language_service_hub.register_provider(
            provider,
            language_ids=language_ids,
            default=default,
        )
        updater = getattr(provider, "update_settings", None)
        if callable(updater):
            updater(self._completion_config())

    def _ai_assist_config(self) -> dict:
        cfg = self.config.get("ai_assist", {})
        if isinstance(cfg, dict):
            return normalize_ai_settings(cfg)
        return normalize_ai_settings({})

    def _git_config(self) -> dict:
        cfg = self.config.get("git", {})
        return cfg if isinstance(cfg, dict) else {}

    def _git_tinting_enabled(self) -> bool:
        return bool(self._git_config().get("enable_file_tinting", True))

    def _git_tint_colors(self) -> dict[str, str]:
        cfg = self._git_config()
        return {
            "clean": str(cfg.get("tracked_clean_color") or "#7fbf7f"),
            "dirty": str(cfg.get("tracked_dirty_color") or "#e69f6b"),
            "untracked": str(cfg.get("untracked_color") or "#c8c8c8"),
        }

    def _apply_git_tinting_config(self) -> None:
        self.version_control_controller._apply_git_tinting_config()

    def _configure_git_poll_timer(self) -> None:
        self.version_control_controller._configure_git_poll_timer()

    def schedule_git_status_refresh(self, *, delay_ms: int = 320, force: bool = False) -> None:
        self.version_control_controller.schedule_git_status_refresh(delay_ms=delay_ms, force=force)

    def _request_git_status_refresh(self, force: bool = False) -> None:
        self.version_control_controller._request_git_status_refresh(force=force)

    def _submit_git_task(self, kind: str, fn, context: object | None = None) -> None:
        self.version_control_controller._submit_git_task(kind, fn, context=context)

    def _drain_git_tasks(self) -> None:
        self.version_control_controller._drain_git_tasks()

    def _handle_git_task_result(self, kind: str, context: object | None, result: object, error: Exception | None) -> None:
        self.version_control_controller._handle_git_task_result(kind, context, result, error)

    def _repo_root_for_path(self, path: str) -> str | None:
        return self.version_control_controller._repo_root_for_path(path)

    def _ensure_git_repo(self) -> str | None:
        return self.version_control_controller._ensure_git_repo()

    def _is_python_file_path(self, file_path: str | None) -> bool:
        if not isinstance(file_path, str):
            return False
        return file_path.lower().endswith(self.PYTHON_SOURCE_SUFFIXES)

    def _apply_completion_ui_settings_to_editor(self, ed: EditorWidget):
        if not isinstance(ed, EditorWidget):
            return
        try:
            ed.update_completion_ui_settings(self._completion_config())
        except Exception:
            pass

    def _apply_lint_visual_settings_to_editor(self, ed: EditorWidget):
        if not isinstance(ed, EditorWidget):
            return
        try:
            ed.update_lint_visual_settings(self._lint_visual_config())
        except Exception:
            pass

    def _clamp_editor_font_size(self, value: int) -> int:
        return max(self.FONT_SIZE_MIN, min(self.FONT_SIZE_MAX, int(value)))

    def _resolve_editor_font_family(self, family: object) -> str:
        preferred = str(family or "").strip()
        families = set(QFontDatabase.families())
        if preferred and preferred in families:
            return preferred
        for candidate in self.EDITOR_FONT_FALLBACKS:
            if candidate in families:
                return candidate
        return preferred or "Monospace"

    def _apply_editor_font_settings_to_all(self) -> None:
        self.editor_workspace.set_editor_font_defaults(
            font_size=int(self.font_size),
            font_family=str(self.font_family or "").strip(),
        )
        for widget in self.editor_workspace.all_document_widgets():
            setter = getattr(widget, "set_editor_font_preferences", None)
            if not callable(setter):
                continue
            try:
                setter(
                    family=str(self.font_family or "").strip(),
                    point_size=int(self.font_size),
                )
            except Exception:
                pass

    def _set_editor_font_size(self, size: int, *, persist: bool = True, announce: bool = False) -> None:
        new_size = self._clamp_editor_font_size(size)
        if int(new_size) == int(self.font_size):
            return
        self.font_size = int(new_size)
        self._apply_editor_font_settings_to_all()
        self.settings_manager.set("font_size", int(self.font_size), "ide")
        if persist:
            try:
                self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
            except Exception:
                pass
        self.config = self.settings_manager.export_legacy_config()
        if announce:
            self.statusBar().showMessage(f"Editor font size: {self.font_size}", 1300)

    def _set_editor_font_family(self, family: object, *, persist: bool = True, announce: bool = False) -> None:
        new_family = self._resolve_editor_font_family(family)
        if str(new_family) == str(self.font_family):
            return
        self.font_family = new_family
        self._apply_editor_font_settings_to_all()
        self.settings_manager.set("font_family", str(self.font_family), "ide")
        if persist:
            try:
                self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
            except Exception:
                pass
        self.config = self.settings_manager.export_legacy_config()
        if announce:
            self.statusBar().showMessage(f"Editor font: {self.font_family}", 1300)

    def _editor_background_config(self) -> dict:
        cfg = self.settings_manager.get("editor", scope_preference="ide", default={})
        return cfg if isinstance(cfg, dict) else {}

    @staticmethod
    def _normalize_word_wrap_file_type_key(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if text.startswith("."):
            if "/" in text or "\\" in text or text == "." or len(text) < 2:
                return ""
            return text
        if text.startswith("lang:"):
            lang = text[5:].strip()
            if not lang:
                return ""
            if not re.match(r"^[a-z0-9_+\-]+$", lang):
                return ""
            return f"lang:{lang}"
        return ""

    def _load_word_wrap_enabled_file_types(self) -> set[str]:
        raw = self.settings_manager.get(
            "editor.word_wrap_enabled_file_types",
            scope_preference="ide",
            default=[],
        )
        out: set[str] = set()
        if not isinstance(raw, list):
            return out
        for item in raw:
            key = self._normalize_word_wrap_file_type_key(item)
            if key:
                out.add(key)
        return out

    def _save_word_wrap_enabled_file_types(self) -> None:
        ordered = sorted(self._word_wrap_enabled_file_types)
        self.settings_manager.set("editor.word_wrap_enabled_file_types", ordered, "ide")
        try:
            self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

    def _word_wrap_file_type_key(self, *, file_path: str | None, language_id: str | None) -> str:
        path_text = str(file_path or "").strip()
        if path_text:
            suffix = Path(path_text).suffix.lower()
            if suffix:
                return suffix
        lang = str(language_id or "").strip().lower() or "plaintext"
        return f"lang:{lang}"

    def _word_wrap_enabled_for_editor(self, ed: EditorWidget) -> bool:
        key = self._word_wrap_file_type_key(
            file_path=getattr(ed, "file_path", None),
            language_id=ed.language_id() if hasattr(ed, "language_id") else "plaintext",
        )
        return key in self._word_wrap_enabled_file_types

    def _apply_word_wrap_to_editor(self, ed: EditorWidget) -> None:
        if not isinstance(ed, EditorWidget):
            return
        enabled = self._word_wrap_enabled_for_editor(ed)
        setter = getattr(ed, "set_word_wrap_enabled", None)
        if not callable(setter):
            return
        try:
            setter(enabled)
        except Exception:
            pass

    def _apply_word_wrap_to_matching_editors(self, *, type_key: str, enabled: bool) -> None:
        for candidate in self.editor_workspace.all_editors():
            if not isinstance(candidate, EditorWidget):
                continue
            candidate_key = self._word_wrap_file_type_key(
                file_path=getattr(candidate, "file_path", None),
                language_id=candidate.language_id() if hasattr(candidate, "language_id") else "plaintext",
            )
            if candidate_key != type_key:
                continue
            setter = getattr(candidate, "set_word_wrap_enabled", None)
            if callable(setter):
                try:
                    setter(enabled)
                except Exception:
                    pass

    def _set_word_wrap_enabled_for_type_key(self, type_key: str, enabled: bool) -> bool:
        key = self._normalize_word_wrap_file_type_key(type_key)
        if not key:
            return False
        before = set(self._word_wrap_enabled_file_types)
        if enabled:
            self._word_wrap_enabled_file_types.add(key)
        else:
            self._word_wrap_enabled_file_types.discard(key)
        if before == self._word_wrap_enabled_file_types:
            return False
        self._save_word_wrap_enabled_file_types()
        return True

    def _apply_editor_background_to_editor(self, ed: object) -> None:
        setter = getattr(ed, "set_editor_background", None)
        if not callable(setter):
            return
        cfg = self._editor_background_config()
        try:
            setter(
                background_color=str(cfg.get("background_color", "#252526") or "#252526"),
                background_image_path=str(cfg.get("background_image_path", "") or ""),
                background_image_scale_mode=str(cfg.get("background_image_scale_mode", "stretch") or "stretch"),
                background_image_brightness=int(cfg.get("background_image_brightness", 100)),
                background_tint_color=str(cfg.get("background_tint_color", "#000000") or "#000000"),
                background_tint_strength=int(cfg.get("background_tint_strength", 0)),
            )
        except Exception:
            pass

    def _attach_all_editor_lint_hooks(self):
        self.diagnostics_controller._attach_all_editor_lint_hooks()

    def _attach_editor_lint_hooks(self, ed: EditorWidget):
        self.diagnostics_controller._attach_editor_lint_hooks(ed)
        self._apply_word_wrap_to_editor(ed)
        self._attach_editor_cpp_hooks(ed)
        self._attach_editor_rust_hooks(ed)

    def _on_editor_document_changed(self, ed: EditorWidget):
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if ed is self.current_editor():
            self._schedule_symbol_outline_refresh()
        if ed.file_path:
            self._record_ai_recent_file(ed.file_path)
            self._notify_cpp_document_changed(ed)
            self._notify_rust_document_changed(ed)
        if not ed.file_path:
            ed.clear_lint_diagnostics()
        elif self._is_python_file_path(ed.file_path):
            self._request_lint_for_editor(ed, reason="idle", include_source_if_modified=True)
        elif self._is_tdoc_related_path(ed.file_path):
            self._schedule_tdoc_validation(ed.file_path)
        self._request_completion_for_editor(ed, reason="auto")
        self._request_ai_inline_for_editor(ed, reason="passive")

    def _on_editor_text_changed_for_autosave(self, ed_ref):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        self._schedule_autosave()

    def _on_editor_completion_requested(self, ed_ref, reason: str):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        ed.clear_inline_suggestion()
        self._request_completion_for_editor(ed, reason=reason or "manual")

    def _on_editor_ai_assist_requested(self, ed_ref, reason: str):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        self._request_ai_inline_for_editor(ed, reason=reason or "manual")

    def _diagnostics_for_editor_line(self, ed: EditorWidget, line_num: int) -> list[dict]:
        return self.diagnostics_controller._diagnostics_for_editor_line(ed, line_num)

    def _on_editor_context_menu_about_to_show(self, ed_ref, menu_obj: object, payload_obj: object):
        self.diagnostics_controller._on_editor_context_menu_about_to_show(ed_ref, menu_obj, payload_obj)

    def _on_editor_word_wrap_preference_changed(self, ed_ref, payload_obj: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        enabled = bool(payload.get("enabled", False))
        file_path = str(payload.get("file_path") or getattr(ed, "file_path", "") or "")
        language_id = str(payload.get("language_id") or ed.language_id() or "plaintext").strip().lower()
        type_key = self._word_wrap_file_type_key(file_path=file_path, language_id=language_id)
        changed = self._set_word_wrap_enabled_for_type_key(type_key, enabled)
        self._apply_word_wrap_to_matching_editors(type_key=type_key, enabled=enabled)
        if changed:
            human = type_key
            if type_key.startswith("lang:"):
                human = type_key.split(":", 1)[1]
            mode = "ON" if enabled else "OFF"
            self.statusBar().showMessage(f"Word wrap {mode} for file type {human}", 2200)

    def _append_import_fix_actions_to_menu(self, parent_menu: QMenu, ed_ref, symbol: str, candidates: list[dict]) -> None:
        self.diagnostics_controller._append_import_fix_actions_to_menu(parent_menu, ed_ref, symbol, candidates)

    def _apply_import_candidate_from_context_menu(self, ed_ref, candidate_obj: object, symbol: str):
        self.diagnostics_controller._apply_import_candidate_from_context_menu(ed_ref, candidate_obj, symbol)

    def _apply_remove_unused_import_from_context_menu(self, ed_ref, diag_obj: object) -> None:
        self.diagnostics_controller._apply_remove_unused_import_from_context_menu(ed_ref, diag_obj)

    def _editor_lookup_id(self, ed: EditorWidget) -> str:
        return self.language_intelligence_controller._editor_lookup_id(ed)

    def _next_completion_token(self) -> int:
        return self.language_intelligence_controller._next_completion_token()

    def _next_signature_token(self) -> int:
        return self.language_intelligence_controller._next_signature_token()

    def _next_definition_token(self) -> int:
        return self.language_intelligence_controller._next_definition_token()

    def _next_usages_token(self) -> int:
        return self.language_intelligence_controller._next_usages_token()

    def _completion_target_path(self, ed: EditorWidget) -> str:
        return self.language_intelligence_controller._completion_target_path(ed)

    def _request_completion_for_editor(self, ed: EditorWidget, reason: str = "auto"):
        self.language_intelligence_controller._request_completion_for_editor(ed, reason=reason)

    def _record_ai_recent_file(self, file_path: str | None) -> None:
        self.language_intelligence_controller._record_ai_recent_file(file_path)

    def _request_ai_inline_for_editor(self, ed: EditorWidget, reason: str = "manual") -> None:
        self.language_intelligence_controller._request_ai_inline_for_editor(ed, reason=reason)

    def _on_ai_inline_suggestion_ready(self, payload_obj: object) -> None:
        self.language_intelligence_controller._on_ai_inline_suggestion_ready(payload_obj)

    def _on_editor_signature_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_signature_requested(ed_ref, payload)

    def _on_editor_definition_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_definition_requested(ed_ref, payload)

    def _on_editor_usages_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_usages_requested(ed_ref, payload)

    def _on_editor_rename_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_rename_requested(ed_ref, payload)

    def _on_editor_quick_fix_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_quick_fix_requested(ed_ref, payload)

    def _on_editor_extract_variable_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_extract_variable_requested(ed_ref, payload)

    def _on_editor_extract_method_requested(self, ed_ref, payload: object):
        self.language_intelligence_controller._on_editor_extract_method_requested(ed_ref, payload)

    def _attach_editor_cpp_hooks(self, ed: EditorWidget) -> None:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        editor_id = self._editor_lookup_id(ed)
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            self.cpp_language_pack.on_editor_detached(editor_id)
            return
        cpath = self._canonical_path(file_path)
        self.cpp_language_pack.on_editor_attached(
            editor_id=editor_id,
            file_path=cpath,
            source_text=ed.toPlainText(),
            language_id=self.language_intelligence_controller._editor_language_id(ed),
        )

    def _attach_editor_rust_hooks(self, ed: EditorWidget) -> None:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        editor_id = self._editor_lookup_id(ed)
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            self.rust_language_pack.on_editor_detached(editor_id)
            return
        cpath = self._canonical_path(file_path)
        self.rust_language_pack.on_editor_attached(
            editor_id=editor_id,
            file_path=cpath,
            source_text=ed.toPlainText(),
            language_id=self.language_intelligence_controller._editor_language_id(ed),
        )

    def _notify_cpp_document_changed(self, ed: EditorWidget) -> None:
        if not isinstance(ed, EditorWidget):
            return
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            return
        cpath = self._canonical_path(file_path)
        if not self.cpp_language_pack.supports_file(cpath):
            return
        self.cpp_language_pack.on_document_changed(
            file_path=cpath,
            source_text=ed.toPlainText(),
        )

    def _notify_rust_document_changed(self, ed: EditorWidget) -> None:
        if not isinstance(ed, EditorWidget):
            return
        file_path = getattr(ed, "file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            return
        cpath = self._canonical_path(file_path)
        if not self.rust_language_pack.supports_file(cpath):
            return
        self.rust_language_pack.on_document_changed(
            file_path=cpath,
            source_text=ed.toPlainText(),
        )

    def _on_cpp_file_diagnostics_updated(self, file_path: str, diagnostics_obj: object) -> None:
        self.diagnostics_controller._on_file_diagnostics_updated(file_path, diagnostics_obj)
        self._maybe_prompt_clangd_std_header_repair(file_path, diagnostics_obj)

    def _on_rust_file_diagnostics_updated(self, file_path: str, diagnostics_obj: object) -> None:
        self.diagnostics_controller._on_file_diagnostics_updated(file_path, diagnostics_obj)

    def _maybe_prompt_clangd_std_header_repair(self, file_path: str, diagnostics_obj: object) -> None:
        if self._clangd_repair_active:
            return
        diagnostics = diagnostics_obj if isinstance(diagnostics_obj, list) else []
        missing_headers: list[str] = []
        for diag in diagnostics:
            if not isinstance(diag, dict):
                continue
            header = missing_std_header_from_diagnostic(diag)
            if not header:
                continue
            missing_headers.append(header)
        if not missing_headers:
            return

        first_header = str(missing_headers[0] or "").strip()
        cpath = self._canonical_path(file_path)
        prompt_key = f"{cpath.lower()}|{first_header.lower()}"
        if prompt_key in self._clangd_std_header_prompt_keys:
            return
        self._clangd_std_header_prompt_keys.add(prompt_key)

        def _prompt() -> None:
            message = (
                f"clangd cannot resolve standard header '{first_header}' in this project.\n\n"
                "Apply automatic C/C++ include repair now?\n"
                "This tries query-driver first, then writes a project .clangd include patch only if still needed."
            )
            choice = QMessageBox.question(
                self,
                "Repair Clangd Includes",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if choice != QMessageBox.Yes:
                return
            ok, detail = self._run_clangd_include_repair(target_file_path=cpath)
            if ok:
                self.statusBar().showMessage(detail, 4800)
                return
            self.statusBar().showMessage(detail, 5200)
            # Allow another prompt later if repair did not succeed.
            self._clangd_std_header_prompt_keys.discard(prompt_key)

        QTimer.singleShot(0, _prompt)

    def _run_clangd_include_repair(self, *, target_file_path: str = "") -> tuple[bool, str]:
        if self._clangd_repair_active:
            return False, "Clangd include repair is already running."
        self._clangd_repair_active = True
        try:
            cpp_cfg = self._cpp_config()
            clangd_path = str(cpp_cfg.get("clangd_path") or "clangd").strip() or "clangd"
            query_driver = str(cpp_cfg.get("query_driver") or "").strip()
            compile_mode = str(cpp_cfg.get("compile_commands_mode") or "auto").strip().lower()
            compile_path = str(cpp_cfg.get("compile_commands_path") or "").strip()
            result = repair_clangd_includes(
                project_root=self.project_root,
                clangd_path=clangd_path,
                query_driver=query_driver,
                compile_commands_mode=compile_mode,
                compile_commands_path=compile_path,
                target_file_path=target_file_path,
            )

            if result.query_driver_changed and result.query_driver:
                self.settings_manager.set("c_cpp.query_driver", result.query_driver, "project")
                self.settings_manager.save_all(scopes={"project"}, only_dirty=True)

            # Always refresh once so clangd restarts with updated settings and/or .clangd.
            self._refresh_runtime_settings_from_manager()

            if result.ok:
                if result.fixed_by_query_driver:
                    return True, "Clangd include repair applied via query-driver."
                if result.wrote_clangd_file:
                    return True, f"Clangd include repair applied ({result.clangd_file_path})."
                return True, result.message
            if result.wrote_clangd_file:
                return False, f"{result.message} ({result.clangd_file_path})"
            return False, result.message
        except Exception as exc:
            return False, f"Clangd include repair failed: {exc}"
        finally:
            self._clangd_repair_active = False

    def _on_editor_font_size_step_requested(self, ed_ref, step: int) -> None:
        widget = ed_ref() if callable(ed_ref) else ed_ref
        if widget is None or not _is_qobject_valid(widget):
            return
        delta = 1 if int(step or 0) > 0 else -1
        target = self._clamp_editor_font_size(int(self.font_size) + delta)
        if target == int(self.font_size):
            return
        self._set_editor_font_size(target, persist=True, announce=True)

    def _request_definition_for_editor(self, ed: EditorWidget, payload: dict):
        self.language_intelligence_controller._request_definition_for_editor(ed, payload)

    def _request_usages_for_editor(self, ed: EditorWidget, payload: dict):
        self.language_intelligence_controller._request_usages_for_editor(ed, payload)

    def _editor_by_id(self, editor_id: str) -> EditorWidget | None:
        return self.language_intelligence_controller._editor_by_id(editor_id)

    def _on_completion_result_ready(self, result_obj: object):
        self.language_intelligence_controller._on_completion_result_ready(result_obj)

    def _on_signature_result_ready(self, result_obj: object):
        self.language_intelligence_controller._on_signature_result_ready(result_obj)

    def _navigate_to_location(self, file_path: str, line: int, column: int):
        self.language_intelligence_controller._navigate_to_location(file_path, line, column)

    def _on_definition_result_ready(self, result_obj: object):
        self.language_intelligence_controller._on_definition_result_ready(result_obj)

    def _on_references_progress(self, result_obj: object):
        self.language_intelligence_controller._on_references_progress(result_obj)

    def _on_references_done(self, result_obj: object):
        self.language_intelligence_controller._on_references_done(result_obj)

    def _invoke_focus_chain_edit_method(self, method_name: str) -> bool:
        name = str(method_name or "").strip()
        if not name:
            return False

        widget = QApplication.focusWidget()
        visited: set[int] = set()
        while isinstance(widget, QWidget):
            wid = id(widget)
            if wid in visited:
                break
            visited.add(wid)
            method = getattr(widget, name, None)
            if callable(method):
                try:
                    method()
                    return True
                except Exception:
                    pass
            widget = widget.parentWidget()
        return False

    def _is_project_tree_focus_context(self) -> bool:
        tree = getattr(self, "tree", None)
        if not isinstance(tree, FileSystemTreeWidget):
            return False
        widget = QApplication.focusWidget()
        visited: set[int] = set()
        while isinstance(widget, QWidget):
            wid = id(widget)
            if wid in visited:
                break
            visited.add(wid)
            if widget is tree:
                return True
            widget = widget.parentWidget()
        return False

    def copy_focused_widget(self) -> None:
        if self._is_project_tree_focus_context():
            self._copy_tree_selection()
            return
        if self._invoke_focus_chain_edit_method("copy"):
            return
        ed = self.current_editor()
        if isinstance(ed, EditorWidget):
            ed.copy()

    def paste_into_focused_widget(self) -> None:
        if self._is_project_tree_focus_context():
            self._paste_tree_into_selection()
            return
        if self._invoke_focus_chain_edit_method("paste"):
            return
        ed = self.current_editor()
        if isinstance(ed, EditorWidget):
            ed.paste()

    def trigger_completion(self):
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        self._request_completion_for_editor(ed, reason="manual")

    def trigger_ai_inline_assist(self):
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        self._request_ai_inline_for_editor(ed, reason="manual")

    def show_find_in_editor(self):
        widget = self._current_document_widget()
        show_find = getattr(widget, "show_find_bar", None)
        if not callable(show_find):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        show_find()

    def show_replace_in_editor(self):
        widget = self._current_document_widget()
        show_replace = getattr(widget, "show_replace_bar", None)
        if not callable(show_replace):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        show_replace()

    def format_current_file(self) -> None:
        self._format_active_editor(selection_only=False)

    def format_current_selection(self) -> None:
        self._format_active_editor(selection_only=True)

    def _format_active_editor(self, *, selection_only: bool) -> None:
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        if not ed.file_path:
            self.statusBar().showMessage("Save the file before formatting.", 2200)
            return

        language_id = str(ed.language_id() or "").strip().lower()
        provider = self.code_formatting_registry.provider_for(language_id=language_id, file_path=str(ed.file_path or ""))
        if provider is None or not provider.can_format(language_id, file_path=str(ed.file_path or "")):
            self.statusBar().showMessage("No formatter for this file type.", 2200)
            return

        request = FormatRequest(
            file_path=str(ed.file_path or ""),
            source_text=ed.toPlainText(),
            project_root=self.project_root,
            interpreter=self.resolve_interpreter(str(ed.file_path or "")),
            parent_widget=self,
        )

        if selection_only:
            cursor = ed.textCursor()
            if not cursor.hasSelection():
                self.statusBar().showMessage("No selection to format.", 1800)
                return
            try:
                first_bn, last_bn = ed._selected_block_range(cursor)
                start_line = max(1, int(first_bn) + 1)
                end_line = max(start_line, int(last_bn) + 1)
            except Exception:
                start_line = max(1, int(cursor.blockNumber()) + 1)
                end_line = start_line
            result = provider.format_selection(request, start_line=start_line, end_line=end_line)
        else:
            result = provider.format_document(request)

        if result.debug_lines:
            self._append_debug_output_lines([str(line) for line in result.debug_lines if str(line).strip()], reveal=False)

        if result.status == "canceled":
            return
        if not result.ok:
            msg = str(result.message or "Formatting failed.").strip()
            QMessageBox.warning(self, "Formatting", msg)
            self.statusBar().showMessage(msg, 2600)
            return

        formatted_text = str(result.formatted_text or "")
        original_text = ed.toPlainText()
        if formatted_text != original_text:
            self._replace_editor_text_preserve_cursor(ed, formatted_text)
            scope = "selection" if selection_only else "file"
            self.statusBar().showMessage(f"Formatted {scope}: {os.path.basename(str(ed.file_path or ''))}", 2200)
        else:
            self.statusBar().showMessage("Already formatted.", 1600)

        if result.created_config_path:
            self.statusBar().showMessage(f"Created {result.created_config_path} and formatted file.", 3200)

    def open_find_in_files_dialog(self) -> None:
        self.search_controller.open_find_in_files_dialog()

    def _normalize_find_in_files_request(self, payload: object) -> dict | None:
        return self.search_controller._normalize_find_in_files_request(payload)

    def _compile_find_in_files_pattern(self, request: dict) -> re.Pattern[str] | None:
        return self.search_controller._compile_find_in_files_pattern(request)

    def _find_in_files_targets(self) -> list[str]:
        return self.search_controller._find_in_files_targets()

    def _search_indexed_files(
        self,
        pattern: re.Pattern[str],
        targets: list[str],
        *,
        max_results: int = 20000,
    ) -> list[dict]:
        return self.search_controller._search_indexed_files(pattern, targets, max_results=max_results)

    def _apply_replaced_text_to_open_editor(self, file_path: str, disk_text: str) -> None:
        self.search_controller._apply_replaced_text_to_open_editor(file_path, disk_text)

    def _replace_in_indexed_files(
        self,
        pattern: re.Pattern[str],
        replace_text: str,
        targets: list[str],
    ) -> tuple[int, int, list[str]]:
        return self.search_controller._replace_in_indexed_files(pattern, replace_text, targets)

    def _set_find_in_files_results(self, results: list[dict], summary_text: str) -> None:
        self.search_controller._set_find_in_files_results(results, summary_text)

    def _on_find_in_files_requested(self, payload: object) -> None:
        self.search_controller._on_find_in_files_requested(payload)

    def _on_replace_in_files_requested(self, payload: object) -> None:
        self.search_controller._on_replace_in_files_requested(payload)

    def _prune_find_results_docks(self) -> None:
        self.search_controller._prune_find_results_docks()

    def _create_find_results_dock(self, payload: dict) -> None:
        self.search_controller._create_find_results_dock(payload)

    def _on_add_find_results_dock_requested(self, payload: object) -> None:
        self.search_controller._on_add_find_results_dock_requested(payload)

    def _documentation_root_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for base in (
            Path(self.project_root),
            Path(__file__).resolve().parents[2],
            Path.cwd(),
        ):
            try:
                candidate = (base / "docs").resolve()
            except Exception:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _resolve_documentation_root(self) -> Path | None:
        for candidate in self._documentation_root_candidates():
            if candidate.is_dir():
                return candidate
        return None

    def open_documentation_viewer(self) -> None:
        docs_root = self._resolve_documentation_root()
        if docs_root is None:
            attempted = "\n".join(str(path) for path in self._documentation_root_candidates())
            QMessageBox.information(
                self,
                "Documentation",
                "No docs folder was found.\n\nLooked in:\n" + attempted,
            )
            return

        try:
            from TPOPyside.widgets.doc_viewer import DocumentationViewer
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Documentation",
                f"Could not open documentation viewer:\n{exc}",
            )
            return

        viewer = self._documentation_viewer
        if isinstance(viewer, QWidget):
            try:
                viewer_root = Path(str(getattr(viewer, "doc_root", ""))).resolve()
                if viewer_root != docs_root:
                    viewer.close()
                    viewer.deleteLater()
                    viewer = None
                    self._documentation_viewer = None
            except Exception:
                viewer = None
                self._documentation_viewer = None

        if not isinstance(viewer, QWidget) or not _is_qobject_valid(viewer):
            viewer = DocumentationViewer(str(docs_root), parent=self)
            viewer.setWindowFlag(Qt.Window, True)
            self._documentation_viewer = viewer

        viewer.show()
        viewer.raise_()
        viewer.activateWindow()
        self.statusBar().showMessage(f"Opened documentation from {docs_root}", 2500)

    def open_check_for_updates_dialog(self) -> None:
        from src.services.update_service import UpdateService
        from src.ui.dialogs.check_for_updates_dialog import CheckForUpdatesDialog

        update_service = UpdateService(
            app_root=Path(__file__).resolve().parents[2],
            github_token_provider=lambda: self._github_auth_store.get(),
        )
        dialog = CheckForUpdatesDialog(
            update_service=update_service,
            use_native_chrome=self.use_native_chrome,
            parent=self,
        )
        dialog.exec()

    def _app_version_from_pyproject(self) -> str:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        try:
            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            project = payload.get("project")
            if isinstance(project, dict):
                version = str(project.get("version") or "").strip()
                if version:
                    return version
        return "unknown"

    def _about_markdown_payload(self, *, version: str) -> tuple[str, QUrl]:
        about_path = Path(__file__).resolve().parents[2] / "docs" / "about.md"
        text = ""
        if about_path.is_file():
            try:
                text = about_path.read_text(encoding="utf-8")
            except Exception:
                text = ""
        if not text.strip():
            text = (
                f"# {self.APP_NAME}\n\n"
                "PySide6 IDE with language packs for Python, C/C++, and Rust.\n\n"
                "Open Help > Documentation to browse local docs.\n"
            )
        text = text.replace("{{APP_NAME}}", self.APP_NAME)
        base_url = QUrl.fromLocalFile(str(about_path.parent if about_path.is_file() else about_path))
        return text, base_url

    def show_about_dialog(self) -> None:
        from src.ui.dialogs.about_dialog import AboutDialog

        version = self._app_version_from_pyproject()
        markdown_text, markdown_base_url = self._about_markdown_payload(version=version)
        dialog = AboutDialog(
            app_name=self.APP_NAME,
            app_version=version,
            markdown_text=markdown_text,
            markdown_base_url=markdown_base_url,
            use_native_chrome=self.use_native_chrome,
            parent=self,
        )
        dialog.exec()

    def go_to_definition(self):
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        if not ed.request_definition("menu"):
            self.statusBar().showMessage("No symbol under cursor.", 1600)

    def find_usages(self):
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        if not ed.request_usages("menu"):
            self.statusBar().showMessage("No symbol under cursor.", 1600)

    def rename_symbol(self):
        if self._is_project_tree_focus_context():
            self._rename_tree_selection()
            return
        self.language_intelligence_controller.rename_symbol_for_current_editor()

    def extract_variable(self):
        self.language_intelligence_controller.extract_variable_for_current_editor()

    def extract_method(self):
        self.language_intelligence_controller.extract_method_for_current_editor()

    def cancel_find_usages(self):
        token = int(self._active_usages_token or 0)
        if token <= 0:
            return
        self.language_service_hub.cancel_references(token)
        self.statusBar().showMessage("Canceling usages search...", 1600)

    def _request_lint_for_editor(
        self,
        ed: EditorWidget,
        reason: str,
        include_source_if_modified: bool = True,
    ):
        self.diagnostics_controller._request_lint_for_editor(
            ed,
            reason,
            include_source_if_modified=include_source_if_modified,
        )

    def _apply_lint_to_editor(self, ed: EditorWidget):
        self.diagnostics_controller._apply_lint_to_editor(ed)

    def _apply_lint_to_open_editors_for_file(self, file_path: str):
        self.diagnostics_controller._apply_lint_to_open_editors_for_file(file_path)

    def _set_problems_panel_data(self):
        self.diagnostics_controller._set_problems_panel_data()

    def _on_file_diagnostics_updated(self, file_path: str, diagnostics_obj: object):
        self.diagnostics_controller._on_file_diagnostics_updated(file_path, diagnostics_obj)

    def _on_file_diagnostics_cleared(self, file_path: str):
        self.diagnostics_controller._on_file_diagnostics_cleared(file_path)

    def _on_all_diagnostics_cleared(self):
        self.diagnostics_controller._on_all_diagnostics_cleared()

    def _on_problem_count_changed(self, count: int):
        self.diagnostics_controller._on_problem_count_changed(count)

    def _navigate_to_problem_location(self, file_path: str, line: int, col: int) -> bool:
        cpath = self._canonical_path(file_path)
        if os.path.exists(cpath):
            self.open_file(cpath)

        widget = self._find_open_document_for_path(cpath)
        if widget is None:
            return False

        line_num = max(1, int(line or 1))
        col_num = max(1, int(col or 1))
        if isinstance(widget, EditorWidget):
            block = widget.document().findBlockByNumber(line_num - 1)
            if not block.isValid():
                block = widget.document().lastBlock()
            cursor = QTextCursor(block)
            cursor.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.MoveAnchor,
                col_num - 1,
            )
            widget.setTextCursor(cursor)
            widget.centerCursor()
        elif isinstance(widget, TDocDocumentWidget):
            widget.jump_to_line(line_num, col_num)
        else:
            cursor_getter = getattr(widget, "textCursor", None)
            doc_getter = getattr(widget, "document", None)
            if callable(cursor_getter) and callable(doc_getter):
                try:
                    doc = doc_getter()
                    block = doc.findBlockByNumber(line_num - 1)
                    if block.isValid():
                        cursor = QTextCursor(block)
                        cursor.movePosition(
                            QTextCursor.MoveOperation.Right,
                            QTextCursor.MoveMode.MoveAnchor,
                            col_num - 1,
                        )
                        widget.setTextCursor(cursor)
                except Exception:
                    pass
        self._focus_document_widget(widget)
        return True

    def _on_problem_activated(self, file_path: str, line: int, col: int):
        self.diagnostics_controller._on_problem_activated(file_path, line, col)

    def _on_problem_import_symbol_requested(self, diag_obj: object):
        self.diagnostics_controller._on_problem_import_symbol_requested(diag_obj)

    def _on_problem_remove_unused_import_requested(self, diag_obj: object):
        self.diagnostics_controller._on_problem_remove_unused_import_requested(diag_obj)

    def _on_problem_add_tdoc_symbol_requested(self, diag_obj: object):
        self.diagnostics_controller._on_problem_add_tdoc_symbol_requested(diag_obj)

    def _on_problem_capitalize_tdoc_section_requested(self, diag_obj: object):
        self.diagnostics_controller._on_problem_capitalize_tdoc_section_requested(diag_obj)

    def _on_problem_clear_file_requested(self, file_path: str) -> None:
        cpath = self._canonical_path(file_path)
        self.lint_manager.clear_file(cpath)
        self.cpp_language_pack.clear_file_diagnostics(cpath)
        self.rust_language_pack.clear_file_diagnostics(cpath)
        self._clear_tdoc_diagnostics_for_path(cpath)

    def _on_problem_clear_all_requested(self) -> None:
        self.lint_manager.clear_all()
        self.cpp_language_pack.clear_all_diagnostics()
        self.rust_language_pack.clear_all_diagnostics()
        self._clear_all_tdoc_diagnostics()

    def _apply_import_candidate_to_editor(self, ed: EditorWidget, candidate: dict, symbol: str) -> str:
        return self.diagnostics_controller._apply_import_candidate_to_editor(ed, candidate, symbol)

    def _missing_symbol_from_diagnostic(self, diag: dict | None) -> str:
        return self.diagnostics_controller._missing_symbol_from_diagnostic(diag)

    def _is_unused_import_diagnostic(self, diag: dict | None) -> bool:
        return self.diagnostics_controller._is_unused_import_diagnostic(diag)

    def _unused_import_name_from_diagnostic(self, diag: dict | None) -> str:
        return self.diagnostics_controller._unused_import_name_from_diagnostic(diag)

    def _symbol_used_as_module(self, source_text: str, diag: dict | None, symbol: str) -> bool:
        return self.diagnostics_controller._symbol_used_as_module(source_text, diag, symbol)

    def _resolve_import_candidates(
        self,
        *,
        symbol: str,
        source_text: str,
        prefer_module_import: bool,
        current_file_path: str = "",
    ) -> list[dict]:
        return self.diagnostics_controller._resolve_import_candidates(
            symbol=symbol,
            source_text=source_text,
            prefer_module_import=prefer_module_import,
            current_file_path=current_file_path,
        )

    def _modules_mentioned_in_imports(self, source_text: str) -> set[str]:
        return self.diagnostics_controller._modules_mentioned_in_imports(source_text)

    def _project_module_name_for_file(self, file_path: str) -> str:
        return self.diagnostics_controller._project_module_name_for_file(file_path)

    def _iter_indexable_python_files(self) -> list[str]:
        return self.diagnostics_controller._iter_indexable_python_files()

    def _project_file_exported_names(self, file_path: str) -> set[str]:
        return self.diagnostics_controller._project_file_exported_names(file_path)

    def _refresh_project_symbol_index(self) -> None:
        self.diagnostics_controller._refresh_project_symbol_index()

    def _project_local_symbol_modules(self, symbol: str, *, current_file_path: str = "") -> list[str]:
        return self.diagnostics_controller._project_local_symbol_modules(symbol, current_file_path=current_file_path)

    def _module_exports_symbol(self, module_name: str, symbol: str) -> bool:
        return self.diagnostics_controller._module_exports_symbol(module_name, symbol)

    def _can_import_module(self, module_name: str) -> bool:
        return self.diagnostics_controller._can_import_module(module_name)

    def _qt_symbol_candidates(self, symbol: str) -> list[tuple[str, str]]:
        return self.diagnostics_controller._qt_symbol_candidates(symbol)

    def _insert_import_candidate(self, ed: EditorWidget, candidate: dict) -> str:
        return self.diagnostics_controller._insert_import_candidate(ed, candidate)

    def _remove_unused_import_from_editor(self, ed: EditorWidget, diag_obj: object) -> str:
        return self.diagnostics_controller._remove_unused_import_from_editor(ed, diag_obj)

    def _remove_unused_import_from_source(self, source_text: str, diag: dict | None) -> tuple[str, str, str]:
        result = self.diagnostics_controller._remove_unused_import_from_source(source_text, diag)
        return result.source_text, result.removed_name, result.status

    def _insert_module_import(self, ed: EditorWidget, module_name: str, bind_name: str) -> str:
        return self.diagnostics_controller._insert_module_import(ed, module_name, bind_name)

    def _insert_from_import(self, ed: EditorWidget, module_name: str, export_name: str, bind_name: str) -> str:
        return self.diagnostics_controller._insert_from_import(ed, module_name, export_name, bind_name)

    def _replace_editor_text_preserve_cursor(self, ed: EditorWidget, text: str) -> None:
        cursor = ed.textCursor()
        pos = max(0, int(cursor.position()))
        doc_cursor = QTextCursor(ed.document())
        doc_cursor.beginEditBlock()
        doc_cursor.select(QTextCursor.SelectionType.Document)
        doc_cursor.insertText(text)
        doc_cursor.endEditBlock()
        new_cursor = ed.textCursor()
        new_cursor.setPosition(min(pos, len(text)))
        ed.setTextCursor(new_cursor)

    def _on_console_traceback_activated(self, file_path: str, line: int, col: int):
        if not file_path:
            return
        self._on_problem_activated(file_path, line, col)
        self.statusBar().showMessage(
            f"Opened console location: {os.path.basename(file_path)}:{max(1, int(line or 1))}",
            2200,
        )

    def lint_current_file(self):
        widget = self._current_document_widget()
        if widget is None:
            self.statusBar().showMessage("No active editor to lint.", 1800)
            return
        file_path = self._document_widget_path(widget)
        if not file_path:
            self.statusBar().showMessage("Save the file before linting.", 2200)
            return

        cpath = self._canonical_path(file_path)
        if self._is_tdoc_related_path(cpath):
            self._refresh_tdoc_diagnostics_for_path(cpath)
            if self.dock_problems is not None:
                self.dock_problems.show()
            self.statusBar().showMessage(f"TDOC validation completed for {os.path.basename(cpath)}", 1800)
            return

        ed = widget if isinstance(widget, EditorWidget) else None
        if not isinstance(ed, EditorWidget):
            self.statusBar().showMessage("Linting is available for Python and TDOC files.", 2200)
            return
        if not self._is_python_file_path(ed.file_path):
            self.statusBar().showMessage("Linting is available for Python and TDOC files.", 2200)
            self.lint_manager.clear_file(self._canonical_path(ed.file_path))
            ed.clear_lint_diagnostics()
            return
        self._attach_editor_lint_hooks(ed)
        self._request_lint_for_editor(ed, reason="manual", include_source_if_modified=True)
        if self.dock_problems is not None:
            self.dock_problems.show()
        self.statusBar().showMessage(f"Lint requested for {os.path.basename(ed.file_path)}", 1500)

    def lint_project(self):
        self.lint_manager.request_lint_project()
        self._refresh_tdoc_diagnostics_for_project()
        if self.dock_problems is not None:
            self.dock_problems.show()

    def clear_diagnostics(self):
        self.lint_manager.clear_all()
        self.cpp_language_pack.clear_all_diagnostics()
        self.rust_language_pack.clear_all_diagnostics()
        self._clear_all_tdoc_diagnostics()
        self.statusBar().showMessage("Diagnostics cleared.", 1500)

    # ---------- Project Explorer ----------

    def _show_tree_error(self, title: str, message: str):
        self.explorer_controller._show_tree_error(title, message)

    def _show_project_tree_context_menu(self, path_obj: object, global_pos: QPoint):
        self.explorer_controller._show_project_tree_context_menu(path_obj, global_pos)

    def _copy_tree_selection(self):
        self.explorer_controller._copy_tree_selection()

    def _cut_tree_selection(self):
        self.explorer_controller._cut_tree_selection()

    def _paste_tree_into_selection(self):
        self.explorer_controller._paste_tree_into_selection()

    def _selected_tree_paths(self) -> list[str]:
        return self.explorer_controller._selected_tree_paths()

    def _context_target_paths(self, trigger_path: str | None) -> list[str]:
        return self.explorer_controller._context_target_paths(trigger_path)

    def _populate_multi_context_menu(self, menu: QMenu, paths: list[str]) -> None:
        self.explorer_controller._populate_multi_context_menu(menu, paths)

    def _copy_tree_paths(self, path: str | None):
        self.explorer_controller._copy_tree_paths(path)

    def _cut_tree_paths(self, path: str | None):
        self.explorer_controller._cut_tree_paths(path)

    def _paste_tree_paths_into(self, dest_dir: str):
        self.explorer_controller._paste_tree_paths_into(dest_dir)

    def _resolve_tree_paste_paths(self) -> list[str]:
        return self.explorer_controller._resolve_tree_paste_paths()

    def _set_system_clipboard_paths(self, paths: list[str]) -> None:
        self.explorer_controller._set_system_clipboard_paths(paths)

    def _system_clipboard_paths(self) -> list[str]:
        return self.explorer_controller._system_clipboard_paths()

    def _filter_nested_paths(self, paths: list[str]) -> list[str]:
        return self.explorer_controller._filter_nested_paths(paths)

    def _next_copy_target(self, dest_dir: str, source_name: str) -> str:
        return self.explorer_controller._next_copy_target(dest_dir, source_name)

    def _populate_folder_context_menu(self, menu: QMenu, folder_path: str):
        self.explorer_controller._populate_folder_context_menu(menu, folder_path)

    def _populate_file_context_menu(self, menu: QMenu, file_path: str):
        self.explorer_controller._populate_file_context_menu(menu, file_path)

    def _populate_root_context_menu(self, menu: QMenu):
        self.explorer_controller._populate_root_context_menu(menu)

    def refresh_project_tree(self, include_excluded: bool = False):
        self.explorer_controller.refresh_project_tree(include_excluded=include_excluded)
        if not include_excluded:
            self._schedule_project_fs_watch_sync()

    def refresh_subtree(self, path: str):
        self.explorer_controller.refresh_subtree(path)
        self._schedule_project_fs_watch_sync()

    def _on_tree_path_moved(self, old_path: str, new_path: str):
        self.explorer_controller._on_tree_path_moved(old_path, new_path)

    def _create_new_file(self, folder_path: str):
        self.explorer_controller._create_new_file(folder_path)

    def _create_new_folder(self, folder_path: str):
        self.explorer_controller._create_new_folder(folder_path)

    def _rename_path(self, path: str):
        self.explorer_controller._rename_path(path)

    def _rename_tree_selection(self) -> None:
        targets = self._selected_tree_paths()
        if len(targets) != 1:
            self.statusBar().showMessage("Select a single file or folder to rename.", 2200)
            return
        self._rename_path(targets[0])

    def _delete_path(self, path: str):
        self.explorer_controller._delete_path(path)

    def _delete_paths(self, paths: list[str]) -> None:
        self.explorer_controller._delete_paths(paths)

    def _delete_tree_selection(self) -> None:
        targets = self._selected_tree_paths()
        if not targets:
            self.statusBar().showMessage("Select file(s) or folder(s) to delete.", 2200)
            return
        self._delete_paths(targets)

    def _set_interpreter_for_folder_dialog(self, folder_path: str):
        self.explorer_controller._set_interpreter_for_folder_dialog(folder_path)

    def _clear_folder_interpreter_action(self, folder_path: str):
        self.explorer_controller._clear_folder_interpreter_action(folder_path)

    def _toggle_folder_excluded(self, folder_path: str):
        self.explorer_controller._toggle_folder_excluded(folder_path)

    def _toggle_file_excluded(self, file_path: str):
        self.explorer_controller._toggle_file_excluded(file_path)

    def _set_folders_excluded_bulk(self, folder_paths: list[str], *, excluded: bool) -> None:
        self.explorer_controller._set_folders_excluded_bulk(folder_paths, excluded=excluded)

    def _set_files_excluded_bulk(self, file_paths: list[str], *, excluded: bool) -> None:
        self.explorer_controller._set_files_excluded_bulk(file_paths, excluded=excluded)

    def _is_untracked_git_path(
        self,
        file_path: str,
        *,
        repo_root: str | None = None,
        allow_fallback: bool = True,
    ) -> bool:
        return self.explorer_controller._is_untracked_git_path(
            file_path,
            repo_root=repo_root,
            allow_fallback=allow_fallback,
        )

    def _prompt_simple_name(self, title: str, label: str, initial: str = "") -> str | None:
        return self.explorer_controller._prompt_simple_name(title, label, initial=initial)

    # ---------- Helpers ----------

    def _canonical_path(self, path: str) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(path))

    def _rel_to_project(self, path: str) -> str:
        cpath = self._canonical_path(path)
        if not self._path_has_prefix(cpath, self.project_root):
            return cpath
        rel = os.path.relpath(cpath, self.project_root)
        return "." if rel in (".", "") else rel

    def _resolve_path_from_project(self, rel_or_abs: str) -> str:
        text = str(rel_or_abs or "").strip()
        if not text:
            return self._canonical_path(self.project_root)

        expanded = os.path.expanduser(text)
        if os.path.isabs(expanded):
            return self._canonical_path(expanded)
        return self._canonical_path(os.path.join(self.project_root, expanded))

    def _resolve_path_from_project_no_symlink_resolve(self, rel_or_abs: str) -> str:
        text = str(rel_or_abs or "").strip()
        if not text:
            return os.path.abspath(os.path.expanduser(self.project_root))

        expanded = os.path.expanduser(text)
        if os.path.isabs(expanded):
            return os.path.abspath(expanded)
        return os.path.abspath(os.path.join(self.project_root, expanded))

    def _normalize_rel(self, path: str) -> str:
        text = str(path or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.strip("/")

    def _path_has_prefix(self, path: str, prefix: str) -> bool:
        try:
            cpath = self._canonical_path(path)
            cprefix = self._canonical_path(prefix)
            return os.path.commonpath([cpath, cprefix]) == cprefix
        except Exception:
            return False

    def _resolve_interpreter_value(self, value) -> str:
        return self.project_policy_service._resolve_interpreter_value(value)

    def _run_config(self) -> dict:
        cfg = self.config.get("run", {})
        return cfg if isinstance(cfg, dict) else {}

    def _window_config(self) -> dict:
        cfg = self.config.get("window", {})
        return cfg if isinstance(cfg, dict) else {}

    def _apply_custom_toolbar_window_settings(self) -> None:
        if self._custom_title_text_visibility_override is not None:
            self.set_title_text_visible(bool(self._custom_title_text_visibility_override))
            return
        show_title = bool(
            self.settings_manager.get(
                "window.show_title_in_custom_toolbar",
                scope_preference="ide",
                default=True,
            )
        )
        self.set_title_text_visible(show_title)

    def _sync_titlebar_ai_toggle(self) -> None:
        chk = self._toolbar_ai_checkbox
        if chk is None:
            return
        enabled = bool(
            self.settings_manager.get(
                "ai_assist.enabled",
                scope_preference="ide",
                default=False,
            )
        )
        self._toolbar_ai_toggle_guard = True
        try:
            chk.setChecked(enabled)
        finally:
            self._toolbar_ai_toggle_guard = False

    def _on_titlebar_ai_toggle_changed(self, checked: bool) -> None:
        if self._toolbar_ai_toggle_guard:
            return
        self.settings_manager.set("ai_assist.enabled", bool(checked), "ide")
        self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        self._refresh_runtime_settings_from_manager()
        state = "enabled" if checked else "disabled"
        self.statusBar().showMessage(f"AI Assist {state}.", 1600)

    def _projects_config(self) -> dict:
        cfg = self.config.get("projects", {})
        return cfg if isinstance(cfg, dict) else {}

    def _autosave_config(self) -> dict:
        cfg = self.config.get("autosave", {})
        return cfg if isinstance(cfg, dict) else {}

    def _recent_projects_limit(self) -> int:
        try:
            return max(1, min(50, int(self.settings_manager.get("projects.max_recent_projects", scope_preference="ide", default=10))))
        except Exception:
            return 10

    def _recent_projects(self) -> list[str]:
        raw = self.settings_manager.get("projects.recent_projects", scope_preference="ide", default=[])
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            text = str(entry or "").strip()
            if not text:
                continue
            try:
                canonical = self._canonical_path(text)
            except Exception:
                canonical = text
            key = canonical.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(canonical)
        return result[: self._recent_projects_limit()]

    def _set_recent_projects(self, projects: list[str], *, save: bool = True) -> None:
        clean: list[str] = []
        seen: set[str] = set()
        for entry in projects:
            text = str(entry or "").strip()
            if not text:
                continue
            canonical = self._canonical_path(text)
            key = canonical.lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(canonical)
        clean = clean[: self._recent_projects_limit()]
        self.settings_manager.set("projects.recent_projects", clean, "ide")
        if save:
            self.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        self.config = self.settings_manager.export_legacy_config()
        self._refresh_recent_projects_menu()
        self._refresh_welcome_recent_projects()

    def _remember_recent_project(self, project_path: str, *, save: bool = True) -> None:
        canonical = self._canonical_path(project_path)
        others = [entry for entry in self._recent_projects() if entry.lower() != canonical.lower()]
        self._set_recent_projects([canonical] + others, save=save)

    def _refresh_recent_projects_menu(self) -> None:
        menu = self.recent_projects_menu
        if menu is None:
            return
        menu.clear()
        recent = self._recent_projects()
        shown = 0
        for path in recent:
            if not os.path.isdir(path):
                continue
            shown += 1
            label = path
            action = menu.addAction(label)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_project_path(p))
        if shown == 0:
            placeholder = menu.addAction("No Recent Projects")
            placeholder.setEnabled(False)
        menu.addSeparator()
        clear_action = menu.addAction("Clear Recent Projects")
        clear_action.setEnabled(bool(recent))
        clear_action.triggered.connect(self.clear_recent_projects)

    def clear_recent_projects(self) -> None:
        self._set_recent_projects([], save=True)
        self.statusBar().showMessage("Recent projects cleared.", 1500)

    def _configure_autosave_timer(self) -> None:
        self.workspace_controller._configure_autosave_timer()

    def _schedule_autosave(self) -> None:
        self.workspace_controller._schedule_autosave()

    def _autosave_dirty_editors(self) -> None:
        self.workspace_controller._autosave_dirty_editors()

    def _collect_open_file_paths(self) -> set[str]:
        paths: set[str] = set()
        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            paths.add(self._canonical_path(path))
        config_path = str(self.project_config_path or "").strip()
        if config_path:
            paths.add(self._canonical_path(config_path))
        return paths

    def _is_project_config_path(self, path: str | None) -> bool:
        candidate = str(path or "").strip()
        if not candidate:
            return False
        config_path = str(self.project_config_path or "").strip()
        if not config_path:
            return False
        return self._canonical_path(candidate) == self._canonical_path(config_path)

    def _queue_project_config_reload(self, *, source: str, honor_open_editors: bool = True) -> None:
        self._project_config_reload_source = str(source or "project.json change")
        self._project_config_reload_honor_open_editors = (
            self._project_config_reload_honor_open_editors or bool(honor_open_editors)
        )
        if self._project_config_reload_pending:
            return
        self._project_config_reload_pending = True
        QTimer.singleShot(0, self._flush_project_config_reload)

    def _flush_project_config_reload(self) -> None:
        if not self._project_config_reload_pending:
            return
        source = self._project_config_reload_source or "project.json change"
        honor_open_editors = bool(self._project_config_reload_honor_open_editors)
        self._project_config_reload_pending = False
        self._project_config_reload_honor_open_editors = False
        self._project_config_reload_source = ""
        self._reload_project_config_from_disk(source=source, honor_open_editors=honor_open_editors)

    def _note_editor_saved(self, ed: object, *, source: str) -> None:
        self.workspace_controller._note_editor_saved(ed, source=source)

    def _external_file_signature(self, path: str) -> tuple[bool, int, int] | None:
        return self.workspace_controller._external_file_signature(path)

    def _seed_external_file_watch_state(self) -> None:
        self.workspace_controller._seed_external_file_watch_state()

    def _check_external_file_updates(self) -> None:
        self.workspace_controller._check_external_file_updates()

    def _handle_external_file_change(self, path: str, sig: tuple[bool, int, int]) -> None:
        self.workspace_controller._handle_external_file_change(path, sig)

    def _setup_instance_server(self) -> None:
        key = self.no_project_instance_key() if self.no_project_mode else self.project_root
        server = ProjectInstanceServer(key, parent=self)
        if not server.listen():
            self._instance_server = None
            return
        server.activateRequested.connect(self._activate_window_from_instance_request)
        self._instance_server = server

    def _activate_window_from_instance_request(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _indexing_config(self) -> dict:
        return self.project_policy_service._indexing_config(self.config)

    def _iter_directory_overrides(self) -> list[dict]:
        return self.project_policy_service._iter_directory_overrides(self.config)

    def _directory_entry_abs_path(self, entry: dict) -> str:
        return self.project_policy_service._directory_entry_abs_path(entry)

    def resolve_folder_policy(self, path: str) -> dict:
        return self.project_policy_service.resolve_folder_policy(self.config, path)

    def resolve_interpreter(self, file_path: str) -> str:
        return self.project_policy_service.resolve_interpreter(self.config, file_path)

    def resolve_run_in(self, file_path: str) -> str:
        return self.project_policy_service.resolve_run_in(self.config, file_path)

    def _matches_excluded_dir(self, rel_path: str) -> bool:
        return self.project_policy_service._matches_excluded_dir(self.config, rel_path)

    def _is_file_explicitly_excluded(self, file_path: str) -> bool:
        return self.project_policy_service.is_file_explicitly_excluded(self.config, file_path)

    @staticmethod
    def _pattern_has_glob(pattern: str) -> bool:
        return ProjectPolicyService.pattern_has_glob(pattern)

    def _matches_excluded_file_pattern(self, file_path: str) -> bool:
        return self.project_policy_service._matches_excluded_file_pattern(self.config, file_path)

    def is_path_excluded(self, path: str, for_feature: str = "indexing") -> bool:
        return self.project_policy_service.is_path_excluded(self.config, path, for_feature=for_feature)

    def _is_tree_path_excluded(self, path: str, is_dir: bool) -> bool:
        return self.project_policy_service.is_tree_path_excluded(
            self.config,
            path,
            is_dir,
            no_project_mode=self.no_project_mode,
        )

    def _persist_config(self):
        self.write_project_config(self.config)

    def _normalize_folder_store_path(self, folder_path: str) -> str:
        return self.project_policy_service.normalize_folder_store_path(folder_path)

    def _find_folder_override(self, folder_path: str) -> tuple[int, dict | None]:
        return self.project_policy_service.find_folder_override(self.config, folder_path)

    def _directory_entries_ref(self) -> list:
        return self.project_policy_service.directory_entries_ref(self.config)

    def set_folder_interpreter(self, folder_path: str, python_path: str):
        if self.project_policy_service.set_folder_interpreter(self.config, folder_path, python_path):
            self._persist_config()

    def clear_folder_interpreter(self, folder_path: str):
        if self.project_policy_service.clear_folder_interpreter(self.config, folder_path):
            self._persist_config()

    def set_folder_excluded(self, folder_path: str, excluded: bool):
        if self.project_policy_service.set_folder_excluded(self.config, folder_path, excluded):
            self._persist_config()

    def set_file_excluded(self, file_path: str, excluded: bool):
        try:
            changed = self.project_policy_service.set_file_excluded(self.config, file_path, excluded)
        except ValueError:
            self._show_tree_error("Indexing", "Only project files can be toggled.")
            return
        if changed:
            self._persist_config()

    def _doc_key_for_editor(self, ed: EditorWidget) -> str:
        try:
            return self.editor_workspace.document_key_for_editor(ed)
        except Exception:
            if getattr(ed, "file_path", None):
                return self._canonical_path(ed.file_path)
            return f"__editor__/{self._editor_lookup_id(ed)}"

    def _assign_dock_identity(self, _ed: EditorWidget):
        # Workspace editors are tab/split based, not dock based.
        return

    def _is_resizable_docked(self, dock):
        return (
            dock is not None
            and dock.parentWidget() is self
            and dock.isVisible()
            and not dock.isFloating()
            and self.dockWidgetArea(dock) != Qt.NoDockWidgetArea
        )

    def _style_terminal_widget(self, terminal: TerminalWidget):
        pass

    def _set_active_terminal(self, terminal: TerminalWidget | None):
        self.terminal = terminal

    @staticmethod
    def _document_widget_path(widget: QWidget | None) -> str:
        return str(getattr(widget, "file_path", "") or "").strip()

    def _iter_open_document_widgets(self) -> list[QWidget]:
        return self.editor_workspace.all_document_widgets()

    def _is_tdoc_path(self, file_path: str | None) -> bool:
        return is_tdoc_document_path(file_path)

    def _is_tdoc_related_path(self, file_path: str | None) -> bool:
        return is_tdoc_related_path(file_path)

    def _current_document_widget(self) -> QWidget | None:
        return self.editor_workspace.active_document_widget()

    def _find_open_document_for_path(self, canonical_path: str) -> QWidget | None:
        target = self._canonical_path(canonical_path)
        found = self.editor_workspace.find_document_by_path(target)
        return found if isinstance(found, QWidget) else None

    def _focus_document_widget(self, widget: QWidget) -> None:
        if not isinstance(widget, QWidget):
            return
        for tabs in self.editor_workspace.all_tabs():
            idx = tabs.indexOf(widget)
            if idx < 0:
                continue
            tabs.setCurrentIndex(idx)
            widget.setFocus()
            return

    def _find_open_editor_for_path(self, canonical_path: str) -> EditorWidget | None:
        target = self._canonical_path(canonical_path)
        found = self._find_open_document_for_path(target)
        return found if isinstance(found, EditorWidget) else None

    def _focus_editor(self, ed: EditorWidget):
        self._focus_document_widget(ed)

    def _set_source_diagnostics_for_file(self, file_path: str, source: str, rows: list[dict]) -> None:
        key = self._canonical_path(file_path)
        clean_rows = [d for d in rows if isinstance(d, dict)]
        existing = self._diagnostics_by_file.get(key, [])
        keep = [
            d
            for d in existing
            if isinstance(d, dict) and str(d.get("source") or "").strip().lower() != str(source).strip().lower()
        ]
        merged = keep + clean_rows
        if merged:
            self._diagnostics_by_file[key] = merged
        else:
            self._diagnostics_by_file.pop(key, None)
        self._set_problems_panel_data()
        self._apply_lint_to_open_editors_for_file(key)

    def _set_tdoc_diagnostics_for_root(self, root: str, diagnostics_by_file: dict[str, list[dict]]) -> None:
        root_key = self._canonical_path(root)
        previous = self._tdoc_diagnostics_by_root.get(root_key, {})
        previous_files = set(previous.keys())

        normalized: dict[str, list[dict]] = {}
        for file_path, rows in diagnostics_by_file.items():
            cpath = self._canonical_path(file_path)
            clean_rows = [d for d in rows if isinstance(d, dict)]
            if clean_rows:
                normalized[cpath] = clean_rows

        self._tdoc_diagnostics_by_root[root_key] = normalized
        current_files = set(normalized.keys())

        for file_path in sorted(previous_files | current_files):
            self._set_source_diagnostics_for_file(file_path, "tdoc", normalized.get(file_path, []))

    def _clear_tdoc_diagnostics_for_path(self, file_path: str) -> None:
        cpath = self._canonical_path(file_path)
        for root, by_file in list(self._tdoc_diagnostics_by_root.items()):
            if cpath not in by_file:
                continue
            remaining = dict(by_file)
            remaining.pop(cpath, None)
            if remaining:
                self._tdoc_diagnostics_by_root[root] = remaining
            else:
                self._tdoc_diagnostics_by_root.pop(root, None)
            self._set_source_diagnostics_for_file(cpath, "tdoc", [])

    def _clear_all_tdoc_diagnostics(self) -> None:
        roots = list(self._tdoc_diagnostics_by_root.items())
        self._tdoc_diagnostics_by_root.clear()
        for root, by_file in roots:
            _ = root
            for file_path in by_file.keys():
                self._set_source_diagnostics_for_file(file_path, "tdoc", [])

    def _schedule_tdoc_validation(self, file_path: str, *, delay_ms: int = 520) -> None:
        cpath = self._canonical_path(file_path)
        if not self._is_tdoc_related_path(cpath):
            return
        root = self._canonical_path(resolve_tdoc_root_for_path(cpath, project_root=self.project_root))
        self._tdoc_pending_paths_by_root[root] = cpath
        timer = self._tdoc_validation_timers.get(root)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda r=root: self._flush_tdoc_validation(r))
            self._tdoc_validation_timers[root] = timer
        timer.start(max(120, int(delay_ms)))

    def _flush_tdoc_validation(self, root: str) -> None:
        target = self._tdoc_pending_paths_by_root.pop(root, "")
        if not target:
            return
        self._refresh_tdoc_diagnostics_for_path(target)

    def _refresh_tdoc_diagnostics_for_path(self, file_path: str) -> None:
        cpath = self._canonical_path(file_path)
        if not self._is_tdoc_related_path(cpath):
            return
        root, by_file = collect_tdoc_diagnostics(
            file_path=cpath,
            project_root=self.project_root,
            canonicalize=self._canonical_path,
            source="tdoc",
        )
        self._set_tdoc_diagnostics_for_root(root, by_file)

    def _refresh_all_tdoc_diagnostics(self) -> None:
        roots: set[str] = set()
        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not self._is_tdoc_related_path(path):
                continue
            root, by_file = collect_tdoc_diagnostics(
                file_path=path,
                project_root=self.project_root,
                canonicalize=self._canonical_path,
                source="tdoc",
            )
            roots.add(root)
            self._set_tdoc_diagnostics_for_root(root, by_file)
        stale_roots = [root for root in self._tdoc_diagnostics_by_root.keys() if root not in roots]
        for root in stale_roots:
            by_file = self._tdoc_diagnostics_by_root.pop(root, {})
            for file_path in by_file.keys():
                self._set_source_diagnostics_for_file(file_path, "tdoc", [])
        if not roots and not stale_roots:
            self._clear_all_tdoc_diagnostics()

    def _refresh_tdoc_diagnostics_for_project(self) -> None:
        candidates: list[str] = []
        visited_roots: set[str] = set()
        for walk_root, _dirs, files in os.walk(self.project_root):
            marker = os.path.join(walk_root, ".tdocproject")
            if ".tdocproject" in files:
                candidates.append(marker)
            has_doc = any(name.lower().endswith(".tdoc") for name in files)
            if has_doc:
                # one sample document path per folder is enough to resolve/project-validate the root
                sample = next((name for name in files if name.lower().endswith(".tdoc")), "")
                if sample:
                    candidates.append(os.path.join(walk_root, sample))

        if not candidates:
            self._clear_all_tdoc_diagnostics()
            return

        for candidate in candidates:
            root = self._canonical_path(resolve_tdoc_root_for_path(candidate, project_root=self.project_root))
            if root in visited_roots:
                continue
            visited_roots.add(root)
            self._refresh_tdoc_diagnostics_for_path(candidate)

    def _on_tdoc_file_link_requested(self, widget_ref, target: str) -> None:
        widget = widget_ref() if callable(widget_ref) else widget_ref
        if not isinstance(widget, TDocDocumentWidget):
            return
        target_path, jump_line = widget.resolve_file_link_target(target)
        if not target_path:
            return
        if not os.path.exists(target_path):
            answer = QMessageBox.question(
                self,
                "Missing TDOC File",
                f"Create linked file?\n\n{target_path}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                Path(target_path).write_text("", encoding="utf-8")
            except Exception as exc:
                QMessageBox.warning(self, "TDOC", f"Could not create file:\n{exc}")
                return
            self.refresh_subtree(os.path.dirname(target_path))
        self.open_file(target_path)
        opened = self._find_open_document_for_path(target_path)
        if isinstance(opened, TDocDocumentWidget) and jump_line:
            opened.jump_to_line(jump_line, 1)
        elif isinstance(opened, EditorWidget) and jump_line:
            block = opened.document().findBlockByNumber(max(0, int(jump_line) - 1))
            if block.isValid():
                cursor = QTextCursor(block)
                opened.setTextCursor(cursor)
                opened.centerCursor()

    def _on_tdoc_symbol_link_requested(self, widget_ref, symbol: str) -> None:
        widget = widget_ref() if callable(widget_ref) else widget_ref
        if not isinstance(widget, TDocDocumentWidget):
            return
        index_path = widget.ensure_index_file()
        if not index_path:
            QMessageBox.information(
                self,
                "TDOC Index",
                "No TDOC project marker found. Create a .tdocproject file first.",
            )
            return
        self.open_file(index_path)
        opened = self._find_open_document_for_path(index_path)
        if isinstance(opened, TDocDocumentWidget):
            opened.jump_to_symbol(symbol)

    def _tdoc_root_for_widget(self, widget: TDocDocumentWidget | None) -> str:
        if not isinstance(widget, TDocDocumentWidget):
            return ""
        root = str(widget.tdoc_root or "").strip()
        if root:
            return self._canonical_path(root)
        path = self._document_widget_path(widget)
        if not path:
            return ""
        return self._canonical_path(resolve_tdoc_root_for_path(path, project_root=self.project_root))

    def _open_tdoc_roots(self) -> list[str]:
        roots: list[str] = []
        seen: set[str] = set()
        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            cpath = self._canonical_path(path)
            if not self._is_tdoc_related_path(cpath):
                continue
            if isinstance(widget, TDocDocumentWidget) and str(widget.tdoc_root or "").strip():
                root = self._canonical_path(str(widget.tdoc_root))
            else:
                root = self._canonical_path(resolve_tdoc_root_for_path(cpath, project_root=self.project_root))
            if root in seen:
                continue
            seen.add(root)
            roots.append(root)
        return roots

    def build_open_tdoc_indexes(self) -> None:
        roots = self._open_tdoc_roots()
        if not roots:
            self.statusBar().showMessage("No open TDOC documents.", 1800)
            return

        built = 0
        missing_marker = 0
        refreshed_roots: set[str] = set()
        for root in roots:
            if not self._save_dirty_tdoc_documents_for_root(root):
                return
            if not TDocProjectIndex.has_project_marker(root):
                missing_marker += 1
                continue
            out = TDocProjectIndex.build_index(root)
            if out is None:
                continue
            built += 1
            refreshed_roots.add(root)
            self._reload_open_tdoc_documents_for_root(root)
            marker_path = self._canonical_path(str(Path(root) / PROJECT_MARKER_FILENAME))
            self._refresh_tdoc_diagnostics_for_path(marker_path if os.path.exists(marker_path) else str(out))

        for root in refreshed_roots:
            self.refresh_subtree(root)
        if refreshed_roots:
            self.schedule_git_status_refresh(delay_ms=90)

        if built and missing_marker:
            self.statusBar().showMessage(
                f"Built TDOC index for {built} project(s); {missing_marker} missing .tdocproject.",
                3200,
            )
            return
        if built:
            self.statusBar().showMessage(f"Built TDOC index for {built} project(s).", 2200)
            return
        if missing_marker:
            self.statusBar().showMessage(
                "TDOC index not built: no .tdocproject found for open TDOC files.",
                3200,
            )
            return
        self.statusBar().showMessage("TDOC index build failed.", 2200)

    def _save_dirty_tdoc_documents_for_root(self, root: str) -> bool:
        root_c = self._canonical_path(root)
        seen: set[str] = set()
        refresh_dirs: set[str] = set()
        saved_count = 0

        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            cpath = self._canonical_path(path)
            if not self._path_has_prefix(cpath, root_c):
                continue
            if not self._is_tdoc_related_path(cpath):
                continue

            key = cpath if not isinstance(widget, EditorWidget) else self._doc_key_for_editor(widget)
            if key in seen:
                continue
            seen.add(key)

            doc_getter = getattr(widget, "document", None)
            if not callable(doc_getter):
                continue
            try:
                doc = doc_getter()
                modified = bool(doc.isModified())
            except Exception:
                continue
            if not modified:
                continue

            saver = getattr(widget, "save_file", None)
            if not callable(saver):
                continue
            if not saver():
                self.statusBar().showMessage(
                    f"TDOC operation canceled: could not save {os.path.basename(cpath)}.",
                    2600,
                )
                return False

            saved_count += 1
            self._note_editor_saved(widget, source="tdoc operation pre-save")
            refresh_dirs.add(os.path.dirname(cpath))

        for folder in refresh_dirs:
            self.refresh_subtree(folder)
        if saved_count:
            self.schedule_git_status_refresh(delay_ms=90)
        return True

    def _reload_open_tdoc_documents_for_root(self, root: str) -> None:
        root_c = self._canonical_path(root)
        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            cpath = self._canonical_path(path)
            if not self._path_has_prefix(cpath, root_c):
                continue
            if not self._is_tdoc_related_path(cpath):
                continue
            if not os.path.exists(cpath):
                continue

            loaded = False
            if isinstance(widget, EditorWidget):
                try:
                    widget.load_file(cpath)
                    loaded = True
                except Exception:
                    loaded = False
            else:
                loader = getattr(widget, "load_file", None)
                if callable(loader):
                    try:
                        loaded = bool(loader(cpath))
                    except Exception:
                        loaded = False
            if not loaded and callable(getattr(widget, "setPlainText", None)):
                try:
                    widget.setPlainText(Path(cpath).read_text(encoding="utf-8"))
                    doc_getter = getattr(widget, "document", None)
                    if callable(doc_getter):
                        try:
                            doc_getter().setModified(False)
                        except Exception:
                            pass
                    loaded = True
                except Exception:
                    loaded = False

            if not loaded:
                continue

            for tabs in self.editor_workspace.all_tabs():
                idx = tabs.indexOf(widget)
                if idx >= 0:
                    tabs._refresh_tab_title(widget)
                    break

    def _on_tdoc_rename_alias_requested(self, widget_ref, old_alias: str) -> None:
        widget = widget_ref() if callable(widget_ref) else widget_ref
        if not isinstance(widget, TDocDocumentWidget):
            return

        old = str(old_alias or "").strip()
        if not old:
            return
        linked_file, _ = parse_file_link(old)
        if linked_file:
            return

        root = self._tdoc_root_for_widget(widget)
        if not root or not TDocProjectIndex.has_project_marker(root):
            QMessageBox.information(
                self,
                "Rename TDOC Alias",
                "No TDOC project marker found. Create a .tdocproject file first.",
            )
            return

        new_alias, ok = QInputDialog.getText(
            self,
            "Rename TDOC Alias",
            f"Rename alias '{old}' to:",
            text=old,
        )
        if not ok:
            return
        new_alias = str(new_alias or "").strip()
        if not new_alias:
            QMessageBox.warning(self, "Rename TDOC Alias", "Alias cannot be empty.")
            return
        if parse_file_link(new_alias)[0]:
            QMessageBox.warning(self, "Rename TDOC Alias", "Alias cannot be a file-link value.")
            return
        if new_alias.casefold() == old.casefold():
            return

        if not self._save_dirty_tdoc_documents_for_root(root):
            return

        marker_changed = TDocProjectIndex.rename_alias_in_marker(root, old, new_alias)
        docs_changed = TDocProjectIndex.rename_alias_in_documents(root, old, new_alias)

        if not marker_changed and docs_changed == 0:
            QMessageBox.information(self, "Rename TDOC Alias", f"No matches found for '{old}'.")
            return

        TDocProjectIndex.build_index(root)
        self._reload_open_tdoc_documents_for_root(root)
        marker_path = self._canonical_path(str(Path(root) / PROJECT_MARKER_FILENAME))
        if os.path.exists(marker_path):
            self._refresh_tdoc_diagnostics_for_path(marker_path)
        else:
            self._refresh_tdoc_diagnostics_for_path(widget.file_path or root)
        self.refresh_subtree(root)
        self.schedule_git_status_refresh(delay_ms=90)

        QMessageBox.information(
            self,
            "Rename TDOC Alias",
            f"Renamed '{old}' to '{new_alias}'. Updated {docs_changed} document file(s).",
        )

    def _on_tdoc_normalize_symbol_requested(self, widget_ref, symbol_or_alias: str) -> None:
        widget = widget_ref() if callable(widget_ref) else widget_ref
        if not isinstance(widget, TDocDocumentWidget):
            return

        current = str(symbol_or_alias or "").strip()
        if not current:
            return
        linked_file, _ = parse_file_link(current)
        if linked_file:
            return

        root = self._tdoc_root_for_widget(widget)
        if not root or not TDocProjectIndex.has_project_marker(root):
            QMessageBox.information(
                self,
                "Normalize TDOC Symbol",
                "No TDOC project marker found. Create a .tdocproject file first.",
            )
            return

        if not self._save_dirty_tdoc_documents_for_root(root):
            return

        alias_to_symbol, _sym, _sec, _inc, _ign, _meta = TDocProjectIndex.load_aliases(root)
        canonical_symbol = alias_to_symbol.get(current.casefold())
        if not canonical_symbol:
            QMessageBox.information(
                self,
                "Normalize TDOC Symbol",
                f"'{current}' is not defined in .tdocproject.",
            )
            return

        touched_files, replacements = TDocProjectIndex.normalize_symbol_in_documents(
            root,
            alias_to_symbol,
            canonical_symbol,
        )

        TDocProjectIndex.build_index(root)
        self._reload_open_tdoc_documents_for_root(root)
        marker_path = self._canonical_path(str(Path(root) / PROJECT_MARKER_FILENAME))
        if os.path.exists(marker_path):
            self._refresh_tdoc_diagnostics_for_path(marker_path)
        else:
            self._refresh_tdoc_diagnostics_for_path(widget.file_path or root)
        self.refresh_subtree(root)
        self.schedule_git_status_refresh(delay_ms=90)

        QMessageBox.information(
            self,
            "Normalize TDOC Symbol",
            (
                f"Normalized '{canonical_symbol}'. Updated {replacements} link(s) "
                f"across {touched_files} file(s)."
            ),
        )

    def _open_tdoc_file(self, file_path: str) -> TDocDocumentWidget | None:
        cpath = self._canonical_path(file_path)
        existing = self._find_open_document_for_path(cpath)
        if isinstance(existing, TDocDocumentWidget):
            self._focus_document_widget(existing)
            self._schedule_tdoc_validation(cpath, delay_ms=0)
            return existing

        widget = TDocDocumentWidget(
            file_path=cpath,
            project_root=self.project_root,
            canonicalize=self._canonical_path,
        )
        if not widget.file_path:
            return None
        self._apply_editor_background_to_editor(widget)
        try:
            widget.set_editor_font_preferences(
                family=str(self.font_family or "").strip(),
                point_size=int(self.font_size),
            )
        except Exception:
            pass

        ref = weakref.ref(widget)
        widget.open_file_by_name = lambda target, w=ref: self._on_tdoc_file_link_requested(w, target)
        widget.open_symbol = lambda symbol, w=ref: self._on_tdoc_symbol_link_requested(w, symbol)
        widget.rename_alias = lambda label, w=ref: self._on_tdoc_rename_alias_requested(w, label)
        widget.normalize_symbol = lambda label, w=ref: self._on_tdoc_normalize_symbol_requested(w, label)
        font_step_signal = getattr(widget, "editorFontSizeStepRequested", None)
        if font_step_signal is not None and hasattr(font_step_signal, "connect"):
            font_step_signal.connect(lambda step, w=ref: self._on_editor_font_size_step_requested(w, step))
        def _on_tdoc_changed(wref=ref):
            obj = wref()
            if not isinstance(obj, TDocDocumentWidget):
                return
            path = self._document_widget_path(obj)
            if path:
                self._schedule_tdoc_validation(path)
        widget.textChanged.connect(_on_tdoc_changed)
        widget.textChanged.connect(self._schedule_autosave)

        tabs = self.editor_workspace._current_tabs() or self.editor_workspace._ensure_one_main_tabs()
        tabs.add_editor(widget)
        self._schedule_tdoc_validation(cpath, delay_ms=0)
        return widget

    def _save_editor_for_run(self, ed: EditorWidget) -> str | None:
        if not ed.file_path:
            self.statusBar().showMessage("Run canceled: unsaved editors are not supported.", 2200)
            return None

        if self._run_config().get("auto_save_before_run", True) and ed.document().isModified():
            if not ed.save_file():
                return None
            self._note_editor_saved(ed, source="save for run")
            self._attach_editor_lint_hooks(ed)
            self._request_lint_for_editor(ed, reason="save", include_source_if_modified=False)

        return self._canonical_path(ed.file_path)

    def _save_all_dirty_editors_for_run(self) -> bool:
        if not bool(self._run_config().get("auto_save_before_run", True)):
            return True

        dirty_editors: list[EditorWidget] = []
        seen_docs: set[str] = set()
        for ed in self.editor_workspace.all_editors():
            if not isinstance(ed, EditorWidget):
                continue
            doc_key = self._doc_key_for_editor(ed)
            if doc_key in seen_docs:
                continue
            seen_docs.add(doc_key)
            if ed.document().isModified():
                dirty_editors.append(ed)
        if not dirty_editors:
            return True

        for ed in dirty_editors:
            self._focus_editor(ed)
            saved_path = self._save_editor_for_run(ed)
            if not saved_path:
                self.statusBar().showMessage("Run canceled: save was canceled or failed.", 2200)
                return False
            self.refresh_subtree(os.path.dirname(saved_path))

        self.statusBar().showMessage(f"Saved {len(dirty_editors)} modified file(s).", 1400)
        return True

    def _refresh_editor_title(self, ed: EditorWidget):
        target_key = self._doc_key_for_editor(ed)
        for candidate in self.editor_workspace.all_editors():
            if self._doc_key_for_editor(candidate) != target_key:
                continue
            for tabs in self.editor_workspace.all_tabs():
                idx = tabs.indexOf(candidate)
                if idx >= 0:
                    tabs._refresh_tab_title(candidate)

    def _update_open_editors_for_move(self, old_path: str, new_path: str):
        old_c = self._canonical_path(old_path)
        new_c = self._canonical_path(new_path)
        moved_editors: list[EditorWidget] = []
        processed_code_docs: set[str] = set()

        for widget in self._iter_open_document_widgets():
            if isinstance(widget, EditorWidget):
                doc_key = self._doc_key_for_editor(widget)
                if doc_key in processed_code_docs:
                    continue
                processed_code_docs.add(doc_key)

            current_path = self._document_widget_path(widget)
            if not current_path:
                continue
            src = self._canonical_path(current_path)
            if src == old_c:
                relocated = new_c
            elif self._path_has_prefix(src, old_c):
                try:
                    suffix = os.path.relpath(src, old_c)
                except Exception:
                    continue
                relocated = self._canonical_path(os.path.join(new_c, suffix))
            else:
                continue

            if isinstance(widget, EditorWidget):
                widget.file_path = relocated
                widget.set_file_path(relocated)
                self._refresh_editor_title(widget)
                moved_editors.append(widget)
            elif hasattr(widget, "set_file_path"):
                try:
                    widget.set_file_path(relocated)
                except Exception:
                    pass
                for tabs in self.editor_workspace.all_tabs():
                    idx = tabs.indexOf(widget)
                    if idx >= 0:
                        tabs._refresh_tab_title(widget)
                        break

        self.lint_manager.clear_paths_under(old_c)
        self._clear_tdoc_diagnostics_for_path(old_c)
        for ed in moved_editors:
            self._attach_editor_lint_hooks(ed)
            self._request_lint_for_editor(ed, reason="open", include_source_if_modified=True)
        self._refresh_all_tdoc_diagnostics()

    def _detach_deleted_editors(self, deleted_path: str):
        deleted_c = self._canonical_path(deleted_path)
        self.lint_manager.clear_paths_under(deleted_c)
        widgets_to_close: set[QWidget] = set()
        seen_code_doc_keys: set[str] = set()

        for widget in self._iter_open_document_widgets():
            if isinstance(widget, EditorWidget):
                doc_key = self._doc_key_for_editor(widget)
                if doc_key in seen_code_doc_keys:
                    continue
                seen_code_doc_keys.add(doc_key)
            path = self._document_widget_path(widget)
            if not path:
                continue
            src = self._canonical_path(path)
            if src == deleted_c or self._path_has_prefix(src, deleted_c):
                widgets_to_close.add(widget)

        if not widgets_to_close:
            return

        for tabs in list(self.editor_workspace.all_tabs()):
            for idx in range(tabs.count() - 1, -1, -1):
                widget = tabs.widget(idx)
                if widget not in widgets_to_close:
                    continue
                tabs.removeTab(idx)
                widget.hide()
                if isinstance(widget, EditorWidget):
                    self.editor_workspace.release_document_view(widget, self._doc_key_for_editor(widget))
                widget.deleteLater()
            owner = getattr(tabs, "owner_window", None)
            if owner is not None and tabs.count() == 0:
                try:
                    owner.close()
                except Exception:
                    pass
        self._clear_tdoc_diagnostics_for_path(deleted_c)
        self._refresh_all_tdoc_diagnostics()
        self.editor_workspace.request_cleanup_empty_panes()
        self._refresh_runtime_action_states()

    # ---------- Actions ----------

    def new_file(self):
        self.project_lifecycle_controller.new_file()

    def open_file_dialog(self):
        self.project_lifecycle_controller.open_file_dialog()

    def open_project_dialog(self):
        self.project_lifecycle_controller.open_project_dialog()

    def open_new_project_dialog(self) -> None:
        self.project_lifecycle_controller.open_new_project_dialog()

    def _ask_project_open_mode(self, target_project: str) -> str | None:
        return self.project_lifecycle_controller._ask_project_open_mode(target_project)

    def _confirm_save_modified_editors(self) -> bool:
        return self.project_lifecycle_controller._confirm_save_modified_editors()

    def _launch_project_window(self, project_path: str) -> bool:
        return self.project_lifecycle_controller._launch_project_window(project_path)

    def _launch_no_project_window(self) -> bool:
        return self.project_lifecycle_controller._launch_no_project_window()

    def _wait_for_project_instance(self, project_path: str, timeout_s: float = 2.0) -> bool:
        return self.project_lifecycle_controller._wait_for_project_instance(project_path, timeout_s=timeout_s)

    def _open_project_in_current_window(self, project_path: str) -> bool:
        return self.project_lifecycle_controller._open_project_in_current_window(project_path)

    def close_project(self) -> None:
        self.project_lifecycle_controller.close_project()

    def open_project_path(self, path: str):
        self.project_lifecycle_controller.open_project_path(path)

    def open_clone_repository_dialog(self) -> None:
        self.git_workflow_controller.open_clone_repository_dialog()

    def open_share_to_github_dialog(self) -> None:
        self.git_workflow_controller.open_share_to_github_dialog()

    def open_git_commit_dialog(self, *, prefer_push_action: bool = False) -> None:
        self.git_workflow_controller.open_git_commit_dialog(prefer_push_action=prefer_push_action)

    def push_current_branch(self) -> None:
        self.git_workflow_controller.push_current_branch()

    def fetch_remote(self) -> None:
        self.git_workflow_controller.fetch_remote()

    def pull_current_branch(self) -> None:
        self.git_workflow_controller.pull_current_branch()

    def run_git_preflight_check(self) -> None:
        self.git_workflow_controller.run_push_preflight_check()

    def open_git_branches_dialog(self) -> None:
        self.git_workflow_controller.open_git_branches_dialog()

    def open_git_releases_dialog(self) -> None:
        self.git_workflow_controller.open_git_releases_dialog()

    def rollback_file_changes(self, file_path: str) -> None:
        self.git_workflow_controller.rollback_file_changes(file_path)

    def rollback_discard_unstaged(self) -> None:
        self.git_workflow_controller.rollback_discard_unstaged()

    def rollback_unstage_all(self) -> None:
        self.git_workflow_controller.rollback_unstage_all()

    def rollback_hard_reset_head(self) -> None:
        self.git_workflow_controller.rollback_hard_reset_head()

    def track_paths_in_git(self, paths: list[str]) -> None:
        self.git_workflow_controller.track_paths_in_git(paths)

    def stage_paths_in_git(self, paths: list[str], label: str = "path") -> None:
        self.git_workflow_controller.stage_paths_in_git(paths, label=label)

    def unstage_paths_in_git(self, paths: list[str], label: str = "path") -> None:
        self.git_workflow_controller.unstage_paths_in_git(paths, label=label)

    def stage_all_changes(self) -> None:
        self.git_workflow_controller.stage_all_changes()

    def _to_repo_rel_paths(self, repo_root: str, paths: list[str]) -> list[str]:
        return self.git_workflow_controller._to_repo_rel_paths(repo_root, paths)

    def open_file(self, path: str):
        cpath = self._canonical_path(path)
        if os.path.isdir(cpath):
            return

        existing = self._find_open_document_for_path(cpath)
        if existing:
            if isinstance(existing, EditorWidget):
                self._attach_editor_lint_hooks(existing)
                self._schedule_symbol_outline_refresh(immediate=True)
            self._focus_document_widget(existing)
            if self._is_tdoc_related_path(cpath):
                self._schedule_tdoc_validation(cpath, delay_ms=0)
            self.statusBar().showMessage(f"Focused already-open file: {cpath}", 1800)
            self._refresh_runtime_action_states()
            return

        if self._is_tdoc_path(cpath):
            opened_tdoc = self._open_tdoc_file(cpath)
            if opened_tdoc is not None:
                self._refresh_runtime_action_states()
                QTimer.singleShot(0, self.apply_default_layout)
                QTimer.singleShot(80, self.apply_default_layout)
                return

        self.editor_workspace.open_editor(
            os.path.basename(cpath),
            cpath,
            font_size=self.font_size,
            font_family=self.font_family,
        )
        ed = self.current_editor()
        if ed:
            ed.file_path = cpath
            try:
                ed.configure_keybindings(self._keybindings_config())
            except Exception:
                pass
            self._assign_dock_identity(ed)
            self._attach_editor_lint_hooks(ed)
            self._schedule_symbol_outline_refresh(immediate=True)
            if self._is_tdoc_related_path(cpath):
                self._schedule_tdoc_validation(cpath, delay_ms=0)
        self._refresh_runtime_action_states()

        QTimer.singleShot(0, self.apply_default_layout)
        QTimer.singleShot(80, self.apply_default_layout)

    def current_editor(self):
        return self.editor_workspace.active_editor()

    def save_current_editor(self):
        widget = self._current_document_widget()
        if widget is None:
            self.statusBar().showMessage("No active editor.", 1500)
            return

        path = self._document_widget_path(widget)
        if not path:
            self.statusBar().showMessage("Cannot save: editor has no backing file.", 2200)
            return

        if not callable(getattr(widget, "save_file", None)):
            self.statusBar().showMessage("This tab cannot be saved.", 2200)
            return

        if not widget.save_file():
            return

        cpath = self._canonical_path(path)
        self._note_editor_saved(widget, source="manual save")
        if isinstance(widget, EditorWidget):
            self._assign_dock_identity(widget)
            self._attach_editor_lint_hooks(widget)
            self._request_lint_for_editor(widget, reason="save", include_source_if_modified=False)
        if self._is_tdoc_related_path(cpath):
            self._schedule_tdoc_validation(cpath, delay_ms=0)
        self.refresh_subtree(os.path.dirname(cpath))
        self.schedule_git_status_refresh(delay_ms=90)

    def save_current_editor_as(self):
        widget = self._current_document_widget()
        if widget is None:
            self.statusBar().showMessage("No active editor.", 1500)
            return

        path, _ = get_save_file_name(
            parent=self,
            manager=self.settings_manager,
            caption="Save File As",
            directory=self.project_root,
        )
        if not path:
            return

        old_path = self._canonical_path(self._document_widget_path(widget)) if self._document_widget_path(widget) else None
        new_path = self._canonical_path(path)

        if isinstance(widget, EditorWidget):
            widget.file_path = new_path
        elif hasattr(widget, "set_file_path"):
            try:
                widget.set_file_path(new_path)
            except Exception:
                self.statusBar().showMessage("Could not update target path for this tab.", 2200)
                return
        else:
            setattr(widget, "file_path", new_path)

        saver = getattr(widget, "save_file", None)
        if not callable(saver):
            self.statusBar().showMessage("This tab cannot be saved.", 2200)
            return
        if not saver():
            return

        self._note_editor_saved(widget, source="save as")
        if isinstance(widget, EditorWidget):
            self._assign_dock_identity(widget)
            if old_path and old_path != new_path:
                self.lint_manager.clear_file(old_path)
            self._attach_editor_lint_hooks(widget)
            self._request_lint_for_editor(widget, reason="save", include_source_if_modified=False)
        elif old_path and old_path != new_path:
            self._clear_tdoc_diagnostics_for_path(old_path)
        if old_path:
            self.refresh_subtree(os.path.dirname(old_path))
        self.refresh_subtree(os.path.dirname(new_path))
        if self._is_tdoc_related_path(new_path):
            self._schedule_tdoc_validation(new_path, delay_ms=0)
        self.schedule_git_status_refresh(delay_ms=90)

    def close_active_editor(self):
        widget = self._current_document_widget()
        if widget is None:
            return

        tabs = None
        w = widget.parentWidget()
        while w is not None:
            if isinstance(w, EditorTabs):
                tabs = w
                break
            w = w.parentWidget()

        if tabs is None:
            return

        idx = tabs.indexOf(widget)
        if idx < 0:
            return

        tabs._on_tab_close_requested(idx)
        self._refresh_runtime_action_states()
        QTimer.singleShot(60, self.apply_default_layout)

    def split_editor_right(self):
        if not self.current_editor():
            return
        self.editor_workspace.split_editor_right(self.font_size, self.font_family)
        self._attach_all_editor_lint_hooks()
        QTimer.singleShot(0, self.apply_default_layout)

    def split_editor_down(self):
        if not self.current_editor():
            return
        self.editor_workspace.split_editor_down(self.font_size, self.font_family)
        self._attach_all_editor_lint_hooks()
        QTimer.singleShot(0, self.apply_default_layout)

    def populate_build_config_menu(self) -> None:
        menu = self._run_build_config_menu
        if menu is None:
            return
        menu.clear()

        names = self.execution_controller.cmake_build_config_names()
        active_name = self.execution_controller.active_cmake_build_config_name()
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._run_build_config_action_group = group

        if names:
            for name in names:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name.lower() == active_name.lower())
                action.triggered.connect(
                    lambda checked=False, cfg_name=name: self.execution_controller.set_active_cmake_build_config(cfg_name)
                )
                group.addAction(action)
        else:
            empty = menu.addAction("No Build Configurations")
            empty.setEnabled(False)

        menu.addSeparator()
        manage = menu.addAction("Manage Build Configurations...")
        manage.triggered.connect(lambda _checked=False: self.open_settings(initial_page_id="project-build-configs"))

    def _populate_python_run_menu(self, menu: QMenu) -> None:
        menu.clear()
        active_name = self.execution_controller.active_python_run_config_name()
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._run_python_config_action_group = group

        run_current = menu.addAction("Run Current File")
        run_current.setCheckable(True)
        run_current.setChecked(not active_name)
        run_current.triggered.connect(
            lambda _checked=False: self._set_python_run_target_current_file(run_now=True)
        )
        group.addAction(run_current)

        names = self.execution_controller.python_run_config_names()

        if names:
            menu.addSeparator()
            for name in names:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name.lower() == active_name.lower())
                action.triggered.connect(
                    lambda checked=False, cfg_name=name: self.run_python_config(cfg_name, set_active=True)
                )
                group.addAction(action)

        menu.addSeparator()
        manage = menu.addAction("Manage Run Configurations...")
        manage.triggered.connect(lambda _checked=False: self.open_settings(initial_page_id="project-run-configs"))

    def _populate_cargo_run_menu(self, menu: QMenu) -> None:
        menu.clear()
        active_name = self.execution_controller.active_rust_run_config_name()
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._run_cargo_config_action_group = group

        run_current = menu.addAction("Run Cargo Project (Current Context)")
        run_current.setCheckable(True)
        run_current.setChecked(not active_name)
        run_current.triggered.connect(
            lambda _checked=False: self._set_rust_run_target_default(run_now=True)
        )
        group.addAction(run_current)

        names = self.execution_controller.rust_run_config_names()
        if names:
            menu.addSeparator()
            for name in names:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name.lower() == active_name.lower())
                action.triggered.connect(
                    lambda checked=False, cfg_name=name: self.run_cargo_config(cfg_name, set_active=True)
                )
                group.addAction(action)

        menu.addSeparator()
        manage = menu.addAction("Manage Cargo Configurations...")
        manage.triggered.connect(lambda _checked=False: self.open_settings(initial_page_id="project-rust-run-configs"))

    def populate_python_run_config_menu(self) -> None:
        menu = self._run_python_config_menu
        if menu is None:
            return
        self._populate_python_run_menu(menu)

    def populate_cargo_run_config_menu(self) -> None:
        menu = self._run_cargo_config_menu
        if menu is None:
            return
        self._populate_cargo_run_menu(menu)

    def populate_toolbar_python_run_menu(self) -> None:
        menu = self._toolbar_run_menu
        if menu is None:
            return
        self._populate_python_run_menu(menu)
        menu.addSeparator()
        cargo = menu.addMenu("Cargo Configurations")
        self._populate_cargo_run_menu(cargo)

    def run_python_config(self, config_name: str, *, set_active: bool = False) -> None:
        self.execution_controller.run_named_python_config(config_name, set_active=bool(set_active))

    def _set_python_run_target_current_file(self, *, run_now: bool = False) -> None:
        if not self.execution_controller.set_active_python_run_config(""):
            return
        if run_now:
            self.execution_controller.run_current_file()

    def _set_rust_run_target_default(self, *, run_now: bool = False) -> None:
        if not self.execution_controller.set_active_rust_run_config(""):
            return
        if run_now:
            self.execution_controller.run_current_file()

    def run_cargo_config(self, config_name: str, *, set_active: bool = False) -> None:
        self.execution_controller.run_named_rust_config(config_name, set_active=bool(set_active))

    def run_primary_python_target(self) -> None:
        active_config = self.execution_controller.active_python_run_config_name()
        if active_config:
            self.execution_controller.run_named_python_config(active_config, set_active=False)
            return
        active_rust = self.execution_controller.active_rust_run_config_name()
        if active_rust:
            self.execution_controller.run_named_rust_config(active_rust, set_active=False)
            return
        self.execution_controller.run_current_file()

    def run_current_file(self):
        self.execution_controller.run_current_file()

    def build_current_file(self):
        self.execution_controller.build_current_file()

    def build_and_run_current_file(self):
        self.execution_controller.build_and_run_current_file()

    def rerun_current_file(self):
        self.execution_controller.rerun_current_file()

    def stop_current_run(self):
        self.execution_controller.stop_current_run()

    def new_terminal_tab(self):
        self.execution_controller.new_terminal_tab()

    def _on_console_tab_close_requested(self, index: int):
        self.execution_controller._on_console_tab_close_requested(index)

    def _close_console_tab_at(self, index: int) -> bool:
        return self.execution_controller._close_console_tab_at(index)

    def _close_console_terminal(self, terminal: TerminalWidget) -> bool:
        return self.execution_controller._close_console_terminal(terminal)

    def close_active_terminal_tab(self):
        self.execution_controller.close_active_terminal_tab()

    def open_settings(self, initial_page_id: str | None = None):

        before = self.settings_manager.export_legacy_config()
        dlg = ScopedSettingsDialog(
            manager=self.settings_manager,
            schema=create_default_settings_schema(theme_options=self.available_themes()),
            initial_page_id=initial_page_id,
            on_applied=self._refresh_runtime_settings_from_manager,
            use_native_chrome=self.use_native_chrome,
            parent=self,
        )
        dlg.exec()
        self._refresh_runtime_settings_from_manager()
        if self.config != before:
            self.statusBar().showMessage("Settings updated.", 1600)

    def open_project_config(self):
        self.open_file(self.project_config_path)

    def _refresh_runtime_settings_from_manager(self):
        self.config = self.settings_manager.export_legacy_config()
        self._apply_application_identity()
        self._set_editor_font_size(
            int(self.settings_manager.get("font_size", scope_preference="ide", default=10)),
            persist=False,
            announce=False,
        )
        self._set_editor_font_family(
            self.settings_manager.get("font_family", scope_preference="ide", default=""),
            persist=False,
            announce=False,
        )
        # Ensure already-open editors stay in sync with current font settings.
        self._apply_editor_font_settings_to_all()
        self.theme_name = str(self.settings_manager.get("theme", scope_preference="ide", default="Dark"))
        self.apply_selected_theme()
        self._configure_autosave_timer()
        self._set_recent_projects(self._recent_projects(), save=True)
        self._refresh_recent_projects_menu()

        desired_chrome = bool(
            self.settings_manager.get(
                "window.use_native_chrome",
                scope_preference="ide",
                default=False,
            )
        )
        if self.use_native_chrome != desired_chrome:
            self.set_chrome_mode(desired_chrome)
        self._apply_custom_toolbar_window_settings()
        self._sync_titlebar_ai_toggle()
        self._update_toolbar_run_controls()
        self.populate_python_run_config_menu()
        self.populate_toolbar_python_run_menu()
        self.populate_cargo_run_config_menu()
        self.populate_build_config_menu()
        self._apply_runtime_keybindings()

        lint_cfg = self._lint_config()
        completion_cfg = self._completion_config()
        cpp_cfg = self._cpp_config()
        rust_cfg = self._rust_config()
        ai_cfg = self._ai_assist_config()
        self.lint_manager.update_settings(lint_cfg)
        self.language_service_hub.update_settings(completion_cfg)
        self.cpp_language_pack.update_project_settings(cpp_cfg)
        self.rust_language_pack.update_project_settings(rust_cfg)
        self.inline_suggestion_controller.update_settings(ai_cfg)
        self._apply_git_tinting_config()
        self._configure_git_poll_timer()
        self.schedule_git_status_refresh(delay_ms=80)

        for ed in self.editor_workspace.all_editors():
            self._apply_editor_background_to_editor(ed)
            self._apply_completion_ui_settings_to_editor(ed)
            self._apply_lint_visual_settings_to_editor(ed)
            self._attach_editor_cpp_hooks(ed)
            self._attach_editor_rust_hooks(ed)
            if not bool(ai_cfg.get("enabled", False)):
                ed.clear_inline_suggestion()
        for widget in self._iter_open_document_widgets():
            if isinstance(widget, TDocDocumentWidget):
                self._apply_editor_background_to_editor(widget)

        if lint_cfg.get("enabled", True):
            ed = self.current_editor()
            if ed and ed.file_path:
                self._request_lint_for_editor(ed, reason="manual", include_source_if_modified=True)

    # ---------- Persistence (open docs only) ----------

    def _collect_open_editor_payload(self):
        return self.project_lifecycle_controller._collect_open_editor_payload()

    def _open_editor_payload_from_config(self, cfg: dict | None = None) -> list[dict]:
        return self.project_lifecycle_controller._open_editor_payload_from_config(cfg)

    def _close_all_editors_without_prompt(self) -> None:
        self.project_lifecycle_controller._close_all_editors_without_prompt()

    def _sync_open_editors_from_config(self, *, source: str) -> bool:
        return self.project_lifecycle_controller._sync_open_editors_from_config(source=source)

    def _reload_project_config_from_disk(self, *, source: str, honor_open_editors: bool = True) -> None:
        self.project_lifecycle_controller._reload_project_config_from_disk(
            source=source,
            honor_open_editors=honor_open_editors,
        )

    def restore_open_files_only(self):
        self.project_lifecycle_controller.restore_open_files_only()

    def save_session_to_config(self):
        self.project_lifecycle_controller.save_session_to_config()

    # ---------- Config ----------

    @classmethod
    def _default_ide_app_dir(cls) -> str:
        override = os.environ.get("PYTPO_IDE_APP_DIR", "").strip()
        if override:
            return str(Path(override).expanduser())
        app_root = Path(__file__).resolve().parents[2]
        return str(app_root / cls.IDE_SETTINGS_DIRNAME)

    @classmethod
    def _legacy_ide_app_dir(cls) -> Path:
        return Path.home() / cls.IDE_SETTINGS_DIRNAME

    @classmethod
    def _migrate_legacy_ide_settings_file(cls, target_dir: str) -> None:
        target_base = Path(target_dir).expanduser()
        source_base = cls._legacy_ide_app_dir()
        try:
            if target_base.resolve() == source_base.resolve():
                return
        except Exception:
            if str(target_base) == str(source_base):
                return

        source_file = source_base / "ide-settings.json"
        target_file = target_base / "ide-settings.json"
        if not source_file.exists() or target_file.exists():
            return

        try:
            target_base.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
        except Exception:
            # Best-effort migration; fallback is defaults in target directory.
            return

    def load_or_create_project_config(self) -> dict:
        self.settings_manager.load_all()
        self.project_config_path = self._canonical_path(str(self.settings_manager.project_path))
        self.ide_app_dir = self._canonical_path(str(self.settings_manager.paths.ide_app_dir))
        self.ide_settings_path = self._canonical_path(str(self.settings_manager.ide_path))
        return self.settings_manager.export_legacy_config()

    def write_project_config(self, cfg: dict):
        try:
            self.settings_manager.apply_legacy_config(cfg)
            self.settings_manager.save_all(only_dirty=True, allow_project_repair=True)
            self.config = self.settings_manager.export_legacy_config()
            self.project_config_path = self._canonical_path(str(self.settings_manager.project_path))
            self.ide_app_dir = self._canonical_path(str(self.settings_manager.paths.ide_app_dir))
            self.ide_settings_path = self._canonical_path(str(self.settings_manager.ide_path))
            sig = self._external_file_signature(self.project_config_path)
            if sig is not None:
                self._external_file_signatures[self.project_config_path] = sig
            self._external_conflict_signatures.pop(self.project_config_path, None)
        except Exception as e:
            QMessageBox.warning(self, "Config Error", f"Could not write project config:\n{e}")

    def themes_dir(self) -> Path:
        return self._shared_themes_dir()

    def _shared_themes_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / self.THEMES_DIRNAME

    def _theme_search_dirs(self) -> list[Path]:
        return self.theme_controller._theme_search_dirs()

    def _theme_candidates(self) -> list[tuple[str, Path]]:
        return self.theme_controller._theme_candidates()

    def available_themes(self) -> list[str]:
        return self.theme_controller.available_themes()

    def _resolve_theme_path(self, theme_name: str) -> tuple[str, Path] | None:
        return self.theme_controller._resolve_theme_path(theme_name)

    def apply_selected_theme(self) -> None:
        self.theme_controller.apply_selected_theme()

    # ---------- Layout ----------

    def apply_default_layout(self):
        if self._dock_layout_restored:
            return
        if self._is_resizable_docked(self.dock_project):
            self.resizeDocks([self.dock_project], [280], Qt.Horizontal)
        if self._is_resizable_docked(self.dock_outline):
            self.resizeDocks([self.dock_outline], [280], Qt.Horizontal)

        bottom = [
            d
            for d in (self.dock_debug, self.dock_terminal, self.dock_problems, self.dock_usages)
            if self._is_resizable_docked(d)
        ]
        if bottom:
            self.resizeDocks([bottom[0]], [140], Qt.Vertical)

    # ---------- Close ----------

    def closeEvent(self, event: QCloseEvent):
        if hasattr(self, "workspace_controller"):
            self.workspace_controller.stop()
        if hasattr(self, "version_control_controller"):
            self.version_control_controller.cleanup()
        skip_prompt = self._skip_close_save_prompt_once
        self._skip_close_save_prompt_once = False
        if not skip_prompt and not self._confirm_save_modified_editors():
            event.ignore()
            return

        self._project_fs_refresh_timer.stop()
        self._project_fs_watch_sync_timer.stop()
        watcher = self._project_fs_watcher
        if watcher is not None:
            try:
                dirs = [path for path in watcher.directories() if isinstance(path, str) and path]
                if dirs:
                    watcher.removePaths(dirs)
            except Exception:
                pass
            self._project_fs_watcher = None
            self._project_fs_watched_dirs.clear()
            self._project_fs_pending_dirs.clear()

        rename_token = int(getattr(self, "_active_rename_token", 0) or 0)
        if rename_token > 0:
            try:
                self.language_service_hub.cancel_references(rename_token)
            except Exception:
                pass
            self._active_rename_token = 0
            self._rename_request_meta.pop(rename_token, None)

        self.language_service_hub.shutdown()
        self.inline_suggestion_controller.shutdown()
        self.lint_manager.shutdown()
        if not skip_prompt and not self.no_project_mode:
            self._remember_recent_project(self.project_root, save=True)
        if self._instance_server is not None:
            self._instance_server.close()
            self._instance_server = None
        if not self.no_project_mode:
            self.save_session_to_config()
        self._persist_window_and_dock_layout()
        event.accept()

    def _project_display_name(self) -> str:
        if self.no_project_mode:
            return "Welcome"
        project_name = str(self.config.get("project_name") or "").strip()
        if project_name:
            return project_name
        fallback = str(Path(self.project_root).name or "").strip()
        return fallback or "Project"

    def _project_explorer_title(self) -> str:
        return f"Project Explorer ({self._project_display_name()})"

    def _apply_application_identity(self) -> None:
        title = f"{self.APP_NAME} [{self._project_display_name()}]"
        self.setWindowTitle(title)
        dock_project = getattr(self, "dock_project", None)
        if isinstance(dock_project, QDockWidget):
            dock_project.setWindowTitle(self._project_explorer_title())

        icon_path = self.app_icon_path()
        if icon_path.is_file():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
                app = QApplication.instance()
                if app is not None:
                    app.setWindowIcon(icon)

        app = QApplication.instance()
        if app is not None:
            app.setApplicationName(self.APP_NAME)
            app.setApplicationDisplayName(title)
