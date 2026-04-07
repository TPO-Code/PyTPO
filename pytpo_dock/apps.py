from __future__ import annotations

from grist.desktop_apps import (
    DESKTOP_APP_DRAG_MIME_TYPE,
    build_app_registry,
    launch_app,
    parse_desktop_app_drag_payload,
    parse_desktop_file,
)


def prettify_wm_class(wm_class: str) -> str:
    """Convert a WM_CLASS token into a readable fallback app name."""
    name = wm_class.split('.')[-1].replace('-', ' ').replace('_', ' ').strip()
    return name.title() if name else "Unknown App"


def build_runtime_window_app(window, *, path: str | None = None):
    """Build transient dock metadata for running windows without a desktop file."""
    wm_class = window.get('class', '')
    title = window.get('title', '').strip()
    app_name = str(window.get('app_name', '') or '').strip()
    icon_name = str(window.get('icon', '') or '').strip()
    return {
        'path': str(path or f"window://{window['id']}"),
        'Name': app_name or prettify_wm_class(wm_class),
        'Title': title,
        'Icon': icon_name or wm_class,
        'StartupWMClass': wm_class,
        'Exec': '',
        'runtime_only': True,
    }
