import sys
import os
import json
import time
import uuid
import requests
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLineEdit, QPushButton, 
                               QProgressBar, QLabel, QScrollArea, QMessageBox, QFrame)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject, QMutex, QMutexLocker

from TPOPyside.dialogs.reusable_file_dialog import FileDialog

# File to store download state
STATE_FILE = "downloads.json"

class DownloadSignals(QObject):
    """Signals for the worker thread."""
    progress = Signal(str, int, int, float)  # id, downloaded, total, speed
    status = Signal(str, str)                # id, status_text
    finished = Signal(str)                   # id
    error = Signal(str, str)                 # id, error_message

class DownloadWorker(QThread):
    """
    Worker thread that handles the actual downloading.
    It supports resuming (Range headers) and infinite retries.
    """
    def __init__(self, download_data):
        super().__init__()
        self.data = download_data
        self.id = download_data['id']
        self.signals = DownloadSignals()
        self.is_paused = download_data.get('status') == 'paused'
        self.is_cancelled = False
        self._mutex = QMutex()

    def pause(self):
        with QMutexLocker(self._mutex):
            self.is_paused = True
            self.signals.status.emit(self.id, "Paused")

    def resume(self):
        with QMutexLocker(self._mutex):
            self.is_paused = False
            self.signals.status.emit(self.id, "Resuming...")
            self.start() # Ensure thread is running

    def cancel(self):
        with QMutexLocker(self._mutex):
            self.is_cancelled = True
        self.quit()
        self.wait()

    def run(self):
        """
        The core logic:
        1. Infinite loop to allow infinite retries.
        2. Check for Pause/Cancel.
        3. Determine file size for Resume (Range header).
        4. Stream download.
        """
        url = self.data['url']
        filepath = self.data['filepath']
        
        # Infinite retry loop
        while not self.is_cancelled:
            
            # 1. Handle Pause State
            if self.is_paused:
                self.signals.status.emit(self.id, "Paused")
                time.sleep(1) # Sleep to reduce CPU usage while paused
                continue

            # 2. Check finished state
            if self.data['status'] == 'completed':
                self.signals.finished.emit(self.id)
                return

            try:
                # 3. Prepare for Resume
                downloaded_bytes = 0
                mode = 'wb'
                headers = {}
                
                if os.path.exists(filepath):
                    downloaded_bytes = os.path.getsize(filepath)
                    # If we have bytes, try to resume
                    if downloaded_bytes > 0:
                        headers = {'Range': f'bytes={downloaded_bytes}-'}
                        mode = 'ab' # Append mode

                self.signals.status.emit(self.id, "Connecting...")
                
                # 4. Request
                # stream=True is critical for large files and progress tracking
                with requests.get(url, stream=True, headers=headers, timeout=10) as r:
                    
                    # Handle Range Not Satisfiable (416) - Likely finished or file changed
                    if r.status_code == 416:
                        # Assuming finished if file exists. verify size in real app.
                        self.data['status'] = 'completed'
                        self.signals.finished.emit(self.id)
                        return

                    # Handle server not supporting ranges (200 OK means it sent the whole file again)
                    if r.status_code == 200 and downloaded_bytes > 0:
                        # Server ignored range, must overwrite
                        downloaded_bytes = 0
                        mode = 'wb'
                    
                    r.raise_for_status() # Raise error for 404, 500, etc.

                    total_length = int(r.headers.get('content-length', 0)) + downloaded_bytes
                    
                    # Store logic for speed calculation
                    start_time = time.time()
                    bytes_in_session = 0
                    
                    with open(filepath, mode) as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            # Check control flags inside the stream loop
                            if self.is_cancelled:
                                return
                            if self.is_paused:
                                break # Break inner loop, outer loop will catch pause state

                            if chunk:
                                f.write(chunk)
                                len_chunk = len(chunk)
                                downloaded_bytes += len_chunk
                                bytes_in_session += len_chunk
                                
                                # Calculate speed
                                elapsed = time.time() - start_time
                                speed = 0
                                if elapsed > 0:
                                    speed = bytes_in_session / elapsed
                                
                                self.signals.progress.emit(self.id, downloaded_bytes, total_length, speed)

                # Check if we finished normally or paused
                if not self.is_paused and not self.is_cancelled:
                    self.data['status'] = 'completed'
                    self.signals.finished.emit(self.id)
                    return 

            except requests.exceptions.RequestException as e:
                # Network Error: Do not stop. Wait and Retry.
                if not self.is_cancelled:
                    self.signals.status.emit(self.id, f"Network Error. Retrying in 5s...")
                    self.signals.error.emit(self.id, str(e))
                    time.sleep(5) # Wait before retry
            except Exception as e:
                # File/OS Error
                if not self.is_cancelled:
                    self.signals.status.emit(self.id, f"Error: {str(e)}")
                    time.sleep(5)

class DownloadItemWidget(QFrame):
    """GUI Widget representing a single download row."""
    def __init__(self, download_data, parent=None):
        super().__init__(parent)
        self.data = download_data
        self.setFrameShape(QFrame.StyledPanel)
        
        layout = QVBoxLayout()
        
        # Top Row: Name and Status
        top_layout = QHBoxLayout()
        self.lbl_name = QLabel(f"<b>{os.path.basename(download_data['filepath'])}</b>")
        self.lbl_status = QLabel(download_data['status'])
        top_layout.addWidget(self.lbl_name)
        top_layout.addStretch()
        top_layout.addWidget(self.lbl_status)
        layout.addLayout(top_layout)

        # Middle Row: Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        if download_data['total_bytes'] > 0:
            pct = int((download_data['downloaded_bytes'] / download_data['total_bytes']) * 100)
            self.progress_bar.setValue(pct)
        layout.addWidget(self.progress_bar)

        # Bottom Row: Speed and Buttons
        btn_layout = QHBoxLayout()
        self.lbl_speed = QLabel("0 KB/s")
        btn_layout.addWidget(self.lbl_speed)
        btn_layout.addStretch()
        
        self.btn_toggle = QPushButton("Pause" if download_data['status'] == 'active' else "Resume")
        self.btn_cancel = QPushButton("Remove")
        
        btn_layout.addWidget(self.btn_toggle)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def update_progress(self, current, total, speed):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{self.format_bytes(current)} / {self.format_bytes(total)} ({pct}%)")
        else:
            self.progress_bar.setRange(0, 0) # Indeterminate
            self.progress_bar.setFormat(f"{self.format_bytes(current)}")
        
        self.lbl_speed.setText(f"{self.format_bytes(speed)}/s")

    def format_bytes(self, size):
        power = 2**10
        n = 0
        power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
        while size > power:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels.get(n, '')}B"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Persistent Resumable Downloader")
        self.resize(600, 500)
        
        self.workers = {} # Map id -> DownloadWorker
        self.widgets = {} # Map id -> DownloadItemWidget
        self.download_list_data = [] # List of dicts

        self.init_ui()
        self.load_state()

    def init_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        # --- Input Area ---
        input_group = QFrame()
        input_group.setFrameShape(QFrame.StyledPanel)
        input_layout = QVBoxLayout()

        # URL
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/largefile.zip")
        url_layout.addWidget(self.url_input)
        input_layout.addLayout(url_layout)

        # Location
        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel("Save to:"))
        self.loc_input = QLineEdit(os.path.join(os.getcwd(), "Downloads"))
        self.btn_browse = QPushButton("...")
        self.btn_browse.clicked.connect(self.browse_folder)
        loc_layout.addWidget(self.loc_input)
        loc_layout.addWidget(self.btn_browse)
        input_layout.addLayout(loc_layout)

        # Filename (Optional)
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Rename (Optional):"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Leave empty to use URL name")
        name_layout.addWidget(self.name_input)
        input_layout.addLayout(name_layout)

        # Add Button
        self.btn_add = QPushButton("Add Download")
        self.btn_add.clicked.connect(self.add_new_download)
        input_layout.addWidget(self.btn_add)
        
        input_group.setLayout(input_layout)
        main_layout.addWidget(input_group)

        # --- Download List Area ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        
        main_layout.addWidget(self.scroll_area)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def browse_folder(self):
        folder, _starred = FileDialog.getExistingDirectory(self, "Select Download Folder")
        if folder:
            self.loc_input.setText(folder)

    def add_new_download(self):
        url = self.url_input.text().strip()
        target_dir = self.loc_input.text().strip()
        optional_name = self.name_input.text().strip()

        if not url or not target_dir:
            return

        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir)
            except OSError:
                QMessageBox.critical(self, "Error", "Cannot create target directory.")
                return

        # Determine filename
        if optional_name:
            filename = optional_name
        else:
            filename = url.split('/')[-1].split('?')[0] # Basic parsing
            if not filename: filename = "downloaded_file"

        filepath = os.path.join(target_dir, filename)

        # Create Data Object
        new_data = {
            "id": str(uuid.uuid4()),
            "url": url,
            "filepath": filepath,
            "status": "active",
            "downloaded_bytes": 0,
            "total_bytes": 0
        }

        self.download_list_data.append(new_data)
        self.create_download_row(new_data)
        self.save_state()
        
        # Clear inputs
        self.url_input.clear()
        self.name_input.clear()

    def create_download_row(self, data):
        """Creates the widget and starts the worker."""
        # 1. Create Widget
        item_widget = DownloadItemWidget(data)
        self.scroll_layout.addWidget(item_widget)
        self.widgets[data['id']] = item_widget

        # 2. Setup Worker
        worker = DownloadWorker(data)
        self.workers[data['id']] = worker

        # 3. Connect UI Buttons
        item_widget.btn_toggle.clicked.connect(lambda: self.toggle_download(data['id']))
        item_widget.btn_cancel.clicked.connect(lambda: self.remove_download(data['id']))

        # 4. Connect Worker Signals
        worker.signals.progress.connect(self.on_progress)
        worker.signals.status.connect(self.on_status)
        worker.signals.finished.connect(self.on_finished)
        
        # 5. Start if active
        if data['status'] == 'active':
            worker.start()
        else:
            item_widget.lbl_status.setText("Paused")

    def toggle_download(self, uid):
        worker = self.workers.get(uid)
        widget = self.widgets.get(uid)
        
        if worker.is_paused:
            worker.resume()
            widget.btn_toggle.setText("Pause")
            # Update data model
            for d in self.download_list_data:
                if d['id'] == uid: d['status'] = 'active'
        else:
            worker.pause()
            widget.btn_toggle.setText("Resume")
            for d in self.download_list_data:
                if d['id'] == uid: d['status'] = 'paused'
        
        self.save_state()

    def remove_download(self, uid):
        # Stop worker
        if uid in self.workers:
            self.workers[uid].cancel()
            del self.workers[uid]

        # Remove widget
        if uid in self.widgets:
            self.widgets[uid].deleteLater()
            del self.widgets[uid]

        # Remove from data
        self.download_list_data = [d for d in self.download_list_data if d['id'] != uid]
        self.save_state()

    # --- Signal Slots ---

    @Slot(str, int, int, float)
    def on_progress(self, uid, current, total, speed):
        if uid in self.widgets:
            self.widgets[uid].update_progress(current, total, speed)
            # Update data model for persistence
            for d in self.download_list_data:
                if d['id'] == uid:
                    d['downloaded_bytes'] = current
                    d['total_bytes'] = total
            
            # Save occasionally could be added here, but causes I/O overhead.
            # We rely on save at stop/start/close for now.

    @Slot(str, str)
    def on_status(self, uid, status_text):
        if uid in self.widgets:
            self.widgets[uid].lbl_status.setText(status_text)

    @Slot(str)
    def on_finished(self, uid):
        if uid in self.widgets:
            self.widgets[uid].lbl_status.setText("Completed")
            self.widgets[uid].btn_toggle.setEnabled(False)
            self.widgets[uid].progress_bar.setValue(100)
            
            for d in self.download_list_data:
                if d['id'] == uid: d['status'] = 'completed'
            self.save_state()

    # --- Persistence ---

    def save_state(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.download_list_data, f, indent=4)
        except Exception as e:
            print(f"Failed to save state: {e}")

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return

        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                self.download_list_data = data
                for d in self.download_list_data:
                    # Reset completed ones to just display
                    # Resume active ones
                    self.create_download_row(d)
        except Exception as e:
            print(f"Failed to load state: {e}")

    def closeEvent(self, event):
        """Handle app closure."""
        # Save state one last time
        self.save_state()
        
        # Stop all threads cleanly
        for uid, worker in self.workers.items():
            worker.cancel()
        
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
