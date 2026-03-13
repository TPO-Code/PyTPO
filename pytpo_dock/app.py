from __future__ import annotations

import sys
import os
import subprocess
import json
import re
import shlex

from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QToolButton,
                               QMenu, QFrame)
from PySide6.QtCore import (Qt, QTimer, QPropertyAnimation, QPoint, QEasingCurve,
                            Signal, QSize)
from PySide6.QtGui import QIcon, QCursor, QAction, QPainter, QColor

from .storage_paths import dock_pinned_apps_path, migrate_legacy_dock_storage


def parse_desktop_file(path):
    """Parses a Linux .desktop file and extracts App Name, Exec, Icon, and Class."""
    app_info = {'path': path}
    in_entry = False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line == '[Desktop Entry]':
                    in_entry = True
                elif line.startswith('[') and in_entry:
                    break
                elif in_entry and '=' in line:
                    key, val = line.split('=', 1)
                    if key in['Name', 'Exec', 'Icon', 'StartupWMClass', 'Type', 'NoDisplay']:
                        app_info[key] = val
    except Exception:
        pass
    return app_info

def build_app_registry():
    """Scans standard directories for .desktop files to register applications."""
    registry = {}
    dirs =['/usr/share/applications', os.path.expanduser('~/.local/share/applications')]
    
    for d in dirs:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.endswith('.desktop'):
                    path = os.path.join(d, f)
                    info = parse_desktop_file(path)
                    
                    if info.get('Type') == 'Application' and info.get('NoDisplay', 'false').lower() != 'true':
                        wm_class = info.get('StartupWMClass', '').lower()
                        if wm_class:
                            registry[wm_class] = info
                        # Fallback: use filename as class identifier
                        filename_class = f.lower().replace('.desktop', '')
                        registry[filename_class] = info
    return registry


# --- UI Elements ---

class DockItem(QToolButton):
    pin_toggled = Signal(str, bool)

    def __init__(self, app_data, is_pinned=False, is_running=False, win_id=None):
        super().__init__()
        self.app_data = app_data
        self.is_pinned = is_pinned
        self.is_running = is_running
        self.win_id = win_id
        
        self.setFixedSize(60, 60)
        
        # Load Icon
        icon_name = app_data.get('Icon', 'application-x-executable')
        icon = QIcon.fromTheme(icon_name)
        if icon.isNull():
            # Fallback if it's an absolute path
            if os.path.exists(icon_name):
                icon = QIcon(icon_name)
            else:
                icon = QIcon.fromTheme("application-default-icon")
                
        self.setIcon(icon)
        self.setIconSize(QSize(42, 42))
        self.setToolTip(app_data.get('Name', 'Unknown App'))

        # Setup standard PySide clean look
        self.setStyleSheet("""
            QToolButton {
                background: transparent;
                border-radius: 12px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 30);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 15);
            }
        """)

        self.clicked.connect(self.launch_or_focus)

    def launch_or_focus(self):
        if self.is_running and self.win_id:
            # Focus existing window
            subprocess.run(['wmctrl', '-i', '-a', self.win_id])
        else:
            # Launch new instance
            exec_str = self.app_data.get('Exec', '')
            # Clean up standard desktop file placeholders
            clean_exec = re.sub(r'%[fFuUdDnNvmikc]', '', exec_str).strip()
            if clean_exec:
                args = shlex.split(clean_exec)
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        pin_text = "Unpin from Dock" if self.is_pinned else "Pin to Dock"
        pin_action = QAction(pin_text, self)
        pin_action.triggered.connect(self.toggle_pin)
        menu.addAction(pin_action)
        
        # Basic styling for the menu to keep it clean
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(40, 40, 40, 240);
                color: white;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 40);
            }
        """)
        menu.exec(event.globalPos())

    def toggle_pin(self):
        self.pin_toggled.emit(self.app_data['path'], not self.is_pinned)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Draw running indicator (Small dot at the bottom)
        if self.is_running:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(200, 200, 200))
            painter.setPen(Qt.NoPen)
            dot_size = 4
            x = (self.width() - dot_size) // 2
            y = self.height() - dot_size - 4
            painter.drawEllipse(x, y, dot_size, dot_size)


class CustomDock(QWidget):
    def __init__(self):
        super().__init__()
        migrate_legacy_dock_storage()
        
        # Window characteristics: frameless, stay on top, transparent background
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.registry = build_app_registry()
        self.load_pinned_apps()
        
        self.is_visible = False
        self.last_dock_state =[]
        
        # UI Layout
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        
        # The stylized container that holds the dock items
        self.container = QFrame(self)
        self.container.setObjectName("DockContainer")
        self.container.setStyleSheet("""
            QFrame#DockContainer {
                background-color: rgba(30, 30, 30, 190);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 18px;
            }
        """)
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(10, 5, 10, 5)
        self.container_layout.setSpacing(5)
        
        self.main_layout.addWidget(self.container)

        # Timers
        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self.check_mouse_proximity)
        self.mouse_timer.start(100) # Check mouse every 100ms
        
        self.wm_timer = QTimer(self)
        self.wm_timer.timeout.connect(self.update_dock_items)
        self.wm_timer.start(1000) # Update running apps every second

        self.update_dock_items()
        self.recenter()

    def load_pinned_apps(self):
        self.pinned_apps =[]
        pinned_file = dock_pinned_apps_path()
        if pinned_file.is_file():
            try:
                with pinned_file.open('r', encoding='utf-8') as f:
                    self.pinned_apps = json.load(f)
            except json.JSONDecodeError:
                pass

    def save_pinned_apps(self):
        with dock_pinned_apps_path().open('w', encoding='utf-8') as f:
            json.dump(self.pinned_apps, f)

    def handle_pin_toggle(self, desktop_path, should_pin):
        if should_pin and desktop_path not in self.pinned_apps:
            self.pinned_apps.append(desktop_path)
        elif not should_pin and desktop_path in self.pinned_apps:
            self.pinned_apps.remove(desktop_path)
        self.save_pinned_apps()
        self.update_dock_items()

    def get_running_windows(self):
        try:
            output = subprocess.check_output(['wmctrl', '-l', '-x'], text=True)
            windows =[]
            for line in output.splitlines():
                parts = line.split(maxsplit=4)
                if len(parts) >= 5:
                    win_id = parts[0]
                    desktop_id = parts[1]
                    if desktop_id == '-1': 
                        continue # Skip background/hidden elements
                    wm_class = parts[2].split('.')[-1].lower() # Usually formatted as instance.class
                    windows.append({'id': win_id, 'class': wm_class})
            return windows
        except FileNotFoundError:
            print("Warning: 'wmctrl' not found. Cannot track running apps.")
            return[]
        except Exception as e:
            return[]

    def update_dock_items(self):
        running_windows = self.get_running_windows()
        
        # Build the new target state
        target_items =[]
        added_paths = set()
        
        # 1. Add Pinned Apps First
        for path in self.pinned_apps:
            # Find in registry or parse manually
            app_data = parse_desktop_file(path) if os.path.exists(path) else None
            if not app_data:
                continue
                
            wm_class = app_data.get('StartupWMClass', '').lower()
            if not wm_class:
                wm_class = os.path.basename(path).lower().replace('.desktop', '')
                
            # Check if this pinned app is running
            running_id = None
            for w in running_windows:
                if w['class'] == wm_class or wm_class in w['class']:
                    running_id = w['id']
                    break
                    
            target_items.append({
                'path': path,
                'data': app_data,
                'is_pinned': True,
                'is_running': running_id is not None,
                'win_id': running_id
            })
            added_paths.add(path)
            
        # 2. Add unpinned Running Apps
        for w in running_windows:
            wm_class = w['class']
            if wm_class in self.registry:
                app_data = self.registry[wm_class]
                path = app_data['path']
                
                if path not in added_paths:
                    target_items.append({
                        'path': path,
                        'data': app_data,
                        'is_pinned': False,
                        'is_running': True,
                        'win_id': w['id']
                    })
                    added_paths.add(path)
                    
        # Compare states to prevent UI flickering / rebuilding needlessly
        current_state_signature = [(i['path'], i['is_running'], i['win_id']) for i in target_items]
        
        if current_state_signature != self.last_dock_state:
            self.rebuild_layout(target_items)
            self.last_dock_state = current_state_signature

    def rebuild_layout(self, items):
        # Clear existing layout
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        # Repopulate
        for item in items:
            btn = DockItem(item['data'], item['is_pinned'], item['is_running'], item['win_id'])
            btn.pin_toggled.connect(self.handle_pin_toggle)
            self.container_layout.addWidget(btn)
            
        self.adjustSize()
        self.recenter()

    def check_mouse_proximity(self):
        pos = QCursor.pos()
        screen_geo = QApplication.primaryScreen().geometry()
        
        # Bottom screen edge proximity detection (bottom 5 pixels)
        trigger_area = screen_geo.height() - 5
        
        if pos.y() >= trigger_area:
            if not self.is_visible:
                self.show_dock()
        else:
            # Hide logic: Only hide if the cursor has completely left the dock area + a buffer zone
            buffer = 60
            dock_rect = self.geometry()
            buffered_rect = dock_rect.adjusted(-buffer, -buffer, buffer, buffer)
            
            if not buffered_rect.contains(pos):
                if self.is_visible:
                    self.hide_dock()

    def show_dock(self):
        if self.is_visible: return
        self.is_visible = True
        
        screen_geo = QApplication.primaryScreen().geometry()
        target_y = screen_geo.height() - self.height() - 15 # 15 pixels from bottom
        
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(250)
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(self.x(), target_y))
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()

    def hide_dock(self):
        if not self.is_visible: return
        self.is_visible = False
        
        screen_geo = QApplication.primaryScreen().geometry()
        target_y = screen_geo.height() # Push completely off-screen
        
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(300)
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(self.x(), target_y))
        self.anim.setEasingCurve(QEasingCurve.InCubic)
        self.anim.start()

    def recenter(self):
        """Ensures the dock remains horizontally centered when resized."""
        screen_geo = QApplication.primaryScreen().geometry()
        new_x = (screen_geo.width() - self.width()) // 2
        
        if self.is_visible:
            new_y = screen_geo.height() - self.height() - 15
        else:
            new_y = screen_geo.height()
            
        self.move(new_x, new_y)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(list(sys.argv if argv is None else argv))

    # Try to set a common Linux icon theme when no theme is active.
    if QIcon.themeName() == "":
        QIcon.setThemeName("Adwaita")

    dock = CustomDock()
    dock.show()

    # Start it off-screen (hidden state).
    screen_geo = QApplication.primaryScreen().geometry()
    dock.move((screen_geo.width() - dock.width()) // 2, screen_geo.height())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
