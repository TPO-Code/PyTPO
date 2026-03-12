#!/usr/bin/env bash
set -euo pipefail
INTEGRATION_DONE_MARKER="__PYTPO_INTEGRATION_DONE__:"
trap 'rc=$?; echo "${INTEGRATION_DONE_MARKER}${rc}"' EXIT

REPO_ROOT=""
LAUNCHER_PATH="${HOME}/.local/bin/pytpo-terminal"
DESKTOP_FILE="${HOME}/.local/share/applications/pytpo-terminal.desktop"
SYSTEM_LAUNCHER_PATH="/usr/local/bin/pytpo-terminal"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-root)
            REPO_ROOT="${2:-}"
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

if [[ -z "${REPO_ROOT}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
fi
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"

mkdir -p "$(dirname "${LAUNCHER_PATH}")"
cat > "${LAUNCHER_PATH}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT}"
CWD=""

if [[ "\${1-}" == "--cwd" || "\${1-}" == "--working-directory" ]]; then
    CWD="\${2-}"
    if [[ \$# -ge 2 ]]; then
        shift 2
    else
        shift 1
    fi
elif [[ "\${1-}" == --cwd=* ]]; then
    CWD="\${1#*=}"
    shift 1
elif [[ "\${1-}" == --working-directory=* ]]; then
    CWD="\${1#*=}"
    shift 1
elif [[ -n "\${1-}" ]]; then
    CANDIDATE="\$1"
    if [[ -d "\${CANDIDATE}" ]]; then
        CWD="\${CANDIDATE}"
        shift 1
    elif [[ -f "\${CANDIDATE}" ]]; then
        CWD="\$(dirname "\${CANDIDATE}")"
        shift 1
    fi
fi

if [[ -z "\${CWD}" && -n "\${PWD-}" && -d "\${PWD}" ]]; then
    CWD="\${PWD}"
fi

CMD=(uv run --project "\${REPO_ROOT}" python "\${REPO_ROOT}/terminal_app_main.py")
if [[ -n "\${CWD}" ]]; then
    CMD+=(--cwd "\${CWD}")
fi

exec "\${CMD[@]}" "\$@"
EOF
chmod +x "${LAUNCHER_PATH}"

mkdir -p "$(dirname "${DESKTOP_FILE}")"
DESKTOP_ID="$(basename "${DESKTOP_FILE}")"
ESCAPED_LAUNCHER="${LAUNCHER_PATH// /\\ }"
cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=PyTPO Terminal
Comment=PyTPO multi-tab terminal
Exec=${ESCAPED_LAUNCHER} --cwd %f
Terminal=false
Categories=System;TerminalEmulator;
Keywords=terminal;shell;
MimeType=x-scheme-handler/terminal;
StartupNotify=true
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "${DESKTOP_FILE}")" >/dev/null 2>&1 || true
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi
if command -v xdg-mime >/dev/null 2>&1; then
    xdg-mime default "${DESKTOP_ID}" x-scheme-handler/terminal >/dev/null 2>&1 || true
fi

if command -v gsettings >/dev/null 2>&1; then
    if gsettings list-schemas 2>/dev/null | grep -qx "org.gnome.desktop.default-applications.terminal"; then
        gsettings set org.gnome.desktop.default-applications.terminal exec "${LAUNCHER_PATH}" >/dev/null 2>&1 || true
        gsettings set org.gnome.desktop.default-applications.terminal exec-arg "--cwd" >/dev/null 2>&1 || true
    fi
fi

echo "Installed launcher: ${LAUNCHER_PATH}"
echo "Installed desktop file: ${DESKTOP_FILE}"
echo "Bound PyTPO repo root: ${REPO_ROOT}"

if command -v update-alternatives >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
        install -m 755 "${LAUNCHER_PATH}" "${SYSTEM_LAUNCHER_PATH}"
        update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator "${SYSTEM_LAUNCHER_PATH}" 60 || true
        update-alternatives --set x-terminal-emulator "${SYSTEM_LAUNCHER_PATH}" || true
        echo "Registered x-terminal-emulator alternative: ${SYSTEM_LAUNCHER_PATH}"
        echo "System default terminal updated through update-alternatives."
    else
        echo "System default terminal was not changed (requires root)."
        echo "To set PyTPO as default terminal on Pop!/Ubuntu, run:"
        echo "  sudo install -m 755 \"${LAUNCHER_PATH}\" \"${SYSTEM_LAUNCHER_PATH}\""
        echo "  sudo update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator \"${SYSTEM_LAUNCHER_PATH}\" 60"
        echo "  sudo update-alternatives --set x-terminal-emulator \"${SYSTEM_LAUNCHER_PATH}\""
    fi
else
    echo "update-alternatives was not found; skipped system default terminal registration."
fi
