from __future__ import annotations

from typing import Any


def ensure_xlib_available() -> None:
    _load_xlib()


def list_windows_via_xlib() -> list[dict[str, Any]]:
    X, display = _load_xlib()
    try:
        x_display = display.Display()
    except Exception as exc:
        raise RuntimeError("python-xlib could not open the X11 display for dock window tracking.") from exc

    try:
        root = x_display.screen().root
        client_list_atom = x_display.intern_atom("_NET_CLIENT_LIST")
        desktop_atom = x_display.intern_atom("_NET_WM_DESKTOP")
        net_wm_name_atom = x_display.intern_atom("_NET_WM_NAME")
        net_wm_pid_atom = x_display.intern_atom("_NET_WM_PID")
        net_wm_state_atom = x_display.intern_atom("_NET_WM_STATE")
        skip_taskbar_atom = x_display.intern_atom("_NET_WM_STATE_SKIP_TASKBAR")
        skip_pager_atom = x_display.intern_atom("_NET_WM_STATE_SKIP_PAGER")
        window_type_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE")
        window_type_dock_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE_DOCK")
        window_type_desktop_atom = x_display.intern_atom("_NET_WM_WINDOW_TYPE_DESKTOP")
        utf8_atom = x_display.intern_atom("UTF8_STRING")

        client_list = root.get_full_property(client_list_atom, X.AnyPropertyType)
        if client_list is None:
            raise RuntimeError("_NET_CLIENT_LIST is not available on the X11 root window.")

        windows: list[dict[str, Any]] = []
        for window_id in client_list.value:
            window = x_display.create_resource_object("window", int(window_id))
            if _window_should_be_skipped(
                window,
                X=X,
                net_wm_state_atom=net_wm_state_atom,
                skip_taskbar_atom=skip_taskbar_atom,
                skip_pager_atom=skip_pager_atom,
                window_type_atom=window_type_atom,
                window_type_dock_atom=window_type_dock_atom,
                window_type_desktop_atom=window_type_desktop_atom,
            ):
                continue

            desktop = _window_desktop(window, X=X, desktop_atom=desktop_atom)
            title = _window_title(window, X=X, utf8_atom=utf8_atom, net_wm_name_atom=net_wm_name_atom)
            pid = _window_pid(window, X=X, net_wm_pid_atom=net_wm_pid_atom)
            instance_name, class_name = _window_class(window)
            wm_class = ".".join(part for part in (instance_name, class_name) if part)

            windows.append(
                {
                    "id": f"0x{int(window_id):x}",
                    "desktop": desktop,
                    "pid": pid,
                    "host": "",
                    "title": title,
                    "wm_class": wm_class,
                    "instance": instance_name,
                    "class": class_name,
                }
            )
        return windows
    finally:
        try:
            x_display.close()
        except Exception:
            pass


def _load_xlib():
    try:
        from Xlib import X, display
    except Exception as exc:
        raise RuntimeError(
            "python-xlib is required for dock window tracking. Install the Python 'python-xlib' package."
        ) from exc
    return X, display


def _window_should_be_skipped(
    window: Any,
    *,
    X: Any,
    net_wm_state_atom: Any,
    skip_taskbar_atom: Any,
    skip_pager_atom: Any,
    window_type_atom: Any,
    window_type_dock_atom: Any,
    window_type_desktop_atom: Any,
) -> bool:
    state_prop = _get_property(window, atom=net_wm_state_atom, prop_type=X.AnyPropertyType)
    state_values = set(int(value) for value in getattr(state_prop, "value", []) or [])
    if int(skip_taskbar_atom) in state_values or int(skip_pager_atom) in state_values:
        return True

    type_prop = _get_property(window, atom=window_type_atom, prop_type=X.AnyPropertyType)
    type_values = set(int(value) for value in getattr(type_prop, "value", []) or [])
    if int(window_type_dock_atom) in type_values or int(window_type_desktop_atom) in type_values:
        return True
    return False


def _window_desktop(window: Any, *, X: Any, desktop_atom: Any) -> str:
    prop = _get_property(window, atom=desktop_atom, prop_type=X.AnyPropertyType)
    if prop is None or not getattr(prop, "value", None):
        return "0"
    desktop = int(prop.value[0])
    return "-1" if desktop == 0xFFFFFFFF else str(desktop)


def _window_title(window: Any, *, X: Any, utf8_atom: Any, net_wm_name_atom: Any) -> str:
    prop = _get_property(window, atom=net_wm_name_atom, prop_type=utf8_atom)
    text = _decode_property_text(prop)
    if text:
        return text
    try:
        wm_name = window.get_wm_name()
    except Exception:
        return ""
    return str(wm_name or "").strip()


def _window_pid(window: Any, *, X: Any, net_wm_pid_atom: Any) -> int:
    prop = _get_property(window, atom=net_wm_pid_atom, prop_type=X.AnyPropertyType)
    if prop is None or not getattr(prop, "value", None):
        return 0
    try:
        return int(prop.value[0])
    except Exception:
        return 0


def _window_class(window: Any) -> tuple[str, str]:
    try:
        wm_class = window.get_wm_class()
    except Exception:
        return "", ""
    if not wm_class:
        return "", ""
    if len(wm_class) == 1:
        value = str(wm_class[0] or "").strip().lower()
        return value, value
    instance_name = str(wm_class[0] or "").strip().lower()
    class_name = str(wm_class[-1] or "").strip().lower()
    return instance_name, class_name


def _get_property(window: Any, *, atom: Any, prop_type: Any):
    try:
        return window.get_full_property(atom, prop_type)
    except Exception:
        return None


def _decode_property_text(prop: Any) -> str:
    if prop is None:
        return ""
    value = getattr(prop, "value", None)
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip()
    if isinstance(value, str):
        return value.strip()
    try:
        return bytes(value).decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""
