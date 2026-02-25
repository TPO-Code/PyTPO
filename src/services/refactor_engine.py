"""Source rewriting helpers extracted from the IDE window."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from src.services.ast_query import unused_import_name_from_diagnostic


@dataclass(frozen=True)
class SourceEditResult:
    source_text: str
    status: str


@dataclass(frozen=True)
class RemoveImportResult:
    source_text: str
    removed_name: str
    status: str


@dataclass(frozen=True)
class ExtractRefactorResult:
    source_text: str
    status: str
    message: str = ""


def _alias_matches_unused_target(alias: ast.alias, target: str, *, node_module: str = "") -> bool:
    needle = str(target or "").strip()
    if not needle:
        return False
    needle_variants: set[str] = {needle}
    if "." in needle:
        parts = [p for p in needle.split(".") if p]
        if parts:
            needle_variants.add(parts[-1])
            needle_variants.add(parts[0])

    alias_name = str(getattr(alias, "name", "") or "").strip()
    alias_as = str(getattr(alias, "asname", "") or "").strip()
    candidates: set[str] = set()
    if alias_name:
        candidates.add(alias_name)
        candidates.add(alias_name.split(".", 1)[0])
        parts = alias_name.split(".")
        if parts:
            candidates.add(parts[-1])
        if node_module:
            candidates.add(f"{node_module}.{alias_name}")
    if alias_as:
        candidates.add(alias_as)
        if alias_name:
            candidates.add(f"{alias_name} as {alias_as}")
            if node_module:
                candidates.add(f"{node_module}.{alias_name} as {alias_as}")
    normalized = {c.strip() for c in candidates if c.strip()}
    return any(n in normalized for n in needle_variants if n)


def _render_import_alias(alias: ast.alias) -> str:
    name = str(getattr(alias, "name", "") or "").strip()
    as_name = str(getattr(alias, "asname", "") or "").strip()
    if not name:
        return ""
    if as_name:
        return f"{name} as {as_name}"
    return name


def _render_import_statement(node: ast.Import | ast.ImportFrom, aliases: list[ast.alias]) -> str:
    parts = [_render_import_alias(alias) for alias in aliases]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if isinstance(node, ast.Import):
        return "import " + ", ".join(parts)
    module = str(getattr(node, "module", "") or "")
    level = int(getattr(node, "level", 0) or 0)
    prefix = "." * max(0, level)
    full_module = f"{prefix}{module}" if module else prefix
    if not full_module:
        return ""
    return f"from {full_module} import " + ", ".join(parts)


def remove_unused_import_from_source(source_text: str, diag: dict | None) -> RemoveImportResult:
    text = str(source_text or "")
    if not text:
        return RemoveImportResult(text, "", "error")
    if not isinstance(diag, dict):
        return RemoveImportResult(text, "", "error")

    try:
        target_line = int(diag.get("line") or 0)
    except Exception:
        target_line = 0
    if target_line <= 0:
        return RemoveImportResult(text, "", "error")

    try:
        tree = ast.parse(text)
    except Exception:
        return RemoveImportResult(text, "", "error")

    import_nodes: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        start = int(getattr(node, "lineno", 0) or 0)
        end = int(getattr(node, "end_lineno", start) or start)
        if start <= target_line <= end:
            import_nodes.append(node)
    if not import_nodes:
        return RemoveImportResult(text, "", "error")

    import_nodes.sort(
        key=lambda n: (
            int(getattr(n, "end_lineno", getattr(n, "lineno", 0))) - int(getattr(n, "lineno", 0)),
            int(getattr(n, "lineno", 0)),
        )
    )
    node = import_nodes[0]

    unused_target = unused_import_name_from_diagnostic(diag)
    names = list(getattr(node, "names", []) or [])
    if not names:
        return RemoveImportResult(text, "", "error")

    remove_idx = -1
    removed_name = ""
    node_module = ""
    if isinstance(node, ast.ImportFrom):
        level = int(getattr(node, "level", 0) or 0)
        module_name = str(getattr(node, "module", "") or "").strip()
        node_module = ("." * max(0, level)) + module_name if module_name or level > 0 else ""

    for idx, alias in enumerate(names):
        if _alias_matches_unused_target(alias, unused_target, node_module=node_module):
            remove_idx = idx
            removed_name = str(getattr(alias, "asname", None) or getattr(alias, "name", "") or "").strip()
            break
    if remove_idx < 0:
        if len(names) == 1:
            remove_idx = 0
            alias0 = names[0]
            removed_name = str(getattr(alias0, "asname", None) or getattr(alias0, "name", "") or "").strip()
        else:
            return RemoveImportResult(text, "", "error")

    remaining = [alias for idx, alias in enumerate(names) if idx != remove_idx]

    lines = text.splitlines(keepends=True)
    line_offsets = [0]
    for raw in lines:
        line_offsets.append(line_offsets[-1] + len(raw))

    start_line = max(1, int(getattr(node, "lineno", 1)))
    end_line = max(start_line, int(getattr(node, "end_lineno", start_line) or start_line))
    if start_line - 1 >= len(line_offsets) - 1:
        return RemoveImportResult(text, "", "error")

    start_idx = line_offsets[start_line - 1]
    end_idx = line_offsets[min(end_line, len(lines))]
    segment = text[start_idx:end_idx]

    replacement = ""
    if remaining:
        indent = ""
        if 1 <= start_line <= len(lines):
            line_raw = lines[start_line - 1]
            match = re.match(r"[ \t]*", line_raw)
            indent = match.group(0) if match else ""
        replacement_stmt = _render_import_statement(node, remaining)
        if not replacement_stmt:
            return RemoveImportResult(text, "", "error")
        newline = ""
        if segment.endswith("\r\n"):
            newline = "\r\n"
        elif segment.endswith("\n"):
            newline = "\n"
        replacement = f"{indent}{replacement_stmt}{newline}"

    updated = text[:start_idx] + replacement + text[end_idx:]
    if updated == text:
        return RemoveImportResult(text, "", "already")
    return RemoveImportResult(updated, removed_name, "updated")


def _append_symbol_to_from_import_line(text: str, node: ast.ImportFrom, token: str) -> str | None:
    line_start = max(1, int(getattr(node, "lineno", 1))) - 1
    line_end = max(1, int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))) - 1
    if line_start != line_end:
        return None

    lines = text.splitlines(keepends=True)
    if line_start < 0 or line_start >= len(lines):
        return None
    raw = lines[line_start]
    body = raw.rstrip("\r\n")
    newline = raw[len(body):] or "\n"
    if "(" in body or ")" in body or "\\" in body:
        return None

    if "#" in body:
        hash_idx = body.index("#")
        prefix = body[:hash_idx].rstrip()
        suffix = body[hash_idx:]
        if not prefix:
            return None
        new_body = f"{prefix}, {token} {suffix}"
    else:
        new_body = f"{body.rstrip()}, {token}"

    lines[line_start] = new_body + newline
    return "".join(lines)


def _line_after_possible_multiline_import(lines: list[str], start_idx: int) -> int:
    i = max(0, int(start_idx))
    depth = 0
    while i < len(lines):
        raw = lines[i]
        body = raw.split("#", 1)[0]
        stripped = body.rstrip()
        depth += stripped.count("(") - stripped.count(")")
        if depth < 0:
            depth = 0
        continued = stripped.endswith("\\")
        i += 1
        if depth == 0 and not continued:
            break
    return i


def _import_insertion_line(text: str, tree: ast.AST | None) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0

    line_idx = 0
    if lines and lines[0].startswith("#!"):
        line_idx = 1

    enc_re = re.compile(r"coding[:=]\s*[-\w.]+")
    for i in range(min(2, len(lines))):
        if enc_re.search(lines[i]):
            line_idx = max(line_idx, i + 1)

    if isinstance(tree, ast.Module) and tree.body:
        first = tree.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(getattr(first, "value", None), ast.Constant)
            and isinstance(first.value.value, str)
        ):
            doc_end = int(getattr(first, "end_lineno", first.lineno))
            line_idx = max(line_idx, doc_end)

    import_spans: dict[int, int] = {}
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            start = max(0, int(getattr(node, "lineno", 1) or 1) - 1)
            end = max(start, int(getattr(node, "end_lineno", getattr(node, "lineno", 1)) or (start + 1)) - 1)
            import_spans[start] = end

    found_import = False
    i = line_idx
    while i < len(lines):
        span_end = import_spans.get(i)
        if span_end is not None:
            found_import = True
            i = min(len(lines), span_end + 1)
            continue

        stripped = lines[i].strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            found_import = True
            i = _line_after_possible_multiline_import(lines, i)
            continue
        if found_import and (not stripped or stripped.startswith("#")):
            i += 1
            continue
        break
    return i if found_import else line_idx


def _insert_line_at(text: str, line_idx: int, line_text: str) -> str:
    lines = text.splitlines(keepends=True)
    idx = max(0, min(int(line_idx), len(lines)))
    lines.insert(idx, line_text)
    return "".join(lines)


def insert_module_import(source_text: str, module_name: str, bind_name: str) -> SourceEditResult:
    module = str(module_name or "").strip()
    bind = str(bind_name or "").strip()
    if not module or not bind:
        return SourceEditResult(str(source_text or ""), "error")

    original = str(source_text or "")
    tree = None
    try:
        tree = ast.parse(original)
    except Exception:
        tree = None

    if tree is not None:
        for node in tree.body:
            if not isinstance(node, ast.Import):
                continue
            for alias in node.names:
                full = str(alias.name or "").strip()
                as_name = str(alias.asname or "").strip()
                visible = as_name or full.split(".", 1)[0]
                if visible == bind and (full == module or full.split(".", 1)[0] == module):
                    return SourceEditResult(original, "already")

    insertion_line = _import_insertion_line(original, tree)
    import_line = f"import {module}\n"
    updated = _insert_line_at(original, insertion_line, import_line)
    if updated == original:
        return SourceEditResult(original, "error")
    return SourceEditResult(updated, "updated")


def insert_from_import(source_text: str, module_name: str, export_name: str, bind_name: str) -> SourceEditResult:
    module = str(module_name or "").strip()
    export = str(export_name or "").strip()
    bind = str(bind_name or "").strip()
    if not module or not export or not bind:
        return SourceEditResult(str(source_text or ""), "error")

    original = str(source_text or "")
    tree = None
    try:
        tree = ast.parse(original)
    except Exception:
        tree = None

    if tree is not None:
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level != 0 or str(node.module or "").strip() != module:
                continue
            imported_bindings = set()
            imported_names = set()
            for alias in node.names:
                alias_name = str(alias.name or "").strip()
                alias_bind = str(alias.asname or "").strip() or alias_name
                imported_names.add(alias_name)
                imported_bindings.add(alias_bind)
            if "*" in imported_names or bind in imported_bindings:
                return SourceEditResult(original, "already")

        token = export if bind == export else f"{export} as {bind}"
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level != 0 or str(node.module or "").strip() != module:
                continue
            updated = _append_symbol_to_from_import_line(original, node, token)
            if updated is not None:
                return SourceEditResult(updated, "updated")

    if bind == export:
        import_line = f"from {module} import {export}\n"
    else:
        import_line = f"from {module} import {export} as {bind}\n"
    insertion_line = _import_insertion_line(original, tree)
    updated = _insert_line_at(original, insertion_line, import_line)
    if updated == original:
        return SourceEditResult(original, "error")
    return SourceEditResult(updated, "updated")


def extract_python_variable(
    source_text: str,
    *,
    selection_start: int,
    selection_end: int,
    variable_name: str,
) -> ExtractRefactorResult:
    text = str(source_text or "")
    name = str(variable_name or "").strip()
    if not text:
        return ExtractRefactorResult(text, "error", "Empty document.")
    if not name:
        return ExtractRefactorResult(text, "error", "Variable name is required.")
    start, end = _normalized_selection_bounds(text, selection_start, selection_end)
    if end <= start:
        return ExtractRefactorResult(text, "error", "Select an expression first.")
    expr = text[start:end]
    if not expr.strip():
        return ExtractRefactorResult(text, "error", "Selection is empty.")
    if "\n" in expr or "\r" in expr:
        return ExtractRefactorResult(text, "error", "Extract Variable supports single-line expressions.")

    line_start = _line_start_offset(text, start)
    line_end = _line_end_offset(text, start)
    indent = _leading_indent(text[line_start:line_end])
    declaration = f"{indent}{name} = {expr.strip()}\n"
    updated = text[:line_start] + declaration + text[line_start:start] + name + text[end:]
    if updated == text:
        return ExtractRefactorResult(text, "already", "No changes made.")
    return ExtractRefactorResult(updated, "updated", f"Extracted variable '{name}'.")


def extract_cpp_variable(
    source_text: str,
    *,
    selection_start: int,
    selection_end: int,
    variable_name: str,
    use_auto_type: bool = True,
) -> ExtractRefactorResult:
    text = str(source_text or "")
    name = str(variable_name or "").strip()
    if not text:
        return ExtractRefactorResult(text, "error", "Empty document.")
    if not name:
        return ExtractRefactorResult(text, "error", "Variable name is required.")
    start, end = _normalized_selection_bounds(text, selection_start, selection_end)
    if end <= start:
        return ExtractRefactorResult(text, "error", "Select an expression first.")
    expr = text[start:end]
    if not expr.strip():
        return ExtractRefactorResult(text, "error", "Selection is empty.")
    if "\n" in expr or "\r" in expr:
        return ExtractRefactorResult(text, "error", "Extract Variable supports single-line expressions.")

    line_start = _line_start_offset(text, start)
    line_end = _line_end_offset(text, start)
    indent = _leading_indent(text[line_start:line_end])
    decl_type = "auto" if bool(use_auto_type) else "int"
    declaration = f"{indent}{decl_type} {name} = {expr.strip()};\n"
    updated = text[:line_start] + declaration + text[line_start:start] + name + text[end:]
    if updated == text:
        return ExtractRefactorResult(text, "already", "No changes made.")
    return ExtractRefactorResult(updated, "updated", f"Extracted variable '{name}'.")


def extract_python_method(
    source_text: str,
    *,
    selection_start: int,
    selection_end: int,
    method_name: str,
) -> ExtractRefactorResult:
    text = str(source_text or "")
    name = str(method_name or "").strip()
    if not text:
        return ExtractRefactorResult(text, "error", "Empty document.")
    if not name:
        return ExtractRefactorResult(text, "error", "Method name is required.")

    start, end = _normalized_selection_bounds(text, selection_start, selection_end)
    if end <= start:
        return ExtractRefactorResult(text, "error", "Select one or more lines first.")
    line_start = _line_start_offset(text, start)
    line_end = _line_end_offset(text, max(line_start, end - 1))
    body_src = text[line_start:line_end]
    if not body_src.strip():
        return ExtractRefactorResult(text, "error", "Selection is empty.")

    base_indent = _first_nonempty_line_indent(body_src)
    if base_indent is None:
        base_indent = _leading_indent(text[line_start:_line_end_offset(text, line_start)])
    dedented = _dedent_block(body_src, base_indent)
    if not dedented.endswith("\n"):
        dedented += "\n"
    method_body = _indent_block(dedented, base_indent + "    ")
    method_def = f"{base_indent}def {name}():\n{method_body}"
    call_line = f"{base_indent}{name}()\n"
    updated = text[:line_start] + method_def + "\n" + call_line + text[line_end:]
    if updated == text:
        return ExtractRefactorResult(text, "already", "No changes made.")
    return ExtractRefactorResult(updated, "updated", f"Extracted method '{name}'.")


def extract_cpp_method(
    source_text: str,
    *,
    selection_start: int,
    selection_end: int,
    method_name: str,
) -> ExtractRefactorResult:
    text = str(source_text or "")
    name = str(method_name or "").strip()
    if not text:
        return ExtractRefactorResult(text, "error", "Empty document.")
    if not name:
        return ExtractRefactorResult(text, "error", "Method name is required.")

    start, end = _normalized_selection_bounds(text, selection_start, selection_end)
    if end <= start:
        return ExtractRefactorResult(text, "error", "Select one or more lines first.")
    line_start = _line_start_offset(text, start)
    line_end = _line_end_offset(text, max(line_start, end - 1))
    body_src = text[line_start:line_end]
    if not body_src.strip():
        return ExtractRefactorResult(text, "error", "Selection is empty.")

    base_indent = _first_nonempty_line_indent(body_src)
    if base_indent is None:
        base_indent = _leading_indent(text[line_start:_line_end_offset(text, line_start)])
    dedented = _dedent_block(body_src, base_indent)
    if not dedented.endswith("\n"):
        dedented += "\n"
    method_body = _indent_block(dedented, base_indent + "    ")
    snippet = (
        f"{base_indent}auto {name} = [&]() {{\n"
        f"{method_body}"
        f"{base_indent}}};\n"
        f"{base_indent}{name}();\n"
    )
    updated = text[:line_start] + snippet + text[line_end:]
    if updated == text:
        return ExtractRefactorResult(text, "already", "No changes made.")
    return ExtractRefactorResult(updated, "updated", f"Extracted method '{name}'.")


def _normalized_selection_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    size = len(str(text or ""))
    lo = max(0, min(size, int(start or 0)))
    hi = max(0, min(size, int(end or 0)))
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _line_start_offset(text: str, position: int) -> int:
    pos = max(0, min(len(text), int(position or 0)))
    idx = text.rfind("\n", 0, pos)
    return 0 if idx < 0 else idx + 1


def _line_end_offset(text: str, position: int) -> int:
    pos = max(0, min(len(text), int(position or 0)))
    idx = text.find("\n", pos)
    return len(text) if idx < 0 else idx + 1


def _leading_indent(line_text: str) -> str:
    return re.match(r"[ \t]*", str(line_text or "")).group(0)


def _first_nonempty_line_indent(block_text: str) -> str | None:
    for raw in str(block_text or "").splitlines():
        if raw.strip():
            return _leading_indent(raw)
    return None


def _dedent_block(block_text: str, indent: str) -> str:
    src = str(block_text or "")
    if not indent:
        return src
    out: list[str] = []
    for raw in src.splitlines(keepends=True):
        if raw.startswith(indent):
            out.append(raw[len(indent):])
        else:
            out.append(raw)
    return "".join(out)


def _indent_block(block_text: str, prefix: str) -> str:
    src = str(block_text or "")
    if not src:
        return ""
    out: list[str] = []
    for raw in src.splitlines(keepends=True):
        if raw.strip():
            out.append(prefix + raw)
        else:
            out.append(raw)
    return "".join(out)
