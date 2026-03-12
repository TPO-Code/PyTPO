#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=""
LAUNCHER_PATH="${HOME}/.local/bin/pytpo-terminal"
DESKTOP_FILE="${HOME}/.local/share/applications/pytpo-terminal.desktop"

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

CMD=(uv run python "\${REPO_ROOT}/terminal_app_main.py")
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
echo "Default terminal integration applied where supported."
