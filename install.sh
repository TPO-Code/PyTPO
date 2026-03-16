#!/usr/bin/env bash
#install the following dependencies
#sudo apt install pavucontrol

set -euo pipefail

PKG_NAME="pytpo"
ROOT_DIR="$(pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
DBUS_SERVICES_DIR="${HOME}/.local/share/dbus-1/services"
STATE_DIR="${HOME}/.local/state/pytpo"
TOPBAR_BIN="${HOME}/.local/bin/pytpo-topbar"

APPINDICATOR_UUID="ubuntu-appindicators@ubuntu.com"
APPINDICATOR_STATE_FILE="${STATE_DIR}/appindicator-was-enabled"

ACTION="install"
TOPBAR_MODE="ask"

# Core system dependencies for PyTPO and related desktop/dev integrations.
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
  ./install.sh [install|uninstall] [--with-topbar|--without-topbar] [--help]

Actions:
  install       Install/reinstall PyTPO and desktop integration (default)
  uninstall     Remove PyTPO, desktop integration, and topbar D-Bus overrides

Options:
  --with-topbar     Enable the PyTPO topbar notification/tray overrides
  --without-topbar  Disable/remove the PyTPO topbar notification/tray overrides
  --help            Show this help
EOF
}

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
}


have_command() {
    command -v "$1" >/dev/null 2>&1
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
    echo "==> Removing topbar D-Bus overrides if present..."
    rm -f "${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service"
    rm -f "${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service"
    rm -f "${DBUS_SERVICES_DIR}/org.freedesktop.StatusNotifierWatcher.service"
}

install_topbar_overrides() {
    echo "==> Installing user D-Bus service overrides for topbar..."
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
    if command -v pytpo-desktop-integration >/dev/null 2>&1; then
        pytpo-desktop-integration uninstall || true
    else
        echo "    No existing desktop integration command found, skipping."
    fi
}

pipx_uninstall() {
    echo "==> Removing pipx package if present..."
    if pipx list --short 2>/dev/null | grep -Fxq "$PKG_NAME"; then
        pipx uninstall "$PKG_NAME"
    else
        echo "    No existing pipx package found, skipping."
    fi
}

do_install() {
    install_apt_dependencies
    desktop_uninstall
    pipx_uninstall

    echo "==> Syncing project environment with uv..."
    uv sync

    if [[ ! -x "$PYTHON_BIN" ]]; then
        echo "Error: expected Python interpreter not found at $PYTHON_BIN"
        exit 1
    fi

    echo "==> Reinstalling app with pipx using $PYTHON_BIN ..."
    pipx install --python "$PYTHON_BIN" . --force

    echo "==> Installing desktop entries..."
    pytpo-desktop-integration install

    case "$TOPBAR_MODE" in
        with)
            install_topbar_overrides
            ;;
        without)
            remove_topbar_overrides
            restore_conflicting_extension
            echo "    Topbar overrides disabled."
            ;;
        ask)
            echo
            read -r -p "==> Enable PyTPO topbar notification/tray override? [y/N] " ENABLE_TOPBAR
            if [[ "$ENABLE_TOPBAR" =~ ^[Yy]$ ]]; then
                install_topbar_overrides
            else
                remove_topbar_overrides
                restore_conflicting_extension
                echo "    Topbar overrides disabled."
            fi
            ;;
        *)
            echo "Error: invalid topbar mode: $TOPBAR_MODE"
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
        --with-topbar)
            TOPBAR_MODE="with"
            ;;
        --without-topbar)
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
