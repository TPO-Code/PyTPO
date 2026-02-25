"""Read-only AST and diagnostic query helpers extracted from the IDE window."""

from __future__ import annotations

import ast
import keyword
import re
from pathlib import Path


def modules_mentioned_in_imports(source_text: str) -> set[str]:
    modules: set[str] = set()
    text = str(source_text or "")
    if not text.strip():
        return modules
    try:
        tree = ast.parse(text)
    except Exception:
        return modules

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and isinstance(node.module, str):
            mod = node.module.strip()
            if mod:
                modules.add(mod)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = str(alias.name or "").strip()
                if name:
                    modules.add(name)
    return modules


def is_valid_python_identifier(name: str) -> bool:
    text = str(name or "").strip()
    return bool(text) and text.isidentifier() and not keyword.iskeyword(text)


def project_module_name_for_file(
    file_path: str,
    *,
    canonicalize,
    rel_to_project,
    normalize_rel,
) -> str:
    cpath = canonicalize(file_path)
    rel = rel_to_project(cpath)
    if rel == cpath:
        return ""
    rel_norm = normalize_rel(rel)
    if not rel_norm.endswith(".py"):
        return ""

    stem = rel_norm[:-3]
    parts = [part for part in stem.split("/") if part]
    if not parts:
        return ""
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return ""
    if any(not is_valid_python_identifier(part) for part in parts):
        return ""
    return ".".join(parts)


def collect_assignment_names(target: ast.AST, names: set[str]) -> None:
    if isinstance(target, ast.Name):
        if is_valid_python_identifier(target.id):
            names.add(target.id)
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            collect_assignment_names(item, names)


def project_file_exported_names(file_path: str) -> set[str]:
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return set()

    try:
        tree = ast.parse(source, filename=file_path)
    except Exception:
        return set()

    exported: set[str] = set()
    type_alias_node = getattr(ast, "TypeAlias", None)
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = str(getattr(node, "name", "") or "").strip()
            if is_valid_python_identifier(name):
                exported.add(name)
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                collect_assignment_names(target, exported)
            continue
        if isinstance(node, ast.AnnAssign):
            collect_assignment_names(node.target, exported)
            continue
        if type_alias_node is not None and isinstance(node, type_alias_node):
            collect_assignment_names(node.name, exported)
    return exported


def missing_symbol_from_diagnostic(diag: dict | None) -> str:
    if not isinstance(diag, dict):
        return ""
    message = str(diag.get("message") or "").strip()
    if not message:
        return ""
    patterns = (
        r"(?:Undefined|undefined)\s+name\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?",
        r"name\s+[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]\s+is\s+not\s+defined",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def is_unused_import_diagnostic(diag: dict | None) -> bool:
    if not isinstance(diag, dict):
        return False
    code = str(diag.get("code") or "").strip().upper()
    if code in {"F401", "W0611"}:
        return True
    message = str(diag.get("message") or "").strip().lower()
    return ("imported but unused" in message) or ("unused import" in message)


def unused_import_name_from_diagnostic(diag: dict | None) -> str:
    if not isinstance(diag, dict):
        return ""
    message = str(diag.get("message") or "").strip()
    if not message:
        return ""
    patterns = (
        r"[`'\"]([^`'\"]+)[`'\"]\s+imported\s+but\s+unused",
        r"unused\s+import[:\s]+[`'\"]?([^`'\"\:]+)[`'\"]?",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def symbol_used_as_module(source_text: str, diag: dict | None, symbol: str) -> bool:
    text = str(source_text or "")
    name = str(symbol or "").strip()
    if not text or not name:
        return False

    line_no = 0
    if isinstance(diag, dict):
        try:
            line_no = int(diag.get("line") or 0)
        except Exception:
            line_no = 0
    if line_no > 0:
        lines = text.splitlines()
        if line_no <= len(lines):
            line_text = lines[line_no - 1]
            if re.search(rf"\b{re.escape(name)}\s*\.", line_text):
                return True
    return bool(re.search(rf"\b{re.escape(name)}\s*\.", text))
