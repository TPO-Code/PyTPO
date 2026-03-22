"""Helpers for project-local `.tide/commit.md` persistence and section sync."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pytpo.services.file_io import read_text, write_text

COMMIT_MD_RELATIVE_PATH = Path(".tide") / "commit.md"
REPO_COMMIT_MD_RELATIVE_DIR = Path(".tide") / "repo-commit-drafts"
COMMIT_HEADING = "Commit message"
RELEASE_HEADING = "Release message"
CANONICAL_COMMIT_HEADING_LINE = f"# {COMMIT_HEADING}"
CANONICAL_RELEASE_HEADING_LINE = f"# {RELEASE_HEADING}"
DEFAULT_COMMIT_MD_TEXT = (
    f"{CANONICAL_COMMIT_HEADING_LINE}\n"
    "\n"
    f"{CANONICAL_RELEASE_HEADING_LINE}\n"
)

_TOP_LEVEL_HEADING_RE = re.compile(r"^\s*#(?!#)\s*(?P<title>.*?)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
_NORMALIZED_COMMIT_HEADING = _WHITESPACE_RE.sub(" ", COMMIT_HEADING.lower()).strip()
_NORMALIZED_RELEASE_HEADING = _WHITESPACE_RE.sub(" ", RELEASE_HEADING.lower()).strip()


@dataclass(frozen=True)
class _SectionSpan:
    start: int
    end: int
    heading: str
    heading_key: str


def commit_md_path_for_project(project_root: str | Path) -> Path:
    return Path(str(project_root or "")).expanduser() / COMMIT_MD_RELATIVE_PATH


def commit_md_path_for_scope(
    project_root: str | Path,
    *,
    scope_kind: str = "project",
    repo_root: str | Path | None = None,
) -> Path:
    project_path = _expand_path(project_root)
    repo_path = _expand_path(repo_root) if repo_root is not None else None
    normalized_scope = str(scope_kind or "project").strip().lower() or "project"
    if normalized_scope == "project" or repo_path is None:
        return project_path / COMMIT_MD_RELATIVE_PATH
    return project_path / REPO_COMMIT_MD_RELATIVE_DIR / f"{_repo_scope_slug(project_path, repo_path)}.md"


def ensure_commit_md_exists(project_root: str | Path) -> Path:
    path = commit_md_path_for_project(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_text(str(path), DEFAULT_COMMIT_MD_TEXT)
    return path


def ensure_commit_md_exists_for_scope(
    project_root: str | Path,
    *,
    scope_kind: str = "project",
    repo_root: str | Path | None = None,
) -> Path:
    path = commit_md_path_for_scope(project_root, scope_kind=scope_kind, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_text(str(path), DEFAULT_COMMIT_MD_TEXT)
    return path


def load_commit_md_text(project_root: str | Path) -> str:
    path = ensure_commit_md_exists(project_root)
    try:
        return read_text(str(path))
    except Exception:
        write_text(str(path), DEFAULT_COMMIT_MD_TEXT)
        return DEFAULT_COMMIT_MD_TEXT


def load_commit_md_text_for_scope(
    project_root: str | Path,
    *,
    scope_kind: str = "project",
    repo_root: str | Path | None = None,
) -> str:
    path = ensure_commit_md_exists_for_scope(project_root, scope_kind=scope_kind, repo_root=repo_root)
    try:
        return read_text(str(path))
    except Exception:
        write_text(str(path), DEFAULT_COMMIT_MD_TEXT)
        return DEFAULT_COMMIT_MD_TEXT


def parse_commit_md_sections(text: str) -> dict[str, str]:
    source = str(text or "")
    lines, sections = _parse_sections(source)
    parsed: dict[str, str] = {}
    for section in sections:
        heading = section.heading.strip()
        if not heading or heading in parsed:
            continue
        parsed[heading] = _section_body(lines, section).strip("\r\n")
    return parsed


def get_commit_message_from_commit_md(text: str) -> str | None:
    return _get_managed_section_text(text, _NORMALIZED_COMMIT_HEADING)


def get_release_message_from_commit_md(text: str) -> str | None:
    return _get_managed_section_text(text, _NORMALIZED_RELEASE_HEADING)


def update_commit_md_sections(text: str, commit_message: str, release_message: str) -> str:
    source = str(text or "")
    lines, sections = _parse_sections(source)
    newline = _preferred_newline(source)

    replacements: dict[int, list[str]] = {}
    has_commit = False
    has_release = False

    for index, section in enumerate(sections):
        if section.heading_key == _NORMALIZED_COMMIT_HEADING and not has_commit:
            replacements[index] = _render_managed_section(
                CANONICAL_COMMIT_HEADING_LINE,
                str(commit_message or ""),
                newline=newline,
            )
            has_commit = True
            continue
        if section.heading_key == _NORMALIZED_RELEASE_HEADING and not has_release:
            replacements[index] = _render_managed_section(
                CANONICAL_RELEASE_HEADING_LINE,
                str(release_message or ""),
                newline=newline,
            )
            has_release = True

    rebuilt: list[str] = []
    cursor = 0
    for index, section in enumerate(sections):
        rebuilt.extend(lines[cursor:section.start])
        replacement = replacements.get(index)
        if replacement is None:
            rebuilt.extend(lines[section.start:section.end])
        else:
            rebuilt.extend(replacement)
        cursor = section.end
    rebuilt.extend(lines[cursor:])

    result = "".join(rebuilt)
    result_lines = result.splitlines(keepends=True)
    if not has_commit and not has_release:
        result_lines = (
            _render_managed_section(
                CANONICAL_COMMIT_HEADING_LINE,
                str(commit_message or ""),
                newline=newline,
            )
            + _render_managed_section(
                CANONICAL_RELEASE_HEADING_LINE,
                str(release_message or ""),
                newline=newline,
            )
            + result_lines
        )
        result = "".join(result_lines)
    elif not has_commit:
        result_lines = _render_managed_section(
            CANONICAL_COMMIT_HEADING_LINE,
            str(commit_message or ""),
            newline=newline,
        ) + result_lines
        result = "".join(result_lines)
    elif not has_release:
        lines_after_rewrite, rewritten_sections = _parse_sections(result)
        release_inserted = False
        for section in rewritten_sections:
            if section.heading_key != _NORMALIZED_COMMIT_HEADING:
                continue
            if section.start != 0:
                break
            lines_after_rewrite = (
                lines_after_rewrite[:section.end]
                + _render_managed_section(
                    CANONICAL_RELEASE_HEADING_LINE,
                    str(release_message or ""),
                    newline=newline,
                )
                + lines_after_rewrite[section.end:]
            )
            release_inserted = True
            break
        if not release_inserted:
            lines_after_rewrite = _render_managed_section(
                CANONICAL_RELEASE_HEADING_LINE,
                str(release_message or ""),
                newline=newline,
            ) + lines_after_rewrite
        result = "".join(lines_after_rewrite)

    if not result:
        return DEFAULT_COMMIT_MD_TEXT
    return result


def write_commit_md_for_project(project_root: str | Path, text: str) -> None:
    path = ensure_commit_md_exists(project_root)
    write_text(str(path), str(text or ""))


def write_commit_md_for_scope(
    project_root: str | Path,
    text: str,
    *,
    scope_kind: str = "project",
    repo_root: str | Path | None = None,
) -> None:
    path = ensure_commit_md_exists_for_scope(project_root, scope_kind=scope_kind, repo_root=repo_root)
    write_text(str(path), str(text or ""))


def _parse_sections(text: str) -> tuple[list[str], list[_SectionSpan]]:
    lines = str(text or "").splitlines(keepends=True)
    sections: list[_SectionSpan] = []

    section_start: int | None = None
    section_heading = ""
    section_heading_key = ""
    for index, line in enumerate(lines):
        heading = _parse_top_level_heading(line)
        if heading is None:
            continue
        if section_start is not None:
            sections.append(
                _SectionSpan(
                    start=section_start,
                    end=index,
                    heading=section_heading,
                    heading_key=section_heading_key,
                )
            )
        section_start = index
        section_heading = heading
        section_heading_key = _normalize_heading(heading)

    if section_start is not None:
        sections.append(
            _SectionSpan(
                start=section_start,
                end=len(lines),
                heading=section_heading,
                heading_key=section_heading_key,
            )
        )

    return lines, sections


def _parse_top_level_heading(line: str) -> str | None:
    text = str(line or "").rstrip("\r\n")
    match = _TOP_LEVEL_HEADING_RE.match(text)
    if match is None:
        return None
    return str(match.group("title") or "")


def _normalize_heading(heading: str) -> str:
    compact = _WHITESPACE_RE.sub(" ", str(heading or "").strip())
    return compact.lower()


def _section_body(lines: list[str], section: _SectionSpan) -> str:
    if section.end <= section.start + 1:
        return ""
    return "".join(lines[section.start + 1 : section.end])


def _get_managed_section_text(text: str, heading_key: str) -> str | None:
    lines, sections = _parse_sections(str(text or ""))
    for section in sections:
        if section.heading_key != heading_key:
            continue
        return _section_body(lines, section).strip("\r\n")
    return None


def _preferred_newline(text: str) -> str:
    return "\r\n" if "\r\n" in str(text or "") else "\n"


def _sanitize_managed_body(value: str) -> list[str]:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized:
        return []
    return normalized.split("\n")


def _render_managed_section(heading_line: str, value: str, *, newline: str) -> list[str]:
    out: list[str] = [f"{heading_line}{newline}"]
    body_lines = _sanitize_managed_body(value)
    for line in body_lines:
        out.append(f"{line}{newline}")
    out.append(newline)
    return out


def _expand_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    text = str(path or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _paths_equal(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _repo_scope_slug(project_root: Path, repo_root: Path) -> str:
    project = project_root
    repo = repo_root
    try:
        relative = repo.resolve().relative_to(project.resolve())
    except Exception:
        relative = repo.name or "repo"
    if isinstance(relative, Path):
        rel_text = "/".join(part for part in relative.parts if part not in {"", "."})
    else:
        rel_text = str(relative or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", rel_text).strip("-") or "workspace-root"
    digest = hashlib.sha1(str(repo).encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{safe}--{digest}"


__all__ = [
    "CANONICAL_COMMIT_HEADING_LINE",
    "CANONICAL_RELEASE_HEADING_LINE",
    "DEFAULT_COMMIT_MD_TEXT",
    "commit_md_path_for_project",
    "commit_md_path_for_scope",
    "ensure_commit_md_exists",
    "ensure_commit_md_exists_for_scope",
    "load_commit_md_text",
    "load_commit_md_text_for_scope",
    "parse_commit_md_sections",
    "get_commit_message_from_commit_md",
    "get_release_message_from_commit_md",
    "update_commit_md_sections",
    "write_commit_md_for_project",
    "write_commit_md_for_scope",
]
