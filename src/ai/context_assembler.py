from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AssembledContext:
    system_prompt: str
    user_prompt: str
    token_estimate: int
    metadata: dict[str, Any]


class ContextAssembler:
    def __init__(self, project_root: str, canonicalize) -> None:
        self.project_root = str(project_root or "")
        self._canonicalize = canonicalize

    def assemble_inline(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        max_context_tokens: int,
        retrieval_snippets: int,
        context_radius_lines: int = 75,
        enclosing_block_max_chars: int = 7000,
        imports_outline_max_imports: int = 50,
        imports_outline_max_symbols: int = 120,
        retrieval_file_read_cap_chars: int = 18000,
        retrieval_same_dir_file_limit: int = 40,
        retrieval_recent_file_limit: int = 80,
        retrieval_walk_file_limit: int = 120,
        retrieval_total_candidate_limit: int = 180,
        retrieval_snippet_char_cap: int = 420,
        retrieval_snippet_segment_limit: int = 80,
        recent_files: list[str] | None = None,
    ) -> AssembledContext:
        text = str(source_text or "")
        lines = text.splitlines()
        cline = max(1, int(line or 1))
        ccol = max(0, int(column or 0))
        language = self._language_for_path(file_path)
        fence_lang = self._fence_lang(language)

        neighborhood, neighborhood_meta = self._cursor_neighborhood(
            lines,
            cline,
            radius=max(1, int(context_radius_lines)),
        )
        enclosing_block, enclosing_meta = self._enclosing_symbol_block(
            text,
            cline,
            language=language,
            max_chars=max(1, int(enclosing_block_max_chars)),
        )
        imports_outline, imports_meta = self._imports_and_outline(
            text,
            language=language,
            max_imports=max(0, int(imports_outline_max_imports)),
            max_symbols=max(0, int(imports_outline_max_symbols)),
        )
        snippets, snippet_meta = self._retrieved_snippets(
            file_path=file_path,
            source_text=text,
            line=cline,
            retrieval_snippets=max(0, int(retrieval_snippets)),
            file_read_cap_chars=max(1, int(retrieval_file_read_cap_chars)),
            snippet_char_cap=max(1, int(retrieval_snippet_char_cap)),
            snippet_segment_limit=max(1, int(retrieval_snippet_segment_limit)),
            same_dir_file_limit=max(0, int(retrieval_same_dir_file_limit)),
            recent_file_limit=max(0, int(retrieval_recent_file_limit)),
            walk_file_limit=max(0, int(retrieval_walk_file_limit)),
            total_candidate_limit=max(0, int(retrieval_total_candidate_limit)),
            recent_files=recent_files or [],
        )

        current_line = lines[cline - 1] if 1 <= cline <= len(lines) else ""
        cursor_prefix = current_line[: min(len(current_line), ccol)]

        sections: list[str] = []
        sections.append(f"## File\nPath: {file_path}\nLanguage: {language}\nCursor: line {cline}, column {ccol}")
        if imports_outline:
            sections.append(f"## Imports and Outline\n{imports_outline}")
        if enclosing_block:
            sections.append(f"## Enclosing Block\n```{fence_lang}\n{enclosing_block}\n```")
        sections.append(f"## Cursor Neighborhood\n```{fence_lang}\n{neighborhood}\n```")
        if snippets:
            parts: list[str] = []
            for idx, item in enumerate(snippets, start=1):
                parts.append(
                    f"### Snippet {idx} ({item['path']})\n```{fence_lang}\n{item['text']}\n```"
                )
            sections.append("## Retrieved Project Snippets\n" + "\n\n".join(parts))

        sections.append(
            "## Instruction\n"
            f"Language: {language}. "
            "Complete code at the cursor in the current project style. "
            "Return code continuation only. No explanations, no markdown fences."
        )
        sections.append(f"## Cursor Prefix\n{cursor_prefix}")

        user_prompt = "\n\n".join(section for section in sections if section.strip())
        token_estimate = self._estimate_tokens(user_prompt)
        if token_estimate > max(256, int(max_context_tokens)):
            user_prompt = self._trim_prompt(user_prompt, max_tokens=max(256, int(max_context_tokens)))
            token_estimate = self._estimate_tokens(user_prompt)

        metadata: dict[str, Any] = {
            "language": language,
            "line": cline,
            "column": ccol,
            "token_estimate": token_estimate,
            "neighborhood": neighborhood_meta,
            "enclosing": enclosing_meta,
            "imports_outline": imports_meta,
            "retrieval": snippet_meta,
            "limits": {
                "context_radius_lines": int(context_radius_lines),
                "enclosing_block_max_chars": int(enclosing_block_max_chars),
                "imports_outline_max_imports": int(imports_outline_max_imports),
                "imports_outline_max_symbols": int(imports_outline_max_symbols),
                "retrieval_file_read_cap_chars": int(retrieval_file_read_cap_chars),
                "retrieval_same_dir_file_limit": int(retrieval_same_dir_file_limit),
                "retrieval_recent_file_limit": int(retrieval_recent_file_limit),
                "retrieval_walk_file_limit": int(retrieval_walk_file_limit),
                "retrieval_total_candidate_limit": int(retrieval_total_candidate_limit),
                "retrieval_snippet_char_cap": int(retrieval_snippet_char_cap),
                "retrieval_snippet_segment_limit": int(retrieval_snippet_segment_limit),
            },
        }
        return AssembledContext(
            system_prompt=(
                "You are an inline coding assistant. "
                "Generate only the minimal continuation text that should appear at the cursor. "
                "Never include explanations."
            ),
            user_prompt=user_prompt,
            token_estimate=token_estimate,
            metadata=metadata,
        )

    def _cursor_neighborhood(self, lines: list[str], line: int, radius: int = 75) -> tuple[str, dict[str, int]]:
        if not lines:
            return "", {"start_line": 1, "end_line": 1, "line_count": 0}
        start = max(1, line - radius)
        end = min(len(lines), line + radius)
        chunk = "\n".join(lines[start - 1 : end])
        return chunk, {"start_line": start, "end_line": end, "line_count": (end - start + 1)}

    def _imports_and_outline(
        self,
        text: str,
        *,
        language: str,
        max_imports: int,
        max_symbols: int,
    ) -> tuple[str, dict[str, int]]:
        if language != "python":
            return "", {"imports": 0, "symbols": 0}
        if not text.strip():
            return "", {"imports": 0, "symbols": 0}
        try:
            tree = ast.parse(text)
        except Exception:
            return "", {"imports": 0, "symbols": 0}

        imports: list[str] = []
        symbols: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                parts = []
                for alias in node.names:
                    if alias.asname:
                        parts.append(f"{alias.name} as {alias.asname}")
                    else:
                        parts.append(str(alias.name))
                imports.append("import " + ", ".join(parts))
            elif isinstance(node, ast.ImportFrom):
                mod = str(node.module or "")
                parts = []
                for alias in node.names:
                    if alias.asname:
                        parts.append(f"{alias.name} as {alias.asname}")
                    else:
                        parts.append(str(alias.name))
                imports.append(f"from {mod} import " + ", ".join(parts))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = getattr(node, "name", "")
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                symbols.append(f"{kind} {name}")

        imports = imports[: max(0, int(max_imports))]
        symbols = symbols[: max(0, int(max_symbols))]
        out = []
        if imports:
            out.append("Imports:")
            out.extend(f"- {item}" for item in imports)
        if symbols:
            out.append("Symbols:")
            out.extend(f"- {item}" for item in symbols)
        return "\n".join(out), {"imports": len(imports), "symbols": len(symbols)}

    def _enclosing_symbol_block(
        self,
        text: str,
        line: int,
        *,
        language: str,
        max_chars: int,
    ) -> tuple[str, dict[str, Any]]:
        if language != "python":
            return "", {"type": "", "name": "", "start_line": 0, "end_line": 0}
        if not text.strip():
            return "", {"type": "", "name": "", "start_line": 0, "end_line": 0}
        try:
            tree = ast.parse(text)
        except Exception:
            return "", {"type": "", "name": "", "start_line": 0, "end_line": 0}

        best_node = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", start) or start)
            if start <= line <= end:
                if best_node is None:
                    best_node = node
                else:
                    best_start = int(getattr(best_node, "lineno", 0) or 0)
                    best_end = int(getattr(best_node, "end_lineno", best_start) or best_start)
                    if (end - start) < (best_end - best_start):
                        best_node = node

        if best_node is None:
            return "", {"type": "", "name": "", "start_line": 0, "end_line": 0}

        start = int(getattr(best_node, "lineno", 1))
        end = int(getattr(best_node, "end_lineno", start))
        lines = text.splitlines()
        snippet = "\n".join(lines[max(0, start - 1) : min(len(lines), end)])
        cap = max(1, int(max_chars))
        if len(snippet) > cap:
            snippet = snippet[:cap]
        kind = "class" if isinstance(best_node, ast.ClassDef) else "function"
        meta = {"type": kind, "name": str(getattr(best_node, "name", "")), "start_line": start, "end_line": end}
        return snippet, meta

    def _retrieved_snippets(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        retrieval_snippets: int,
        file_read_cap_chars: int,
        snippet_char_cap: int,
        snippet_segment_limit: int,
        same_dir_file_limit: int,
        recent_file_limit: int,
        walk_file_limit: int,
        total_candidate_limit: int,
        recent_files: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if retrieval_snippets <= 0:
            return [], {"enabled": False, "reason": "disabled"}

        index_dir = Path(self.project_root) / ".cache" / "completion"
        if not index_dir.is_dir():
            return [], {"enabled": False, "reason": "index_unavailable"}

        query = self._extract_query_tokens(source_text, line)
        if not query:
            return [], {"enabled": True, "reason": "no_query_tokens", "items": []}

        candidates = self._candidate_source_files(
            file_path=file_path,
            recent_files=recent_files,
            same_dir_file_limit=same_dir_file_limit,
            recent_file_limit=recent_file_limit,
            walk_file_limit=walk_file_limit,
            total_candidate_limit=total_candidate_limit,
        )
        recency_rank = {self._canonicalize_path(p): idx for idx, p in enumerate(recent_files)}

        ranked: list[tuple[float, str, str]] = []
        current = self._canonicalize_path(file_path)
        current_dir = os.path.dirname(current)
        for path in candidates:
            cpath = self._canonicalize_path(path)
            if cpath == current or not cpath.endswith((".py", ".pyw", ".pyi")):
                continue
            text = self._read_text_capped(cpath, cap_chars=file_read_cap_chars)
            if not text:
                continue
            snippets = self._split_snippets(
                text,
                cap_each=snippet_char_cap,
                max_segments=snippet_segment_limit,
            )
            if not snippets:
                continue
            for snippet in snippets:
                score = self._snippet_score(
                    snippet=snippet,
                    query_tokens=query,
                    path=cpath,
                    current_dir=current_dir,
                    recency_rank=recency_rank,
                )
                if score <= 0:
                    continue
                ranked.append((score, cpath, snippet))

        ranked.sort(key=lambda item: (-item[0], item[1].lower(), item[2][:32]))
        selected = ranked[:retrieval_snippets]
        out: list[dict[str, Any]] = []
        for score, path, snippet in selected:
            out.append({"path": self._rel_path(path), "score": float(score), "text": snippet})
        return out, {"enabled": True, "reason": "ok", "query_tokens": sorted(query), "items": out}

    def _extract_query_tokens(self, source_text: str, line: int) -> set[str]:
        lines = source_text.splitlines()
        current_line = lines[line - 1] if 1 <= line <= len(lines) else ""
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,40}", current_line))
        return {tok.lower() for tok in tokens if tok.lower() not in {"self", "true", "false", "none"}}

    def _candidate_source_files(
        self,
        *,
        file_path: str,
        recent_files: list[str],
        same_dir_file_limit: int,
        recent_file_limit: int,
        walk_file_limit: int,
        total_candidate_limit: int,
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def _add(path: str) -> None:
            c = self._canonicalize_path(path)
            if not c or c in seen:
                return
            seen.add(c)
            out.append(c)

        current = self._canonicalize_path(file_path)
        current_dir = os.path.dirname(current)
        if os.path.isdir(current_dir):
            try:
                for name in sorted(os.listdir(current_dir)):
                    if not name.endswith((".py", ".pyw", ".pyi")):
                        continue
                    _add(os.path.join(current_dir, name))
                    if same_dir_file_limit > 0 and len(out) >= same_dir_file_limit:
                        break
            except Exception:
                pass

        if recent_file_limit > 0:
            recent_added = 0
            for path in recent_files:
                before = len(out)
                _add(path)
                if len(out) > before:
                    recent_added += 1
                if recent_added >= recent_file_limit:
                    break

        root = self._canonicalize_path(self.project_root)
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "__pycache__", ".ruff_cache", ".mypy_cache"}]
            for fname in sorted(filenames):
                if not fname.endswith((".py", ".pyw", ".pyi")):
                    continue
                _add(os.path.join(dirpath, fname))
                count += 1
                if (walk_file_limit > 0 and count >= walk_file_limit) or (
                    total_candidate_limit > 0 and len(out) >= total_candidate_limit
                ):
                    return out
        return out

    def _split_snippets(self, text: str, cap_each: int, max_segments: int) -> list[str]:
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        out: list[str] = []
        for chunk in chunks:
            if len(chunk) > cap_each:
                out.append(chunk[:cap_each])
            else:
                out.append(chunk)
            if len(out) >= max_segments:
                break
        return out

    def _snippet_score(
        self,
        *,
        snippet: str,
        query_tokens: set[str],
        path: str,
        current_dir: str,
        recency_rank: dict[str, int],
    ) -> float:
        low = snippet.lower()
        overlap = sum(1 for token in query_tokens if token in low)
        if overlap <= 0:
            return 0.0
        score = float(overlap * 3)
        if os.path.dirname(path) == current_dir:
            score += 2.5
        if path in recency_rank:
            score += max(0.0, 1.6 - (recency_rank[path] * 0.1))
        return score

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int((len(text) + 3) // 4))

    def _trim_prompt(self, text: str, max_tokens: int) -> str:
        if self._estimate_tokens(text) <= max_tokens:
            return text
        max_chars = max(256, int(max_tokens) * 4)
        return text[-max_chars:]

    def _rel_path(self, path: str) -> str:
        try:
            return os.path.relpath(path, self.project_root)
        except Exception:
            return path

    def _read_text_capped(self, path: str, cap_chars: int) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read(cap_chars)
            return str(data or "")
        except Exception:
            return ""

    def _canonicalize_path(self, path: str) -> str:
        try:
            return self._canonicalize(path)
        except Exception:
            return str(path or "")

    def _language_for_path(self, file_path: str) -> str:
        suffix = Path(str(file_path or "")).suffix.lower()
        mapping = {
            ".py": "python",
            ".pyw": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".json": "json",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".scss": "scss",
            ".less": "less",
            ".sh": "bash",
            ".zsh": "bash",
            ".ksh": "bash",
            ".bash": "bash",
            ".php": "php",
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".hpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".rs": "rust",
            ".md": "markdown",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".ini": "ini",
            ".qss": "css",
        }
        return mapping.get(suffix, "text")

    def _fence_lang(self, language: str) -> str:
        return language if str(language or "").strip() else "text"
