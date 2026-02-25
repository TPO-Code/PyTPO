"""Rust formatting provider backed by cargo fmt / rustfmt."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from src.formatting.code_formatting import FormatRequest, FormatResult
from src.lang_rust.cargo_discovery import discover_workspace_root_for_file


RUST_FORMAT_EXTENSIONS = {".rs"}
RUST_FORMAT_LANGUAGE_IDS = {"rust"}


class RustFormatProvider:
    def __init__(self, *, canonicalize, path_has_prefix) -> None:
        self._canonicalize = canonicalize
        self._path_has_prefix = path_has_prefix

    def can_format(self, language_id: str, *, file_path: str = "") -> bool:
        lang = str(language_id or "").strip().lower()
        if lang in RUST_FORMAT_LANGUAGE_IDS:
            return True
        ext = str(Path(str(file_path or "")).suffix or "").strip().lower()
        return ext in RUST_FORMAT_EXTENSIONS

    def format_document(self, request: FormatRequest) -> FormatResult:
        file_path = self._canonicalize(str(request.file_path or ""))
        if not file_path:
            return FormatResult(status="error", message="Save the file before formatting.")

        source_text = str(request.source_text or "")
        if not source_text:
            return FormatResult(status="ok", formatted_text=source_text, message="Nothing to format.")

        cargo_root = self._cargo_root_for_file(file_path=file_path, project_root=str(request.project_root or ""))
        debug_lines: list[str] = []

        if cargo_root:
            cargo_result = self._try_cargo_fmt(
                file_path=file_path,
                source_text=source_text,
                cargo_root=cargo_root,
            )
            debug_lines.extend(cargo_result.debug_lines)
            if cargo_result.ok:
                cargo_result.debug_lines = debug_lines
                return cargo_result

        rustfmt_result = self._run_rustfmt(
            file_path=file_path,
            source_text=source_text,
            cwd=cargo_root or os.path.dirname(file_path),
        )
        debug_lines.extend(rustfmt_result.debug_lines)
        rustfmt_result.debug_lines = debug_lines
        return rustfmt_result

    def format_selection(
        self,
        request: FormatRequest,
        *,
        start_line: int,
        end_line: int,
    ) -> FormatResult:
        _ = start_line, end_line
        result = self.format_document(request)
        result.debug_lines = list(result.debug_lines) + [
            "[Format] Rust selection formatting falls back to whole-file format."
        ]
        return result

    def _cargo_root_for_file(self, *, file_path: str, project_root: str) -> str:
        root = discover_workspace_root_for_file(
            file_path=file_path,
            project_root=project_root,
            canonicalize=self._canonicalize,
            path_has_prefix=self._path_has_prefix,
        )
        return self._canonicalize(root) if root else ""

    def _try_cargo_fmt(self, *, file_path: str, source_text: str, cargo_root: str) -> FormatResult:
        debug = [f"[Format] cargo root: {cargo_root}"]
        cargo_bin = shutil.which("cargo") or "cargo"
        debug.append(f"[Format] cargo: {cargo_bin}")

        disk_text, disk_ok = self._read_file(file_path)
        if not disk_ok:
            debug.append("[Format] cargo fmt skipped: file is not readable on disk.")
            return FormatResult(status="error", message="", debug_lines=debug)
        if disk_text != source_text:
            debug.append("[Format] cargo fmt skipped: editor buffer has unsaved changes; using rustfmt stdin.")
            return FormatResult(status="error", message="", debug_lines=debug)

        cmd = [cargo_bin, "fmt"]
        debug.append(f"[Format] cmd: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                cwd=cargo_root,
                check=False,
            )
        except FileNotFoundError:
            debug.append(f"[Format][stderr] Command not found: {cargo_bin}")
            return FormatResult(status="error", message="", debug_lines=debug)
        except Exception as exc:
            debug.append(f"[Format][stderr] {exc}")
            return FormatResult(status="error", message="", debug_lines=debug)

        stderr = str(proc.stderr or "").strip()
        if proc.returncode != 0:
            debug.append(f"[Format][stderr] {stderr or f'exit {proc.returncode}'}")
            return FormatResult(status="error", message=stderr, stderr=stderr, debug_lines=debug)

        formatted_text, ok = self._read_file(file_path)
        if not ok:
            return FormatResult(
                status="error",
                message="cargo fmt completed, but the file could not be reloaded.",
                stderr=stderr,
                debug_lines=debug,
            )
        return FormatResult(status="ok", formatted_text=formatted_text, stderr=stderr, debug_lines=debug)

    def _run_rustfmt(self, *, file_path: str, source_text: str, cwd: str) -> FormatResult:
        debug = [f"[Format] rustfmt cwd: {cwd}"]
        rustfmt_bin = shutil.which("rustfmt") or "rustfmt"
        cmd = [
            rustfmt_bin,
            "--emit",
            "stdout",
            "--stdin-filepath",
            file_path,
        ]
        debug.append(f"[Format] cmd: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                input=source_text,
                text=True,
                capture_output=True,
                cwd=cwd if os.path.isdir(cwd) else None,
                check=False,
            )
        except FileNotFoundError:
            return FormatResult(
                status="error",
                message="Rust formatter not found. Install rustfmt via rustup.",
                debug_lines=debug,
            )
        except Exception as exc:
            return FormatResult(status="error", message=str(exc), debug_lines=debug)

        stderr = str(proc.stderr or "").strip()
        if proc.returncode != 0:
            debug.append(f"[Format][stderr] {stderr or f'exit {proc.returncode}'}")
            return FormatResult(
                status="error",
                message=stderr or f"rustfmt failed (exit {proc.returncode}).",
                stderr=stderr,
                debug_lines=debug,
            )
        return FormatResult(
            status="ok",
            formatted_text=str(proc.stdout or ""),
            stderr=stderr,
            debug_lines=debug,
        )

    @staticmethod
    def _read_file(path: str) -> tuple[str, bool]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read(), True
        except Exception:
            return "", False

