import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QDockWidget, QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt

class CustomTitleBar(QWidget):
    """A custom title bar with a title and a close button."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 5, 10, 5)
        self.layout.setSpacing(5)

        # Title Label
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: white; font-weight: bold;")
        self.layout.addWidget(self.title_label)
        self.layout.addStretch()

        # Custom Close Button
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(20, 20)
        self.btn_close.setStyleSheet("""
            QPushButton { border: none; color: #aaa; font-weight: bold; }
            QPushButton:hover { color: #fff; }
        """)
        self.btn_close.clicked.connect(self.close_dock)
        self.layout.addWidget(self.btn_close)

        # Style the bar background
        self.setStyleSheet("background-color: #333; border-bottom: 1px solid #555;")

    def close_dock(self):
        # Find the parent QDockWidget and close it
        parent = self.parent()
        while parent and not isinstance(parent, QDockWidget):
            parent = parent.parent()
        if parent:
            parent.close()

class ModernDockWidget(QDockWidget):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        
        # Create our custom title bar widget
        self.custom_title_bar = CustomTitleBar(title, self)
        self.setTitleBarWidget(self.custom_title_bar)
        
        # Connect signal to handle floating state
        self.topLevelChanged.connect(self.on_floating_changed)

    def on_floating_changed(self, floating):
        """
        Workaround: When floating, Qt removes native decorations if a custom 
        title bar is set. We remove the custom title bar when floating to 
        get the native window frame back, and restore it when docked.
        """
        if floating:
            self.setTitleBarWidget(None)
        else:
            self.setTitleBarWidget(self.custom_title_bar)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Custom Dock Widget Example")
        self.resize(800, 600)

        # Central widget
        central = QLabel("Main Window Area")
        central.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(central)

        # Add our custom dock widget
        dock = ModernDockWidget("My Custom Dock", self)
        dock.setWidget(QLabel("This is a modern-looking dock content"))
        
        # Style the dock content area
        dock.setStyleSheet("QDockWidget { border: 1px solid red; }")
        
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion") # Fusion style usually plays nicer with custom colors
    w = MainWindow()
    w.show()
    app.exec()