import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from pytpo.file_dialog_settings import configure_shared_file_dialog_defaults
from pytpo.services.theme_compiler import compile_qsst_file
from ui.main_window import MainWindow


def _apply_default_theme(app: QApplication) -> None:
    theme_path = Path(__file__).resolve().parents[1] / "themes" / "Default.qsst"
    if not theme_path.is_file():
        return
    try:
        app.setStyleSheet(compile_qsst_file(theme_path))
    except Exception:
        try:
            app.setStyleSheet(theme_path.read_text(encoding="utf-8"))
        except Exception:
            return


def main(argv):
    app = QApplication(argv)
    app.setApplicationName("PyTPO Session Workbench")
    app.setApplicationDisplayName("PyTPO Session Workbench")
    configure_shared_file_dialog_defaults()

    icon_path = Path(__file__).resolve().parents[1] / "pytpo" / "icon.png"
    if icon_path.is_file():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)

    _apply_default_theme(app)
    win = MainWindow()
    win.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main(sys.argv))
