"""Python code formatting provider backed by Ruff."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QRadioButton, QVBoxLayout, QWidget

from src.formatting.code_formatting import FormatRequest, FormatResult
from src.ui.custom_dialog import DialogWindow


PYTHON_FORMAT_EXTENSIONS = {".py", ".pyi", ".pyw"}
PYTHON_FORMAT_LANGUAGE_IDS = {"python", "py"}


class _RuffBootstrapDialog(DialogWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            use_native_chrome=bool(getattr(parent, "use_native_chrome", True)),
            resizable=False,
            parent=parent,
        )
        self.setWindowTitle("No ruff.toml found")
        self.resize(500, 210)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        text = QLabel(
            "This project has no ruff.toml. Choose a config style to create one in the project root, then format."
        )
        text.setWordWrap(True)
        root.addWidget(text)

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

    def wants_full(self) -> bool:
        return bool(self.full.isChecked())


class PythonRuffFormatProvider:
    def __init__(self, *, canonicalize, path_has_prefix) -> None:
        self._canonicalize = canonicalize
        self._path_has_prefix = path_has_prefix

    def can_format(self, language_id: str, *, file_path: str = "") -> bool:
        lang = str(language_id or "").strip().lower()
        if lang in PYTHON_FORMAT_LANGUAGE_IDS:
            return True
        ext = str(Path(str(file_path or "")).suffix or "").strip().lower()
        return ext in PYTHON_FORMAT_EXTENSIONS

    def format_document(self, request: FormatRequest) -> FormatResult:
        return self._format(request)

    def format_selection(
        self,
        request: FormatRequest,
        *,
        start_line: int,
        end_line: int,
    ) -> FormatResult:
        # Ruff format is whole-file; keep v1 behavior simple and stable.
        _ = start_line, end_line
        out = self._format(request)
        out.debug_lines = list(out.debug_lines) + ["[Format] Ruff selection formatting falls back to whole-file format."]
        return out

    def _format(self, request: FormatRequest) -> FormatResult:
        file_path = self._canonicalize(str(request.file_path or ""))
        if not file_path:
            return FormatResult(status="error", message="Save the file before formatting.")

        source_text = str(request.source_text or "")
        if not source_text:
            return FormatResult(status="ok", formatted_text=source_text, message="Nothing to format.")

        debug_lines: list[str] = []
        cfg_dir, setup = self._ensure_config_available(
            file_path=file_path,
            project_root=str(request.project_root or ""),
            parent=request.parent_widget,
        )
        debug_lines.extend(setup.debug_lines)
        if setup.status == "canceled":
            out = FormatResult(status="canceled", message="Formatting canceled.")
            out.debug_lines = debug_lines
            return out
        if setup.status == "error" or not cfg_dir:
            out = FormatResult(
                status="error",
                message=setup.message or "Could not prepare Ruff configuration.",
                created_config_path=setup.created_config_path,
            )
            out.debug_lines = debug_lines
            return out

        run_result = self._run_ruff_format(
            interpreter=str(request.interpreter or "").strip(),
            cfg_dir=cfg_dir,
            file_path=file_path,
            source_text=source_text,
            parent=request.parent_widget,
        )
        debug_lines.extend(run_result.debug_lines)
        if run_result.ok:
            run_result.debug_lines = debug_lines
            if setup.created_config_path:
                run_result.created_config_path = setup.created_config_path
            return run_result

        if self._looks_like_config_error(run_result.stderr):
            cfg_dir_retry, retry_setup = self._ensure_config_available(
                file_path=file_path,
                project_root=str(request.project_root or ""),
                parent=request.parent_widget,
                force_prompt=True,
            )
            debug_lines.extend(retry_setup.debug_lines)
            if retry_setup.status == "canceled":
                out = FormatResult(status="canceled", message="Formatting canceled.")
                out.debug_lines = debug_lines
                return out
            if retry_setup.status == "error" or not cfg_dir_retry:
                out = FormatResult(
                    status="error",
                    message=retry_setup.message or run_result.message,
                    stderr=run_result.stderr,
                    created_config_path=retry_setup.created_config_path,
                )
                out.debug_lines = debug_lines
                return out
            rerun = self._run_ruff_format(
                interpreter=str(request.interpreter or "").strip(),
                cfg_dir=cfg_dir_retry,
                file_path=file_path,
                source_text=source_text,
                parent=request.parent_widget,
            )
            debug_lines.extend(rerun.debug_lines)
            rerun.debug_lines = debug_lines
            if retry_setup.created_config_path:
                rerun.created_config_path = retry_setup.created_config_path
            return rerun

        run_result.debug_lines = debug_lines
        return run_result

    def _run_ruff_format(
        self,
        *,
        interpreter: str,
        cfg_dir: str,
        file_path: str,
        source_text: str,
        parent: object | None,
    ) -> FormatResult:
        candidates = self._ruff_command_candidates(interpreter=interpreter, file_path=file_path)
        debug = [f"[Format] Ruff cwd: {cfg_dir}"]
        last_err = ""

        for cmd in candidates:
            debug.append(f"[Format] cmd: {' '.join(cmd)}")
            try:
                proc = subprocess.run(
                    cmd,
                    input=source_text,
                    text=True,
                    capture_output=True,
                    cwd=cfg_dir,
                    check=False,
                )
            except FileNotFoundError:
                last_err = f"Command not found: {cmd[0]}"
                debug.append(f"[Format][stderr] {last_err}")
                continue
            except Exception as exc:
                last_err = str(exc)
                debug.append(f"[Format][stderr] {last_err}")
                continue

            stderr = str(proc.stderr or "").strip()
            if proc.returncode == 0:
                return FormatResult(status="ok", formatted_text=str(proc.stdout or ""), stderr=stderr, debug_lines=debug)

            debug.append(f"[Format][stderr] {stderr or f'exit {proc.returncode}'}")
            if self._looks_like_missing_ruff_backend(stderr):
                last_err = stderr or "Ruff backend unavailable."
                continue
            return FormatResult(
                status="error",
                message=stderr or f"Ruff format failed (exit {proc.returncode}).",
                stderr=stderr,
                debug_lines=debug,
            )

        self._show_missing_tool_dialog(parent)
        return FormatResult(
            status="error",
            message="Ruff formatter not found. Install ruff in your interpreter or PATH.",
            stderr=last_err,
            debug_lines=debug,
        )

    def _ruff_command_candidates(self, *, interpreter: str, file_path: str) -> list[list[str]]:
        candidates: list[list[str]] = []
        interp = str(interpreter or "").strip()
        if interp:
            candidates.append([interp, "-m", "ruff", "format", "--stdin-filename", file_path, "-"])
        ruff_bin = shutil.which("ruff")
        if ruff_bin:
            candidates.append([ruff_bin, "format", "--stdin-filename", file_path, "-"])
        else:
            candidates.append(["ruff", "format", "--stdin-filename", file_path, "-"])
        # Deduplicate while preserving order.
        out: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for cmd in candidates:
            key = tuple(cmd)
            if key in seen:
                continue
            seen.add(key)
            out.append(cmd)
        return out

    def _ensure_config_available(
        self,
        *,
        file_path: str,
        project_root: str,
        parent: object | None,
        force_prompt: bool = False,
    ) -> tuple[str, FormatResult]:
        cfg_dir, state, cfg_path = self._discover_ruff_config(file_path=file_path, project_root=project_root)
        if cfg_dir and state == "found" and not force_prompt:
            ok = FormatResult(status="ok")
            ok.debug_lines = [f"[Format] Ruff config: {cfg_path}"]
            return cfg_dir, ok

        parent_widget = parent if isinstance(parent, QWidget) else None
        dialog = _RuffBootstrapDialog(parent=parent_widget)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return "", FormatResult(status="canceled", message="Formatting canceled.")

        target_dir, write_result = self._create_ruff_toml(
            file_path=file_path,
            project_root=project_root,
            full_config=dialog.wants_full(),
        )
        if write_result.status == "error":
            if parent_widget is not None:
                QMessageBox.warning(parent_widget, "Ruff", write_result.message or "Failed to create ruff.toml.")
            return "", write_result
        return target_dir, write_result

    def _discover_ruff_config(self, *, file_path: str, project_root: str) -> tuple[str, str, str]:
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
            # Preferred dedicated Ruff config files.
            for name in ("ruff.toml", ".ruff.toml"):
                candidate = cur / name
                if not candidate.exists():
                    continue
                if not candidate.is_file():
                    return "", "unreadable", str(candidate)
                try:
                    with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.read(1)
                    return str(cur), "found", str(candidate)
                except Exception:
                    return "", "unreadable", str(candidate)

            pyproject = cur / "pyproject.toml"
            if pyproject.exists():
                if not pyproject.is_file():
                    return "", "unreadable", str(pyproject)
                try:
                    content = pyproject.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return "", "unreadable", str(pyproject)
                if "[tool.ruff" in content:
                    return str(cur), "found", str(pyproject)

            if stop_dir is not None and cur == stop_dir:
                break
            parent = cur.parent
            if parent == cur:
                break
            cur = parent

        return "", "missing", ""

    def _create_ruff_toml(
        self,
        *,
        file_path: str,
        project_root: str,
        full_config: bool,
    ) -> tuple[str, FormatResult]:
        cfile = self._canonicalize(file_path)
        file_dir = str(Path(cfile).parent)
        croot = self._canonicalize(str(project_root or ""))
        use_root = bool(croot and self._path_has_prefix(cfile, croot))
        preferred_dir = croot if use_root else file_dir
        fallback_dir = file_dir
        content = self._ruff_toml_content(full_config=full_config)

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
            cfg_path = os.path.join(target_dir, "ruff.toml")
            try:
                with open(cfg_path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                out = FormatResult(
                    status="ok",
                    message=f"Created ruff.toml in {target_dir}",
                    created_config_path=cfg_path,
                )
                out.debug_lines = [f"[Format] Wrote {cfg_path}"]
                return target_dir, out
            except Exception as exc:
                out = FormatResult(status="error", message=f"Could not create ruff.toml in {target_dir}: {exc}")
                out.debug_lines = [f"[Format] {out.message}"]
                continue
        return "", FormatResult(status="error", message="Could not create ruff.toml in project or file directory.")

    @staticmethod
    def _ruff_toml_content(*, full_config: bool) -> str:
        if not full_config:
            return (
                "line-length = 100\n\n"
                "[format]\n"
                "quote-style = \"double\"\n"
                "indent-style = \"space\"\n"
                "line-ending = \"lf\"\n"
            )
        return (
            "line-length = 100\n"
            "target-version = \"py311\"\n\n"
            "[format]\n"
            "quote-style = \"double\"\n"
            "indent-style = \"space\"\n"
            "line-ending = \"lf\"\n"
            "skip-magic-trailing-comma = false\n"
            "docstring-code-format = true\n"
            "docstring-code-line-length = \"dynamic\"\n"
        )

    @staticmethod
    def _looks_like_missing_ruff_backend(stderr: str) -> bool:
        text = str(stderr or "").strip().lower()
        return (
            "no module named ruff" in text
            or "can't open file" in text and "ruff" in text
            or "is not recognized as an internal or external command" in text
            or "command not found" in text and "ruff" in text
        )

    @staticmethod
    def _looks_like_config_error(stderr: str) -> bool:
        text = str(stderr or "").strip().lower()
        if not text:
            return False
        return (
            "ruff.toml" in text
            or ".ruff.toml" in text
            or "pyproject.toml" in text
            or "failed to parse" in text
            or "invalid configuration" in text
            or "toml parse error" in text
        )

    def _show_missing_tool_dialog(self, parent: object | None) -> None:
        widget = parent if isinstance(parent, QWidget) else None
        QMessageBox.warning(
            widget,
            "Ruff not found",
            "Ruff executable was not found.\nInstall ruff in your selected interpreter or make `ruff` available on PATH.",
        )
