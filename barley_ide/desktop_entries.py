from __future__ import annotations

# Compatibility wrapper while callers move from the old module name.
from .desktop_integration import (
    APP_SPEC,
    DesktopEntrySpec,
    applications_dir,
    desktop_file_path,
    icon_dir,
    install_desktop_entry,
    installed_icon_path,
    installation_status,
    legacy_artifact_paths,
    main,
    parser,
    render_desktop_file,
    repo_root,
    resolve_icon_source,
    uninstall_desktop_entry,
    xdg_data_home,
)

