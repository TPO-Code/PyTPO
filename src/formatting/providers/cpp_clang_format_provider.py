"""C/C++ code formatting provider backed by clang-format."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from src.formatting.code_formatting import FormatRequest, FormatResult
from src.ui.custom_dialog import DialogWindow


CPP_FORMAT_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".hxx"}
CPP_FORMAT_LANGUAGE_IDS = {"c", "cpp"}
CLANG_STYLE_PRESETS = ("LLVM", "Google", "Chromium", "Mozilla", "WebKit")


class _ClangFormatBootstrapDialog(DialogWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            use_native_chrome=bool(getattr(parent, "use_native_chrome", True)),
            resizable=False,
            parent=parent,
        )
        self.setWindowTitle("No .clang-format found")
        self.resize(480, 220)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        text = QLabel(
            "This project has no .clang-format. Choose a preset to create one in the project root, then format."
        )
        text.setWordWrap(True)
        root.addWidget(text)

        self.preset = QComboBox(host)
        for item in CLANG_STYLE_PRESETS:
            self.preset.addItem(item)
        root.addWidget(self.preset)

        self.minimal = QRadioButton("Minimal config", host)
        self.full = QRadioButton("Full config", host)
        self.minimal.setChecked(True)
        root.addWidget(self.minimal)
        root.addWidget(self.full)

        buttons = QDialogButtonBox(host)
        self.btn_create = buttons.addButton("Create & Format", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        self.btn_create.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_preset(self) -> str:
        return str(self.preset.currentText() or "LLVM").strip() or "LLVM"

    def wants_full(self) -> bool:
        return bool(self.full.isChecked())


class CppClangFormatProvider:
    def __init__(
        self,
        *,
        canonicalize,
        path_has_prefix,
        clang_format_path: str = "clang-format",
    ) -> None:
        self._canonicalize = canonicalize
        self._path_has_prefix = path_has_prefix
        self._clang_format_path = str(clang_format_path or "clang-format").strip() or "clang-format"

    def can_format(self, language_id: str, *, file_path: str = "") -> bool:
        lang = str(language_id or "").strip().lower()
        if lang in CPP_FORMAT_LANGUAGE_IDS:
            return True
        ext = str(Path(str(file_path or "")).suffix or "").strip().lower()
        return ext in CPP_FORMAT_EXTENSIONS

    def format_document(self, request: FormatRequest) -> FormatResult:
        return self._format(request, line_range=None)

    def format_selection(
        self,
        request: FormatRequest,
        *,
        start_line: int,
        end_line: int,
    ) -> FormatResult:
        start = max(1, int(start_line))
        end = max(start, int(end_line))
        return self._format(request, line_range=(start, end))

    def _format(self, request: FormatRequest, *, line_range: tuple[int, int] | None) -> FormatResult:
        file_path = self._canonicalize(str(request.file_path or ""))
        if not file_path:
            return FormatResult(status="error", message="Save the file before formatting.")

        clang_bin = self._resolve_clang_format_path()
        if not clang_bin:
            self._show_missing_tool_dialog(request.parent_widget)
            return FormatResult(status="error", message="clang-format not found.")

        source_text = str(request.source_text or "")
        if not source_text:
            return FormatResult(status="ok", formatted_text=source_text, message="Nothing to format.")

        debug_lines = [f"[Format] clang-format: {clang_bin}"]
        style_dir, setup = self._ensure_style_available(
            file_path=file_path,
            project_root=str(request.project_root or ""),
            clang_bin=clang_bin,
            parent=request.parent_widget,
        )
        if setup.status == "canceled":
            return setup
        if setup.status == "error" or not style_dir:
            out = FormatResult(
                status="error",
                message=setup.message or "Could not prepare .clang-format.",
                created_config_path=setup.created_config_path,
            )
            out.debug_lines = debug_lines + list(setup.debug_lines or [])
            return out
        if setup.created_config_path:
            debug_lines.append(f"[Format] Created config: {setup.created_config_path}")

        run_result = self._run_clang_format(
            clang_bin=clang_bin,
            file_path=file_path,
            source_text=source_text,
            style_dir=style_dir,
            line_range=line_range,
        )
        debug_lines.extend(run_result.debug_lines)

        if run_result.ok:
            run_result.debug_lines = debug_lines
            if setup.created_config_path:
                run_result.created_config_path = setup.created_config_path
            return run_result

        # Invalid/unreadable style config should be treated as missing and recreated.
        if self._looks_like_style_file_error(run_result.stderr):
            style_dir_retry, retry_setup = self._ensure_style_available(
                file_path=file_path,
                project_root=str(request.project_root or ""),
                clang_bin=clang_bin,
                parent=request.parent_widget,
                force_prompt=True,
            )
            debug_lines.extend(retry_setup.debug_lines)
            if retry_setup.status == "canceled":
                canceled = FormatResult(status="canceled", message="Formatting canceled.")
                canceled.debug_lines = debug_lines
                return canceled
            if retry_setup.status == "error" or not style_dir_retry:
                err = FormatResult(
                    status="error",
                    message=retry_setup.message or run_result.message,
                    stderr=run_result.stderr,
                    created_config_path=retry_setup.created_config_path,
                )
                err.debug_lines = debug_lines
                return err
            rerun_result = self._run_clang_format(
                clang_bin=clang_bin,
                file_path=file_path,
                source_text=source_text,
                style_dir=style_dir_retry,
                line_range=line_range,
            )
            debug_lines.extend(rerun_result.debug_lines)
            rerun_result.debug_lines = debug_lines
            if retry_setup.created_config_path:
                rerun_result.created_config_path = retry_setup.created_config_path
            return rerun_result

        run_result.debug_lines = debug_lines
        return run_result

    def _ensure_style_available(
        self,
        *,
        file_path: str,
        project_root: str,
        clang_bin: str,
        parent: object | None,
        force_prompt: bool = False,
    ) -> tuple[str, FormatResult]:
        discovered_dir, state, _cfg_path = self._discover_style_dir(file_path=file_path, project_root=project_root)
        if discovered_dir and state == "found" and not force_prompt:
            return discovered_dir, FormatResult(status="ok")

        parent_widget = parent if isinstance(parent, QWidget) else None
        dialog = _ClangFormatBootstrapDialog(parent=parent_widget)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return "", FormatResult(status="canceled", message="Formatting canceled.")

        preset = dialog.selected_preset()
        wants_full = dialog.wants_full()
        target_dir, write_result = self._create_style_file(
            file_path=file_path,
            project_root=project_root,
            clang_bin=clang_bin,
            preset=preset,
            full_config=wants_full,
        )
        if write_result.status == "error":
            if parent_widget is not None:
                QMessageBox.warning(parent_widget, "clang-format", write_result.message or "Failed to create .clang-format.")
            return "", write_result
        return target_dir, write_result

    def _discover_style_dir(self, *, file_path: str, project_root: str) -> tuple[str, str, str]:
        cfile = self._canonicalize(file_path)
        if not cfile:
            return "", "missing", ""

        start_dir = Path(cfile).parent
        stop_dir: Path | None = None
        croot = self._canonicalize(str(project_root or ""))
        if croot and self._path_has_prefix(cfile, croot):
            stop_dir = Path(croot)

        cur = start_dir
        while True:
            cfg = cur / ".clang-format"
            if cfg.exists():
                if not cfg.is_file():
                    return "", "unreadable", str(cfg)
                try:
                    with cfg.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.read(1)
                    return str(cur), "found", str(cfg)
                except Exception:
                    return "", "unreadable", str(cfg)

            if stop_dir is not None and cur == stop_dir:
                break
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        return "", "missing", ""

    def _create_style_file(
        self,
        *,
        file_path: str,
        project_root: str,
        clang_bin: str,
        preset: str,
        full_config: bool,
    ) -> tuple[str, FormatResult]:
        cfile = self._canonicalize(file_path)
        file_dir = str(Path(cfile).parent)
        croot = self._canonicalize(str(project_root or ""))
        use_root = bool(croot and self._path_has_prefix(cfile, croot))
        preferred_dir = croot if use_root else file_dir
        fallback_dir = file_dir

        attempted: list[str] = []
        for target_dir in (preferred_dir, fallback_dir):
            target_dir = self._canonicalize(target_dir)
            if not target_dir or target_dir in attempted:
                continue
            attempted.append(target_dir)
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception:
                continue

            cfg_path = os.path.join(target_dir, ".clang-format")
            try:
                content = self._build_style_content(
                    clang_bin=clang_bin,
                    target_dir=target_dir,
                    preset=preset,
                    full_config=full_config,
                )
                if not content.strip():
                    raise RuntimeError("clang-format returned empty config output.")
                with open(cfg_path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                result = FormatResult(
                    status="ok",
                    message=f"Created .clang-format in {target_dir}",
                    created_config_path=cfg_path,
                )
                result.debug_lines = [f"[Format] Wrote {cfg_path}"]
                return target_dir, result
            except Exception as exc:
                msg = f"Could not create .clang-format in {target_dir}: {exc}"
                result = FormatResult(status="error", message=msg)
                result.debug_lines = [f"[Format] {msg}"]
                continue

        return "", FormatResult(status="error", message="Could not create .clang-format in project or file directory.")

    def _build_style_content(
        self,
        *,
        clang_bin: str,
        target_dir: str,
        preset: str,
        full_config: bool,
    ) -> str:
        chosen = str(preset or "LLVM").strip() or "LLVM"
        if not full_config:
            return (
                f"BasedOnStyle: {chosen}\n"
                "IndentWidth: 4\n"
                "UseTab: Never\n"
                "ColumnLimit: 100\n"
            )

        proc = subprocess.run(
            [clang_bin, f"-style={chosen}", "-dump-config"],
            text=True,
            capture_output=True,
            cwd=target_dir,
            check=False,
        )
        if proc.returncode != 0:
            err = str(proc.stderr or "").strip() or f"exit {proc.returncode}"
            raise RuntimeError(f"clang-format -dump-config failed: {err}")
        return str(proc.stdout or "")

    def _run_clang_format(
        self,
        *,
        clang_bin: str,
        file_path: str,
        source_text: str,
        style_dir: str,
        line_range: tuple[int, int] | None,
    ) -> FormatResult:
        args = [clang_bin, "-style=file", f"-assume-filename={file_path}"]
        if line_range is not None:
            start, end = line_range
            args.append(f"-lines={max(1, int(start))}:{max(1, int(end))}")

        proc = subprocess.run(
            args,
            input=source_text,
            text=True,
            capture_output=True,
            cwd=style_dir,
            check=False,
        )
        stderr = str(proc.stderr or "").strip()
        debug = [f"[Format] cmd: {' '.join(args)}"]
        if stderr:
            debug.append(f"[Format][stderr] {stderr}")
        if proc.returncode != 0:
            message = stderr or f"clang-format failed (exit {proc.returncode})."
            return FormatResult(status="error", message=message, stderr=stderr, debug_lines=debug)
        return FormatResult(status="ok", formatted_text=str(proc.stdout or ""), debug_lines=debug)

    def _resolve_clang_format_path(self) -> str:
        raw = str(self._clang_format_path or "").strip() or "clang-format"
        if os.path.isabs(raw):
            return raw if os.path.isfile(raw) else ""
        return str(shutil.which(raw) or "")

    def _show_missing_tool_dialog(self, parent: object | None) -> None:
        widget = parent if isinstance(parent, QWidget) else None
        QMessageBox.warning(
            widget,
            "clang-format not found",
            "clang-format executable was not found.\nInstall clang-format and ensure it is available on PATH.",
        )

    @staticmethod
    def _looks_like_style_file_error(stderr: str) -> bool:
        text = str(stderr or "").strip().lower()
        if not text:
            return False
        return (
            ".clang-format" in text
            or "error reading" in text
            or "yaml" in text
            or "while parsing" in text
            or "style" in text and "file" in text and "error" in text
        )
