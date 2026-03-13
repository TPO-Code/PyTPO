#!/usr/bin/env bash
set -euo pipefail
INTEGRATION_DONE_MARKER="__PYTPO_INTEGRATION_DONE__:"
trap 'rc=$?; echo "${INTEGRATION_DONE_MARKER}${rc}"' EXIT

LAUNCHER_PATH="${HOME}/.local/bin/pytpo-terminal"
DESKTOP_FILE="${HOME}/.local/share/applications/pytpo-terminal.desktop"
SYSTEM_LAUNCHER_PATH="/usr/local/bin/pytpo-terminal"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-root)
            shift 2
            ;;
        --launcher-path)
            LAUNCHER_PATH="${2:-}"
            shift 2
            ;;
        --desktop-file)
            DESKTOP_FILE="${2:-}"
            shift 2
            ;;
        --system-launcher-path)
            SYSTEM_LAUNCHER_PATH="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

rm -f "${LAUNCHER_PATH}"
rm -f "${DESKTOP_FILE}"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "${DESKTOP_FILE}")" >/dev/null 2>&1 || true
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi

if command -v gsettings >/dev/null 2>&1; then
    if gsettings list-schemas 2>/dev/null | grep -qx "org.gnome.desktop.default-applications.terminal"; then
        gsettings reset org.gnome.desktop.default-applications.terminal exec >/dev/null 2>&1 || true
        gsettings reset org.gnome.desktop.default-applications.terminal exec-arg >/dev/null 2>&1 || true
    fi
fi

echo "Removed launcher: ${LAUNCHER_PATH}"
echo "Removed desktop file: ${DESKTOP_FILE}"

if command -v update-alternatives >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
        update-alternatives --remove x-terminal-emulator "${SYSTEM_LAUNCHER_PATH}" || true
        rm -f "${SYSTEM_LAUNCHER_PATH}"
        echo "Removed x-terminal-emulator alternative: ${SYSTEM_LAUNCHER_PATH}"
    else
        echo "System x-terminal-emulator alternative was not removed (requires root)."
        echo "To remove PyTPO from system alternatives, run:"
        echo "  sudo update-alternatives --remove x-terminal-emulator \"${SYSTEM_LAUNCHER_PATH}\""
        echo "  sudo rm -f \"${SYSTEM_LAUNCHER_PATH}\""
    fi
else
    echo "update-alternatives was not found; skipped system alternative removal."
fi
