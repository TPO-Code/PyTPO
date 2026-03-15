#!/usr/bin/env bash
set -euo pipefail

PKG_NAME="pytpo"
ROOT_DIR="$(pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
DBUS_SERVICES_DIR="${HOME}/.local/share/dbus-1/services"
TOPBAR_BIN="${HOME}/.local/bin/pytpo-topbar"

ACTION="install"
TOPBAR_MODE="ask"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh [install|uninstall] [--with-topbar|--without-topbar] [--help]

Actions:
  install       Install/reinstall PyTPO and desktop integration (default)
  uninstall     Remove PyTPO, desktop integration, and topbar D-Bus overrides

Options:
  --with-topbar     Enable the PyTPO topbar notification override
  --without-topbar  Disable/remove the PyTPO topbar notification override
  --help            Show this help
EOF
}

remove_topbar_overrides() {
    echo "==> Removing topbar D-Bus overrides if present..."
    rm -f "${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service"
    rm -f "${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service"
}

install_topbar_overrides() {
    echo "==> Installing user D-Bus service overrides for topbar notifications..."
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

    echo "    Wrote:"
    echo "      ${DBUS_SERVICES_DIR}/org.gnome.Shell.Notifications.service"
    echo "      ${DBUS_SERVICES_DIR}/org.freedesktop.Notifications.service"
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
            echo "    Log out and back in for the D-Bus notification override to take full effect."
            ;;
        without)
            remove_topbar_overrides
            echo "    Topbar notification override disabled."
            ;;
        ask)
            echo
            read -r -p "==> Enable PyTPO topbar notification override? [y/N] " ENABLE_TOPBAR
            if [[ "$ENABLE_TOPBAR" =~ ^[Yy]$ ]]; then
                install_topbar_overrides
                echo "    Log out and back in for the D-Bus notification override to take full effect."
            else
                remove_topbar_overrides
                echo "    Topbar notification override disabled."
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
    desktop_uninstall
    pipx_uninstall
    remove_topbar_overrides
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