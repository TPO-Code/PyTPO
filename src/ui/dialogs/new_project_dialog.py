from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.settings_manager import SettingsManager
from src.ui.custom_dialog import DialogWindow
from src.ui.dialogs.file_dialog_bridge import get_existing_directory


_INVALID_FOLDER_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_TEMPLATE_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("python-app", "Python Application", "src/main.py + pyproject.toml + Python .gitignore"),
    ("python-package", "Python Package", "src/<package>/ + tests/ + build-ready pyproject.toml"),
    ("rust-bin", "Rust Binary", "Cargo.toml + src/main.rs + Rust .gitignore"),
    ("cpp-cmake", "C++ (CMake)", "CMakeLists.txt + src/main.cpp + C/C++ .gitignore"),
    ("empty", "Empty Project", "README.md only"),
)
_TEMPLATE_IDS = {item[0] for item in _TEMPLATE_CHOICES}


class NewProjectDialog(DialogWindow):
    def __init__(
        self,
        *,
        manager: SettingsManager,
        default_create_in: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=False, parent=parent)
        self.setWindowTitle("New Project")
        self.resize(620, 420)

        self._manager = manager
        self._default_create_in = str(default_create_in or "").strip() or str(Path.home())
        self._folder_name_touched = False
        self.created_project_path: str | None = None
        self.created_project_name: str | None = None
        self.created_project_post_create_note: str | None = None

        self._build_ui()
        self._load_initial_values()
        self._refresh_create_enabled()

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.project_name_edit = QLineEdit()
        self.project_name_edit.setPlaceholderText("My Project")

        create_in_row = QHBoxLayout()
        self.create_in_edit = QLineEdit()
        self.create_in_edit.setPlaceholderText("Directory")
        self.browse_btn = QPushButton("Browse")
        create_in_row.addWidget(self.create_in_edit, 1)
        create_in_row.addWidget(self.browse_btn)

        self.folder_name_edit = QLineEdit()
        self.folder_name_edit.setPlaceholderText("my-project")

        self.template_combo = QComboBox()
        for template_id, label, _description in _TEMPLATE_CHOICES:
            self.template_combo.addItem(label, template_id)

        self.template_description_label = QLabel("")
        self.template_description_label.setWordWrap(True)

        root.addWidget(QLabel("Project Name"))
        root.addWidget(self.project_name_edit)
        root.addWidget(QLabel("Create In"))
        root.addLayout(create_in_row)
        root.addWidget(QLabel("Folder Name"))
        root.addWidget(self.folder_name_edit)
        root.addWidget(QLabel("Template"))
        root.addWidget(self.template_combo)
        root.addWidget(self.template_description_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.create_btn = QPushButton("Create Project")
        self.create_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.create_btn)
        root.addLayout(actions)

        self.project_name_edit.textChanged.connect(self._on_project_name_changed)
        self.create_in_edit.textChanged.connect(self._refresh_create_enabled)
        self.folder_name_edit.textEdited.connect(self._on_folder_name_edited)
        self.folder_name_edit.textChanged.connect(self._refresh_create_enabled)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        self.browse_btn.clicked.connect(self._browse_create_in)
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn.clicked.connect(self._create_clicked)

    def _load_initial_values(self) -> None:
        create_in = str(
            self._manager.get("projects.last_create_in", scope_preference="ide", default=self._default_create_in) or ""
        ).strip()
        if not create_in:
            create_in = self._default_create_in
        if not os.path.isdir(create_in):
            create_in = self._default_create_in
        self.create_in_edit.setText(create_in)

        default_name = str(self._manager.get("defaults.name", scope_preference="ide", default="My Python Project") or "")
        project_name = default_name.strip() or "My Python Project"
        self.project_name_edit.setText(project_name)
        self.folder_name_edit.setText(self._derive_folder_name(project_name))
        self._folder_name_touched = False
        preferred = str(
            self._manager.get("projects.last_new_project_template", scope_preference="ide", default="") or ""
        ).strip()
        if preferred not in _TEMPLATE_IDS:
            pref_interpreter = str(self._manager.get("defaults.interpreter", scope_preference="ide", default="python") or "")
            preferred = self._default_template_for_interpreter(pref_interpreter)
        self._set_template(preferred)
        self._on_template_changed()

    def _on_project_name_changed(self, text: str) -> None:
        if not self._folder_name_touched:
            self.folder_name_edit.setText(self._derive_folder_name(text))
        self._refresh_create_enabled()

    def _on_folder_name_edited(self, _text: str) -> None:
        self._folder_name_touched = True

    def _browse_create_in(self) -> None:
        start = str(self.create_in_edit.text() or "").strip() or self._default_create_in
        selected = get_existing_directory(
            parent=self,
            manager=self._manager,
            caption="Select Directory",
            directory=start,
        )
        if selected:
            self.create_in_edit.setText(selected)

    def _create_clicked(self) -> None:
        project_name = str(self.project_name_edit.text() or "").strip()
        create_in = str(self.create_in_edit.text() or "").strip()
        folder_name = str(self.folder_name_edit.text() or "").strip()
        template_id = self._current_template_id()

        if not project_name:
            self._set_status("Project name is required.", error=True)
            return
        if not create_in:
            self._set_status("Create in path is required.", error=True)
            return
        if not self._is_valid_folder_name(folder_name):
            self._set_status("Folder name is invalid.", error=True)
            return

        parent_dir = Path(create_in).expanduser()
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._set_status("Could not create selected parent directory.", error=True)
            return

        target = (parent_dir / folder_name).resolve()
        if target.exists():
            self._set_status(f"Destination already exists: {target}", error=True)
            return

        try:
            target.mkdir(parents=True, exist_ok=False)
        except Exception as exc:
            self._set_status(f"Could not create project folder: {exc}", error=True)
            return

        try:
            self._apply_template(target, project_name=project_name, folder_name=folder_name, template_id=template_id)
        except Exception as exc:
            self._remove_tree_best_effort(target)
            self._set_status(f"Could not create template files: {exc}", error=True)
            return
        self.created_project_post_create_note = self._post_create_template_setup(
            target=target,
            template_id=template_id,
        )

        try:
            self._manager.set("projects.last_create_in", str(parent_dir), "ide")
            self._manager.set("projects.last_new_project_template", template_id, "ide")
            self._manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

        self.created_project_path = str(target)
        self.created_project_name = project_name
        self.accept()

    def reject(self) -> None:
        if self.created_project_path:
            super().reject()
            return
        super().reject()

    def _refresh_create_enabled(self) -> None:
        project_name = str(self.project_name_edit.text() or "").strip()
        create_in = str(self.create_in_edit.text() or "").strip()
        folder_name = str(self.folder_name_edit.text() or "").strip()
        enabled = bool(project_name and create_in and self._is_valid_folder_name(folder_name))
        self.create_btn.setEnabled(enabled)

    def _is_valid_folder_name(self, name: str) -> bool:
        text = str(name or "").strip()
        if not text:
            return False
        if text in {".", ".."}:
            return False
        if "/" in text or "\\" in text:
            return False
        if _INVALID_FOLDER_CHARS_RE.search(text):
            return False
        return True

    def _on_template_changed(self, _index: int = -1) -> None:
        template_id = self._current_template_id()
        self.template_description_label.setText(self._template_description(template_id))
        self._refresh_create_enabled()

    def _set_template(self, template_id: str) -> None:
        target = str(template_id or "").strip()
        if target not in _TEMPLATE_IDS:
            target = "python-app"
        for idx in range(self.template_combo.count()):
            candidate = str(self.template_combo.itemData(idx) or "").strip()
            if candidate == target:
                self.template_combo.setCurrentIndex(idx)
                return
        self.template_combo.setCurrentIndex(0)

    def _current_template_id(self) -> str:
        selected = str(self.template_combo.currentData() or "").strip()
        if selected in _TEMPLATE_IDS:
            return selected
        return "python-app"

    @staticmethod
    def _template_description(template_id: str) -> str:
        target = str(template_id or "").strip()
        for candidate_id, _label, description in _TEMPLATE_CHOICES:
            if candidate_id == target:
                return description
        return "Project template"

    @staticmethod
    def _default_template_for_interpreter(interpreter: str) -> str:
        text = str(interpreter or "").strip().lower()
        if "rust" in text:
            return "rust-bin"
        if "c++" in text or "cpp" in text or "clang" in text:
            return "cpp-cmake"
        return "python-app"

    def _apply_template(self, target: Path, *, project_name: str, folder_name: str, template_id: str) -> None:
        files = self._template_files(template_id, project_name=project_name, folder_name=folder_name)
        root = str(target.resolve())
        for rel_path, content in files.items():
            rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
            if not rel:
                continue
            if rel in {".", ".."} or rel.startswith("../"):
                continue
            fpath = (target / rel).resolve()
            try:
                if os.path.commonpath([root, str(fpath)]) != root:
                    continue
            except Exception:
                continue
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(str(content), encoding="utf-8")

    def _template_files(self, template_id: str, *, project_name: str, folder_name: str) -> dict[str, str]:
        project_title = str(project_name or "").strip() or "New Project"
        folder_slug = self._derive_folder_name(folder_name) or "new-project"
        module_name = self._derive_python_module_name(folder_slug)
        cargo_name = self._derive_cargo_name(folder_slug)
        cmake_name = self._derive_cmake_identifier(project_title)

        if template_id == "empty":
            return {
                "README.md": f"# {project_title}\n\nProject created with PyTPO.\n",
            }

        if template_id == "python-package":
            return {
                ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\n.pytest_cache/\n.tide/\n.pytpo/\n",
                "README.md": f"# {project_title}\n\nPython package template generated by PyTPO.\n",
                "pyproject.toml": (
                    "[build-system]\n"
                    'requires = ["hatchling"]\n'
                    'build-backend = "hatchling.build"\n\n'
                    "[project]\n"
                    f'name = "{folder_slug}"\n'
                    'version = "0.1.0"\n'
                    f'description = "{project_title}"\n'
                    'readme = "README.md"\n'
                    'requires-python = ">=3.10"\n'
                    "dependencies = []\n\n"
                    "[tool.hatch.build.targets.wheel]\n"
                    f'packages = ["src/{module_name}"]\n'
                ),
                f"src/{module_name}/__init__.py": '__all__ = ["main"]\n__version__ = "0.1.0"\n',
                f"src/{module_name}/main.py": (
                    "def main() -> None:\n"
                    f'    print("Hello from {module_name}")\n\n'
                    'if __name__ == "__main__":\n'
                    "    main()\n"
                ),
                "tests/test_smoke.py": (
                    f"from {module_name}.main import main\n\n\n"
                    "def test_smoke() -> None:\n"
                    "    assert callable(main)\n"
                ),
            }

        if template_id == "rust-bin":
            return {
                ".gitignore": "/target/\n.tide/\n.pytpo/\n",
                "README.md": f"# {project_title}\n\nRust binary template generated by PyTPO.\n",
                "Cargo.toml": (
                    "[package]\n"
                    f'name = "{cargo_name}"\n'
                    'version = "0.1.0"\n'
                    'edition = "2021"\n\n'
                    "[dependencies]\n"
                ),
                "src/main.rs": (
                    "fn main() {\n"
                    f'    println!("Hello from {cargo_name}!");\n'
                    "}\n"
                ),
            }

        if template_id == "cpp-cmake":
            return {
                ".gitignore": "/build/\n.tide/\n.pytpo/\n",
                "README.md": f"# {project_title}\n\nC++ CMake template generated by PyTPO.\n",
                "CMakeLists.txt": (
                    "cmake_minimum_required(VERSION 3.16)\n"
                    f"project({cmake_name} LANGUAGES CXX)\n\n"
                    "set(CMAKE_CXX_STANDARD 17)\n"
                    "set(CMAKE_CXX_STANDARD_REQUIRED ON)\n"
                    "set(CMAKE_EXPORT_COMPILE_COMMANDS ON)\n\n"
                    f"add_executable({cmake_name} src/main.cpp)\n"
                ),
                "src/main.cpp": (
                    "#include <iostream>\n\n"
                    "int main() {\n"
                    '    std::cout << "Hello from CMake template!" << std::endl;\n'
                    "    return 0;\n"
                    "}\n"
                ),
            }

        return {
            ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\n.tide/\n.pytpo/\n",
            "README.md": f"# {project_title}\n\nPython application template generated by PyTPO.\n",
            "pyproject.toml": (
                "[project]\n"
                f'name = "{folder_slug}"\n'
                'version = "0.1.0"\n'
                f'description = "{project_title}"\n'
                'readme = "README.md"\n'
                'requires-python = ">=3.10"\n'
                "dependencies = []\n"
            ),
            "src/main.py": (
                "def main() -> None:\n"
                f'    print("Hello from {folder_slug}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n"
            ),
        }

    def _post_create_template_setup(self, *, target: Path, template_id: str) -> str | None:
        if str(template_id or "").strip() != "cpp-cmake":
            return None
        if not (target / "CMakeLists.txt").is_file():
            return None
        if shutil.which("cmake") is None:
            return "CMake was not found in PATH. Configure once to generate compile_commands.json."
        try:
            completed = subprocess.run(
                ["cmake", "-S", ".", "-B", "build"],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except Exception as exc:
            return f"Initial CMake configure failed: {exc}"
        if int(completed.returncode) == 0:
            return None
        detail = str(completed.stderr or "").strip() or str(completed.stdout or "").strip()
        if detail:
            first_line = detail.splitlines()[0].strip()
            if first_line:
                return f"Initial CMake configure failed: {first_line}"
        return "Initial CMake configure failed. Run `cmake -S . -B build` from the project root."

    @staticmethod
    def _derive_python_module_name(folder_name: str) -> str:
        text = str(folder_name or "").strip().lower().replace("-", "_")
        text = re.sub(r"[^a-z0-9_]", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return "app"
        if text[0].isdigit():
            text = f"pkg_{text}"
        return text

    @staticmethod
    def _derive_cargo_name(folder_name: str) -> str:
        text = str(folder_name or "").strip().lower()
        text = re.sub(r"[^a-z0-9_-]", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        if not text:
            return "app"
        if text[0].isdigit():
            text = f"app-{text}"
        return text

    @staticmethod
    def _derive_cmake_identifier(project_name: str) -> str:
        text = str(project_name or "").strip()
        text = re.sub(r"[^A-Za-z0-9_]", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return "App"
        if text[0].isdigit():
            text = f"App_{text}"
        return text

    @staticmethod
    def _remove_tree_best_effort(path: Path) -> None:
        target = Path(path)
        if not target.exists():
            return
        try:
            for base, dirs, files in os.walk(target, topdown=False):
                for name in files:
                    try:
                        os.unlink(os.path.join(base, name))
                    except Exception:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(base, name))
                    except Exception:
                        pass
            os.rmdir(target)
        except Exception:
            pass

    @staticmethod
    def _derive_folder_name(project_name: str) -> str:
        text = str(project_name or "").strip().lower()
        if not text:
            return "new-project"
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^a-z0-9._-]", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-.")
        return text or "new-project"

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")
