"""Qt-aware controllers used by the main IDE window."""

from .action_registry import ActionRegistry
from .diagnostics_controller import DiagnosticsController
from .execution_controller import ExecutionController
from .explorer_controller import ExplorerController
from .git_workflow_controller import GitWorkflowController
from .language_intelligence_controller import LanguageIntelligenceController
from .language_service_hub import LanguageServiceHub
from .project_context import ProjectContext
from .project_lifecycle_controller import ProjectLifecycleController
from .search_controller import SearchController
from .theme_controller import ThemeController
from .version_control_controller import VersionControlController
from .workspace_controller import WorkspaceController

__all__ = [
    "ActionRegistry",
    "DiagnosticsController",
    "ExecutionController",
    "ExplorerController",
    "GitWorkflowController",
    "LanguageIntelligenceController",
    "LanguageServiceHub",
    "ProjectContext",
    "ProjectLifecycleController",
    "SearchController",
    "ThemeController",
    "VersionControlController",
    "WorkspaceController",
]
