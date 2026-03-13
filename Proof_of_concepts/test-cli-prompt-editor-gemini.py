import sys
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QComboBox, QPushButton, QLabel, QTreeWidget, QTreeWidgetItem,
    QPlainTextEdit, QScrollArea, QFrame, QLineEdit, QToolBar, QMessageBox,
    QSizePolicy, QTextBrowser
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QColor, QTextCursor

# ==========================================
# 1. Models & Mappings (prompt_model.py)
# ==========================================

@dataclass
class TokenDef:
    id: str
    category: str
    name: str
    desc: str
    bash_seq: str
    zsh_seq: str
    mock_val: str
    mock_venv: str = ""
    mock_git: str = ""
    mock_fail: str = ""

# Neutral prompt elements definitions
TOKENS =[
    TokenDef('USER', 'Identity', 'Username', 'Current username', '\\u', '%n', 'user'),
    TokenDef('HOST_SHORT', 'Identity', 'Hostname', 'Short hostname', '\\h', '%m', 'host'),
    TokenDef('HOST_FULL', 'Identity', 'Full Hostname', 'Fully qualified hostname', '\\H', '%M', 'host.local'),
    TokenDef('PROMPT_CHAR', 'Identity', 'Prompt Symbol', '$ for user, # for root', '\\$', '%#', '$', mock_fail='#'),
    
    TokenDef('DIR_FULL', 'Path', 'Full Directory', 'Full path to current directory', '\\w', '%~', '~/projects/app'),
    TokenDef('DIR_SHORT', 'Path', 'Short Directory', 'Basename of current directory', '\\W', '%1~', 'app'),
    
    TokenDef('TIME', 'Time', 'Time (24h)', 'Current time in HH:MM:SS', '\\t', '%*', '14:30:00'),
    TokenDef('DATE', 'Time', 'Date', 'Current date', '\\d', '%D', 'Wed Mar 11'),
    
    TokenDef('VENV', 'Environment', 'Virtual Env', 'Python venv (mock)', '${VIRTUAL_ENV:+($(basename $VIRTUAL_ENV))}', '${VIRTUAL_ENV:+($(basename $VIRTUAL_ENV))}', '', mock_venv='(venv)'),
    TokenDef('GIT', 'VCS', 'Git Branch', 'Git branch status (mock)', '$(__git_ps1 "(%s)")', '$(git branch --show-current 2>/dev/null)', '', mock_git='(main)'),
    
    TokenDef('NEWLINE', 'Structure', 'New Line', 'Line break', '\\n', '\\n', '\n'),
]

COLORS =['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white']

@dataclass
class Segment:
    type: str  # 'literal', 'token', 'color_fg', 'color_bg', 'reset'
    value: str # e.g. text for literal, token ID for token, color name for colors
    
    def bash_render(self) -> str:
        if self.type == 'literal': return self.value
        elif self.type == 'token':
            t = next((x for x in TOKENS if x.id == self.value), None)
            return t.bash_seq if t else ''
        elif self.type == 'color_fg':
            idx = COLORS.index(self.value) if self.value in COLORS else 7
            return f"\\[\\e[3{idx}m\\]"
        elif self.type == 'reset':
            return "\\[\\e[0m\\]"
        return ""

    def zsh_render(self) -> str:
        if self.type == 'literal': return self.value
        elif self.type == 'token':
            t = next((x for x in TOKENS if x.id == self.value), None)
            return t.zsh_seq if t else ''
        elif self.type == 'color_fg':
            return f"%F{{{self.value}}}"
        elif self.type == 'reset':
            return "%f%k%b"
        return ""


# ==========================================
# 2. Parsers & Generators (prompt_parsers.py)
# ==========================================

class PromptEngine:
    @staticmethod
    def generate_raw(segments: List[Segment], shell: str) -> str:
        if shell == 'bash':
            return "".join(s.bash_render() for s in segments)
        else:
            return "".join(s.zsh_render() for s in segments)

    @staticmethod
    def parse_raw(raw: str, shell: str) -> Tuple[List[Segment], List[str]]:
        """A best-effort parser. If it fails to perfectly map tokens, falls back to literals."""
        segments =[]
        warnings =[]
        remaining = raw

        # Create mapping of sequences to segments based on shell
        seq_map = {}
        for t in TOKENS:
            seq = t.bash_seq if shell == 'bash' else t.zsh_seq
            seq_map[seq] = Segment('token', t.id)
            
        # Add basic color mapping (simplified for prototype)
        if shell == 'bash':
            seq_map['\\[\\e[0m\\]'] = Segment('reset', '')
            for i, c in enumerate(COLORS):
                seq_map[f'\\[\\e[3{i}m\\]'] = Segment('color_fg', c)
        else:
            seq_map['%f%k%b'] = Segment('reset', '')
            seq_map['%f'] = Segment('reset', '')
            for c in COLORS:
                seq_map[f'%F{{{c}}}'] = Segment('color_fg', c)

        # Sort sequences by length descending to match longest first
        sorted_seqs = sorted(seq_map.keys(), key=len, reverse=True)

        while remaining:
            matched = False
            for seq in sorted_seqs:
                if remaining.startswith(seq):
                    segments.append(seq_map[seq])
                    remaining = remaining[len(seq):]
                    matched = True
                    break
            
            if not matched:
                # Accumulate literal character
                if segments and segments[-1].type == 'literal':
                    segments[-1].value += remaining[0]
                else:
                    segments.append(Segment('literal', remaining[0]))
                remaining = remaining[1:]

        # Simplistic validation: checks for unparsed shell escapes
        if shell == 'bash' and '\\' in "".join(s.value for s in segments if s.type == 'literal'):
            warnings.append("Raw markup contains unrecognized Bash escape sequences.")
        if shell == 'zsh' and '%' in "".join(s.value for s in segments if s.type == 'literal'):
            warnings.append("Raw markup contains unrecognized Zsh escape sequences.")

        return segments, warnings


# ==========================================
# 3. Presets & System Config (prompt_presets.py)
# ==========================================

PRESETS = {
    "Minimal":[
        Segment('token', 'DIR_SHORT'),
        Segment('literal', ' '),
        Segment('token', 'PROMPT_CHAR'),
        Segment('literal', ' ')
    ],
    "Classic user@host:path$":[
        Segment('token', 'USER'),
        Segment('literal', '@'),
        Segment('token', 'HOST_SHORT'),
        Segment('literal', ':'),
        Segment('token', 'DIR_FULL'),
        Segment('token', 'PROMPT_CHAR'),
        Segment('literal', ' ')
    ],
    "Colorful":[
        Segment('color_fg', 'green'), Segment('token', 'USER'),
        Segment('literal', '@'), Segment('token', 'HOST_SHORT'),
        Segment('reset', ''), Segment('literal', ':'),
        Segment('color_fg', 'blue'), Segment('token', 'DIR_FULL'),
        Segment('reset', ''), Segment('token', 'PROMPT_CHAR'),
        Segment('literal', ' ')
    ]
}

def detect_system_prompt(shell: str) -> str:
    """Best-effort system prompt detection"""
    try:
        cfg_path = Path.home() / ('.bashrc' if shell == 'bash' else '.zshrc')
        var_name = 'PS1=' if shell == 'bash' else 'PROMPT='
        if cfg_path.exists():
            with open(cfg_path, 'r', errors='ignore') as f:
                for line in reversed(f.readlines()):
                    line = line.strip()
                    if line.startswith(var_name):
                        val = line[len(var_name):].strip(' "\'')
                        return val
    except Exception:
        pass
    
    # Fallbacks
    return r'\u@\h:\w\$ ' if shell == 'bash' else r'%n@%m:%~%# '


# ==========================================
# 4. Apply / File Writing (prompt_apply.py)
# ==========================================

def apply_to_system(shell: str, raw_markup: str) -> bool:
    try:
        cfg_path = Path.home() / ('.bashrc' if shell == 'bash' else '.zshrc')
        var_name = 'PS1' if shell == 'bash' else 'PROMPT'
        
        block_start = f"# BEGIN MANAGED PROMPT EDITOR ({shell})"
        block_end = f"# END MANAGED PROMPT EDITOR ({shell})"
        assignment = f"{var_name}='{raw_markup}'"

        new_lines =[]
        in_block = False
        replaced = False

        if cfg_path.exists():
            with open(cfg_path, 'r') as f:
                lines = f.readlines()
            
            for line in lines:
                if line.strip() == block_start:
                    in_block = True
                    new_lines.append(block_start + "\n")
                    new_lines.append(assignment + "\n")
                    new_lines.append(block_end + "\n")
                    replaced = True
                elif line.strip() == block_end:
                    in_block = False
                elif not in_block:
                    new_lines.append(line)
        else:
            new_lines = []

        if not replaced:
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines.append('\n')
            new_lines.extend([f"\n{block_start}\n", f"{assignment}\n", f"{block_end}\n"])

        with open(cfg_path, 'w') as f:
            f.writelines(new_lines)
        return True
    except Exception as e:
        print(f"Failed to apply: {e}")
        return False


# ==========================================
# 5. UI Widgets (prompt_palette, prompt_preview, etc)
# ==========================================

class SegmentRowWidget(QWidget):
    """Represents a single segment in the structural editor."""
    removed = Signal(int)
    moved_up = Signal(int)
    moved_down = Signal(int)
    changed = Signal()

    def __init__(self, segment: Segment, index: int):
        super().__init__()
        self.segment = segment
        self.index = index
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.lbl_type = QLabel(f"[{self.segment.type.upper()}]")
        self.lbl_type.setFixedWidth(80)
        self.lbl_type.setStyleSheet("font-weight: bold; color: gray;")
        layout.addWidget(self.lbl_type)

        if self.segment.type == 'literal':
            self.editor = QLineEdit(self.segment.value)
            self.editor.textChanged.connect(self._on_val_changed)
            layout.addWidget(self.editor)
        elif self.segment.type == 'color_fg':
            self.cb = QComboBox()
            self.cb.addItems(COLORS)
            if self.segment.value in COLORS:
                self.cb.setCurrentText(self.segment.value)
            self.cb.currentTextChanged.connect(self._on_val_changed)
            layout.addWidget(self.cb)
        else:
            val_lbl = QLabel(self.segment.value)
            layout.addWidget(val_lbl)

        layout.addStretch()

        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(30)
        btn_up.clicked.connect(lambda: self.moved_up.emit(self.index))
        layout.addWidget(btn_up)

        btn_dn = QPushButton("▼")
        btn_dn.setFixedWidth(30)
        btn_dn.clicked.connect(lambda: self.moved_down.emit(self.index))
        layout.addWidget(btn_dn)

        btn_del = QPushButton("✕")
        btn_del.setFixedWidth(30)
        btn_del.clicked.connect(lambda: self.removed.emit(self.index))
        layout.addWidget(btn_del)

    def _on_val_changed(self, text):
        self.segment.value = text
        self.changed.emit()


class PreviewWidget(QWidget):
    """Renders the mock terminal preview."""
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Controls
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("Preview State:"))
        self.cb_state = QComboBox()
        self.cb_state.addItems(["Normal", "Inside Virtual Env", "Inside Git Repo", "Last Command Failed"])
        self.cb_state.currentTextChanged.connect(self.update_preview)
        ctrl_layout.addWidget(self.cb_state)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

        # Terminal Box
        self.term_view = QTextBrowser()
        self.term_view.setStyleSheet("background-color: #1E1E1E; color: #CCCCCC; font-family: monospace; font-size: 14px; padding: 10px;")
        layout.addWidget(self.term_view)
        
        # Explanations Box
        self.exp_view = QTextBrowser()
        self.exp_view.setMaximumHeight(80)
        self.exp_view.setStyleSheet("background-color: #f0f0f0; color: #333; font-size: 12px;")
        layout.addWidget(self.exp_view)
        
        self.current_segments =[]

    def set_segments(self, segments: List[Segment]):
        self.current_segments = segments
        self.update_preview()

    def update_preview(self, *_):
        state = self.cb_state.currentText()
        html = ""
        current_color = ""
        explanations =[]

        for s in self.current_segments:
            if s.type == 'literal':
                text = s.value.replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
                html += f"<span>{text}</span>"
            elif s.type == 'token':
                t = next((x for x in TOKENS if x.id == s.value), None)
                if t:
                    explanations.append(f"<b>{t.name}</b>: {t.desc}")
                    val = t.mock_val
                    if state == "Inside Virtual Env" and t.mock_venv: val = t.mock_venv + " "
                    if state == "Inside Git Repo" and t.mock_git: val = " " + t.mock_git
                    if state == "Last Command Failed" and t.mock_fail: val = t.mock_fail
                    html += f"<span>{val}</span>"
            elif s.type == 'color_fg':
                color = s.value if s.value in COLORS else 'white'
                html += f"</span><span style='color: {color};'>"
                explanations.append(f"<b>Style</b>: Set foreground to {color}")
            elif s.type == 'reset':
                html += "</span><span>"
                explanations.append(f"<b>Style</b>: Reset attributes")

        self.term_view.setHtml(f"<div>{html}</div>")
        self.exp_view.setHtml(" | ".join(explanations) if explanations else "<i>No elements to explain.</i>")


class PromptEditorWindow(QMainWindow):
    """Main Application Window"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shell Prompt Editor (Bash / Zsh)")
        self.resize(1100, 700)
        
        self.segments: List[Segment] =[]
        self._updating = False

        self._init_ui()
        self._load_system_prompt('bash')

    def _init_ui(self):
        # Toolbar
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)

        self.cb_shell = QComboBox()
        self.cb_shell.addItems(["bash", "zsh"])
        self.cb_shell.currentTextChanged.connect(self._on_shell_changed)
        toolbar.addWidget(QLabel(" Shell: "))
        toolbar.addWidget(self.cb_shell)

        self.cb_preset = QComboBox()
        self.cb_preset.addItems(["-- Select Preset --"] + list(PRESETS.keys()))
        self.cb_preset.currentTextChanged.connect(self._on_preset_changed)
        toolbar.addWidget(QLabel("  Preset: "))
        toolbar.addWidget(self.cb_preset)
        
        toolbar.addSeparator()

        btn_reload = QPushButton("Reload System Prompt")
        btn_reload.clicked.connect(lambda: self._load_system_prompt(self.cb_shell.currentText()))
        toolbar.addWidget(btn_reload)
        
        # Central Splitters
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        top_splitter = QSplitter(Qt.Horizontal)
        
        # LEFT: Palette
        palette_widget = QWidget()
        pal_layout = QVBoxLayout(palette_widget)
        pal_layout.setContentsMargins(0, 0, 0, 0)
        pal_layout.addWidget(QLabel("<b>Palette</b> (Double-click to insert)"))
        
        self.tree_palette = QTreeWidget()
        self.tree_palette.setHeaderHidden(True)
        self._build_palette()
        self.tree_palette.itemDoubleClicked.connect(self._on_palette_insert)
        pal_layout.addWidget(self.tree_palette)
        top_splitter.addWidget(palette_widget)

        # CENTER: Structured Editor
        struct_widget = QWidget()
        struct_layout = QVBoxLayout(struct_widget)
        struct_layout.setContentsMargins(0, 0, 0, 0)
        struct_layout.addWidget(QLabel("<b>Structured Builder</b>"))

        self.scroll_struct = QScrollArea()
        self.scroll_struct.setWidgetResizable(True)
        self.struct_container = QWidget()
        self.struct_vbox = QVBoxLayout(self.struct_container)
        self.struct_vbox.setAlignment(Qt.AlignTop)
        self.scroll_struct.setWidget(self.struct_container)
        struct_layout.addWidget(self.scroll_struct)
        top_splitter.addWidget(struct_widget)

        # RIGHT: Raw Markup
        raw_widget = QWidget()
        raw_layout = QVBoxLayout(raw_widget)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        raw_layout.addWidget(QLabel("<b>Raw Markup</b>"))
        
        self.txt_raw = QPlainTextEdit()
        self.txt_raw.setFont(QFont("Monospace", 11))
        self.txt_raw.textChanged.connect(self._on_raw_changed)
        raw_layout.addWidget(self.txt_raw)
        
        self.lbl_warnings = QLabel("")
        self.lbl_warnings.setStyleSheet("color: red; font-weight: bold;")
        self.lbl_warnings.hide()
        raw_layout.addWidget(self.lbl_warnings)
        
        top_splitter.addWidget(raw_widget)

        # Ensure reasonable widths
        top_splitter.setSizes([250, 450, 400])

        # BOTTOM: Preview
        bottom_widget = QWidget()
        bot_layout = QVBoxLayout(bottom_widget)
        bot_layout.setContentsMargins(0, 10, 0, 0)
        bot_layout.addWidget(QLabel("<b>Live Preview & Validation</b>"))
        self.preview_widget = PreviewWidget()
        bot_layout.addWidget(self.preview_widget)
        
        # Apply button
        btn_apply = QPushButton("Apply to System Configuration")
        btn_apply.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 5px;")
        btn_apply.clicked.connect(self._on_apply)
        bot_layout.addWidget(btn_apply)

        # Main Vertical Splitter
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.addWidget(top_splitter)
        v_splitter.addWidget(bottom_widget)
        v_splitter.setSizes([450, 200])

        main_layout.addWidget(v_splitter)

    def _build_palette(self):
        cats = {}
        for t in TOKENS:
            if t.category not in cats: cats[t.category] = []
            cats[t.category].append(t)
            
        for cat, items in cats.items():
            cat_item = QTreeWidgetItem([cat])
            for t in items:
                child = QTreeWidgetItem([t.name])
                child.setToolTip(0, t.desc)
                child.setData(0, Qt.UserRole, ('token', t.id))
                cat_item.addChild(child)
            self.tree_palette.addTopLevelItem(cat_item)
            cat_item.setExpanded(True)

        # Style Category
        style_cat = QTreeWidgetItem(["Style"])
        
        fg_item = QTreeWidgetItem(["Foreground Color"])
        fg_item.setData(0, Qt.UserRole, ('color_fg', 'green'))
        style_cat.addChild(fg_item)
        
        reset_item = QTreeWidgetItem(["Reset Style"])
        reset_item.setData(0, Qt.UserRole, ('reset', ''))
        style_cat.addChild(reset_item)
        
        self.tree_palette.addTopLevelItem(style_cat)
        style_cat.setExpanded(True)

        # Custom Category
        cust_cat = QTreeWidgetItem(["Custom"])
        lit_item = QTreeWidgetItem(["Literal Space"])
        lit_item.setData(0, Qt.UserRole, ('literal', ' '))
        cust_cat.addChild(lit_item)
        self.tree_palette.addTopLevelItem(cust_cat)
        cust_cat.setExpanded(True)

    def _load_system_prompt(self, shell: str):
        prompt_str = detect_system_prompt(shell)
        self.txt_raw.setPlainText(prompt_str)

    def _on_shell_changed(self, shell: str):
        if self._updating: return
        self._updating = True
        
        # Re-generate raw markup for the new shell using existing segments
        new_raw = PromptEngine.generate_raw(self.segments, shell)
        self.txt_raw.setPlainText(new_raw)
        
        self._updating = False
        self._sync_all()

    def _on_preset_changed(self, preset_name: str):
        if preset_name in PRESETS:
            # Deep copy to avoid modifying preset definition
            import copy
            self.segments = copy.deepcopy(PRESETS[preset_name])
            self._sync_all()

    def _on_palette_insert(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole)
        if data:
            self.segments.append(Segment(type=data[0], value=data[1]))
            self._sync_all()

    def _on_raw_changed(self):
        if self._updating: return
        shell = self.cb_shell.currentText()
        raw_text = self.txt_raw.toPlainText()
        
        self.segments, warnings = PromptEngine.parse_raw(raw_text, shell)
        
        if warnings:
            self.lbl_warnings.setText("\n".join(warnings))
            self.lbl_warnings.show()
        else:
            self.lbl_warnings.hide()

        self._updating = True
        self._rebuild_structured_ui()
        self.preview_widget.set_segments(self.segments)
        self._updating = False

    def _sync_all(self):
        """Called when structural model changes to update raw and preview."""
        if self._updating: return
        self._updating = True
        
        shell = self.cb_shell.currentText()
        new_raw = PromptEngine.generate_raw(self.segments, shell)
        
        # Update raw text without triggering parsing
        self.txt_raw.setPlainText(new_raw)
        
        self._rebuild_structured_ui()
        self.preview_widget.set_segments(self.segments)
        
        self._updating = False

    def _rebuild_structured_ui(self):
        # Clear existing
        while self.struct_vbox.count():
            item = self.struct_vbox.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()

        for i, seg in enumerate(self.segments):
            row = SegmentRowWidget(seg, i)
            row.removed.connect(self._remove_segment)
            row.moved_up.connect(self._move_segment_up)
            row.moved_down.connect(self._move_segment_down)
            row.changed.connect(self._sync_all)
            self.struct_vbox.addWidget(row)

    def _remove_segment(self, index: int):
        if 0 <= index < len(self.segments):
            self.segments.pop(index)
            self._sync_all()

    def _move_segment_up(self, index: int):
        if index > 0:
            self.segments[index-1], self.segments[index] = self.segments[index], self.segments[index-1]
            self._sync_all()

    def _move_segment_down(self, index: int):
        if index < len(self.segments) - 1:
            self.segments[index], self.segments[index+1] = self.segments[index+1], self.segments[index]
            self._sync_all()

    def _on_apply(self):
        shell = self.cb_shell.currentText()
        raw = self.txt_raw.toPlainText()
        success = apply_to_system(shell, raw)
        
        if success:
            QMessageBox.information(self, "Success", f"Prompt successfully applied to ~/.{shell}rc\n\nRestart your terminal or run 'source ~/.{shell}rc' to see changes.")
        else:
            QMessageBox.warning(self, "Error", "Failed to apply prompt to configuration file.")

# ==========================================
# 6. Main execution
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Optional styling for a cleaner modern look
    app.setStyle("Fusion")
    
    window = PromptEditorWindow()
    window.show()
    sys.exit(app.exec())