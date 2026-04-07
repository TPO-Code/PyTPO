#!/usr/bin/env bash
#install the following dependencies
#sudo apt install pavucontrol

set -euo pipefail

PKG_NAME="barley_ide"
LEGACY_PKG_NAMES=("barley-ide" "pytpo")
ROOT_DIR="$(pwd)"
PIPX_PYTHON="$(uv python find --system '==3.11.*')"
DBUS_SERVICES_DIR="${HOME}/.local/share/dbus-1/services"
LOCAL_BIN_DIR="${HOME}/.local/bin"
STATE_DIR="${HOME}/.local/state/pytpo"
TOPBAR_BIN="${HOME}/.local/bin/brim"
DESKTOP_INTEGRATION_CMD="barley-ide-desktop-integration"
TEXT_EDITOR_DESKTOP_INTEGRATION_CMD="pytpo-text-editor-desktop-integration"
DOCK_DESKTOP_INTEGRATION_CMD="pytpo-dock-desktop-integration"
GRIST_DESKTOP_INTEGRATION_CMD="grist-desktop-integration"
LEGACY_APPGRID_DESKTOP_INTEGRATION_CMD="pytpo-appgrid-desktop-integration"
STOUT_DESKTOP_INTEGRATION_CMD="stout-desktop-integration"
LEGACY_DESKTOP_INTEGRATION_CMD="pytpo-desktop-integration"
LEGACY_BIN_NAMES=(
    "pytpo-appgrid"
    "pytpo-appgrid-desktop-integration"
    "pytpo-terminal"
    "pytpo-topbar"
)

APPINDICATOR_UUID="ubuntu-appindicators@ubuntu.com"
APPINDICATOR_STATE_FILE="${STATE_DIR}/appindicator-was-enabled"

ACTION="install"
TOPBAR_MODE="ask"

# Core system dependencies for Barley and related desktop/dev integrations.
# Add to this list when a fresh system install reveals a missing apt package.
APT_DEPENDENCIES=(
    playerctl
    pavucontrol
    wmctrl
    pipx
    libcairo2-dev
    python3
    cmake
    clang-format
    cargo
    gnome-shell-extension-appindicator
    lldb
)

OPTIONAL_COMMANDS=(
    uv
    ruff
    clangd
    rust-analyzer
    rustfmt
)

usage() {
    cat <<'EOF'
Usage:
  ./install.sh [install|uninstall] [--with-brim|--without-brim] [--help]

Actions:
  install       Install/reinstall Barley and desktop integration (default)
  uninstall     Remove Barley, desktop integration, and Brim D-Bus overrides

Options:
  --with-brim       Enable the Brim notification/tray overrides
  --without-brim    Disable/remove the Brim notification/tray overrides
  --with-topbar     Legacy alias for --with-brim
  --without-topbar  Legacy alias for --without-brim
  --help            Show this help
EOF
}

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
}


have_command() {
    command -v "$1" >/dev/null 2>&1
}

cleanup_legacy_bin_links() {
    echo "==> Removing stale legacy launchers from ${LOCAL_BIN_DIR} if present..."
    local found=false
    local legacy_bin
    for legacy_bin in "${LEGACY_BIN_NAMES[@]}"; do
        local legacy_path="${LOCAL_BIN_DIR}/${legacy_bin}"
        if [[ -e "$legacy_path" || -L "$legacy_path" ]]; then
            rm -f "$legacy_path"
            echo "    Removed ${legacy_path}"
            found=true
        fi
    done
    if ! $found; then
        echo "    No stale legacy launchers found."
    fi
}

install_apt_dependencies() {
    if ! have_command apt-get; then
        echo "Error: apt-get not found. This installer currently supports apt-based systems."
        exit 1
    fi

    local missing=()
    local pkg

    for pkg in "${APT_DEPENDENCIES[@]}"; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing+=("$pkg")
        fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "==> System dependencies already installed."
        return
    fi

    echo "==> Installing missing system dependencies:"
    printf '    %s\n' "${missing[@]}"
    echo "==> You may be prompted for your sudo password."

    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"

}

disable_conflicting_extension() {
    ensure_state_dir

    if ! command -v gnome-extensions >/dev/null 2>&1; then
        echo "    Warning: gnome-extensions not found, skipping extension disable."
        return
    fi

    if gnome-extensions list --enabled | grep -Fxq "$APPINDICATOR_UUID"; then
        echo "==> Disabling conflicting GNOME extension: $APPINDICATOR_UUID"
        if gnome-extensions disable "$APPINDICATOR_UUID"; then
            echo "yes" > "$APPINDICATOR_STATE_FILE"
        else
            echo "    Warning: could not disable $APPINDICATOR_UUID"
        fi
    else
        echo "==> GNOME extension already not enabled: $APPINDICATOR_UUID"
        rm -f "$APPINDICATOR_STATE_FILE"
    fi
}

restore_conflicting_extension() {
    if ! command -v gnome-extensions >/dev/null 2>&1; then
        echo "    Warning: gnome-extensions not found, skipping extension restore."
        return
    fi

    if [[ -f "$APPINDICATOR_STATE_FILE" ]]; then
        echo "==> Re-enabling GNOME extension we previously disabled: $APPINDICATOR_UUID"
        if gnome-extensions enable "$APPINDICATOR_UUID"; then
            rm -f "$APPINDICATOR_STATE_FILE"
        else
            echo "    Warning: could not re-enable $APPINDICATOR_UUID"
        fi
    else
        echo "==> Not re-enabling $APPINDICATOR_UUID because it was not disabled by this installer."
    fi
}

remove_topbar_overrides() {
    echo "==> Removing Brim D-Bus overrides if present..."
    rm -f "${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service"
    rm -f "${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service"
    rm -f "${DBUS_SERVICES_DIR}/org.freedesktop.StatusNotifierWatcher.service"
}

install_topbar_overrides() {
    echo "==> Installing user D-Bus service overrides for Brim..."
    mkdir -p "$DBUS_SERVICES_DIR"

    cat > "${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service" <<'EOF'
[D-BUS Service]
Name=org.gnome.Shell.Notifications
Exec=/bin/true
EOF

    cat > "${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service" <<EOF
[D-BUS Service]
Name=org.freedesktop.Notifications
Exec=${TOPBAR_BIN}
EOF

    cat > "${DBUS_SERVICES_DIR}/org.freedesktop.StatusNotifierWatcher.service" <<EOF
[D-BUS Service]
Name=org.freedesktop.StatusNotifierWatcher
Exec=${TOPBAR_BIN}
EOF

    echo "    Wrote:"
    echo "      ${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service"
    echo "      ${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service"
    echo "      ${DBUS_SERVICES_DIR}/org.freedesktop.StatusNotifierWatcher.service"

    disable_conflicting_extension

    echo "    Log out and back in for the D-Bus and GNOME Shell changes to take full effect."
}

desktop_uninstall() {
    echo "==> Removing desktop integration if present..."
    local found=false
    if command -v "$DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$TEXT_EDITOR_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$TEXT_EDITOR_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$DOCK_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$DOCK_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$GRIST_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$GRIST_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$LEGACY_APPGRID_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$LEGACY_APPGRID_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$STOUT_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$STOUT_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if command -v "$LEGACY_DESKTOP_INTEGRATION_CMD" >/dev/null 2>&1; then
        "$LEGACY_DESKTOP_INTEGRATION_CMD" uninstall || true
        found=true
    fi
    if ! $found; then
        echo "    No existing desktop integration command found, skipping."
    fi
}

pipx_uninstall() {
    echo "==> Removing pipx packages if present..."
    if pipx list --short 2>/dev/null | grep -Fxq "$PKG_NAME"; then
        pipx uninstall "$PKG_NAME"
    else
        echo "    No existing $PKG_NAME pipx package found, skipping."
    fi
    local legacy_pkg
    for legacy_pkg in "${LEGACY_PKG_NAMES[@]}"; do
        if pipx list --short 2>/dev/null | grep -Fxq "$legacy_pkg"; then
            pipx uninstall "$legacy_pkg"
        else
            echo "    No existing $legacy_pkg pipx package found, skipping."
        fi
    done
    cleanup_legacy_bin_links
}

do_install() {
    install_apt_dependencies
    desktop_uninstall
    pipx_uninstall

    echo "==> Syncing project environment with uv..."
    uv sync

    if [[ -z "${PIPX_PYTHON:-}" || ! -x "$PIPX_PYTHON" ]]; then
        echo "Error: could not find a usable non-venv Python 3.11 interpreter for pipx."
        echo "Try: uv python install 3.11"
        exit 1
    fi

    echo "==> Reinstalling app with pipx using $PIPX_PYTHON ..."
    pipx install --python "$PIPX_PYTHON" . --force

    echo "==> Installing desktop entries..."
    "$DESKTOP_INTEGRATION_CMD" install
    "$TEXT_EDITOR_DESKTOP_INTEGRATION_CMD" install
    "$DOCK_DESKTOP_INTEGRATION_CMD" install
    "$GRIST_DESKTOP_INTEGRATION_CMD" install
    "$STOUT_DESKTOP_INTEGRATION_CMD" install

    case "$TOPBAR_MODE" in
        with)
            install_topbar_overrides
            ;;
        without)
            remove_topbar_overrides
            restore_conflicting_extension
            echo "    Brim overrides disabled."
            ;;
        ask)
            echo
            read -r -p "==> Enable the Brim notification/tray override? [y/N] " ENABLE_TOPBAR
            if [[ "$ENABLE_TOPBAR" =~ ^[Yy]$ ]]; then
                install_topbar_overrides
            else
                remove_topbar_overrides
                restore_conflicting_extension
                echo "    Brim overrides disabled."
            fi
            ;;
        *)
            echo "Error: invalid Brim mode: $TOPBAR_MODE"
            exit 1
            ;;
    esac

    echo "==> Done. The house is standing, the lights are on, and nobody is bleeding in the hallway."
}

do_uninstall() {
    # Read state FIRST before anything touches the state dir
    local appindicator_was_enabled=false
    if [[ -f "$APPINDICATOR_STATE_FILE" ]]; then
        appindicator_was_enabled=true
    fi

    desktop_uninstall
    pipx_uninstall
    remove_topbar_overrides

   # if $appindicator_was_enabled; then
        echo "==> Re-enabling GNOME extension we previously disabled: $APPINDICATOR_UUID"
        if gnome-extensions enable "$APPINDICATOR_UUID"; then
            rm -f "$APPINDICATOR_STATE_FILE"
        else
            echo "    Warning: could not re-enable $APPINDICATOR_UUID"
        fi
    #else
        #echo "==> Not re-enabling $APPINDICATOR_UUID because it was not disabled by this installer."
    #fi

    echo "==> Uninstall complete. The house has been packed into boxes."
}

for arg in "$@"; do
    case "$arg" in
        install)
            ACTION="install"
            ;;
        uninstall)
            ACTION="uninstall"
            ;;
        --with-brim|--with-topbar)
            TOPBAR_MODE="with"
            ;;
        --without-brim|--without-topbar)
            TOPBAR_MODE="without"
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown argument: $arg"
            echo
            usage
            exit 1
            ;;
    esac
done

case "$ACTION" in
    install)
        do_install
        ;;
    uninstall)
        do_uninstall
        ;;
    *)
        echo "Error: unknown action: $ACTION"
        exit 1
        ;;
esac
