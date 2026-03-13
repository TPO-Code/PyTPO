from __future__ import annotations

import os
import re
import shlex
import subprocess


def parse_desktop_file(path):
    """Parse a Linux .desktop file and extract app metadata used by the dock."""
    app_info = {'path': path}
    in_entry = False
    try:
        with open(path, 'r', encoding='utf-8') as file_handle:
            for line in file_handle:
                line = line.strip()
                if line == '[Desktop Entry]':
                    in_entry = True
                elif line.startswith('[') and in_entry:
                    break
                elif in_entry and '=' in line:
                    key, value = line.split('=', 1)
                    if key in ['Name', 'Exec', 'Icon', 'StartupWMClass', 'Type', 'NoDisplay']:
                        app_info[key] = value
    except Exception:
        pass
    return app_info


def build_app_registry():
    """Scan standard directories for .desktop files and register applications."""
    registry = {}
    directories = ['/usr/share/applications', os.path.expanduser('~/.local/share/applications')]

    for directory in directories:
        if not os.path.exists(directory):
            continue
        for file_name in os.listdir(directory):
            if not file_name.endswith('.desktop'):
                continue
            path = os.path.join(directory, file_name)
            info = parse_desktop_file(path)
            if info.get('Type') != 'Application':
                continue
            if info.get('NoDisplay', 'false').lower() == 'true':
                continue

            wm_class = info.get('StartupWMClass', '').lower()
            if wm_class:
                registry[wm_class] = info
            registry[file_name.lower().replace('.desktop', '')] = info
    return registry


def prettify_wm_class(wm_class: str) -> str:
    """Convert a WM_CLASS token into a readable fallback app name."""
    name = wm_class.split('.')[-1].replace('-', ' ').replace('_', ' ').strip()
    return name.title() if name else "Unknown App"


def build_runtime_window_app(window):
    """Build transient dock metadata for running windows without a desktop file."""
    wm_class = window.get('class', '')
    title = window.get('title', '').strip()
    return {
        'path': f"window://{window['id']}",
        'Name': prettify_wm_class(wm_class),
        'Title': title,
        'Icon': wm_class,
        'StartupWMClass': wm_class,
        'Exec': '',
        'runtime_only': True,
    }


def launch_app(app_data) -> None:
    exec_str = str(app_data.get('Exec', '') or '').strip()
    clean_exec = re.sub(r'%[fFuUdDnNvmikc]', '', exec_str).strip()
    if not clean_exec:
        return
    try:
        args = shlex.split(clean_exec)
    except Exception:
        return
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
