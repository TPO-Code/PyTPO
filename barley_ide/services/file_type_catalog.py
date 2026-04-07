from __future__ import annotations

from dataclasses import dataclass

from .language_id import extension_language_ids, filename_language_ids


@dataclass(frozen=True, slots=True)
class DesktopAssociationType:
    key: str
    label: str
    extensions: tuple[str, ...]
    mime_types: tuple[str, ...]
    description: str
    custom_mime: bool = False
    filenames: tuple[str, ...] = ()


def _extensions_for_language(language_id: str, *, include: tuple[str, ...] = (), exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    ext_map = extension_language_ids()
    selected = {
        ext
        for ext, lang in ext_map.items()
        if str(lang or "").strip().lower() == str(language_id or "").strip().lower()
    }
    selected.update(str(ext or "").strip().lower() for ext in include if str(ext or "").strip())
    selected.difference_update(str(ext or "").strip().lower() for ext in exclude if str(ext or "").strip())
    return tuple(sorted(selected))


def _filenames_for_language(language_id: str) -> tuple[str, ...]:
    name_map = filename_language_ids()
    selected = [
        name
        for name, lang in name_map.items()
        if str(lang or "").strip().lower() == str(language_id or "").strip().lower()
    ]
    return tuple(sorted(selected))


def desktop_association_types() -> tuple[DesktopAssociationType, ...]:
    return (
        DesktopAssociationType(
            "txt",
            "Plain Text",
            _extensions_for_language("plaintext", include=(".txt",), exclude=(".md",)),
            ("text/plain",),
            "Plain text documents.",
        ),
        DesktopAssociationType(
            "md",
            "Markdown",
            _extensions_for_language("markdown"),
            ("text/markdown", "text/x-markdown"),
            "Markdown notes and docs.",
        ),
        DesktopAssociationType(
            "py",
            "Python",
            _extensions_for_language("python"),
            ("text/x-python", "application/x-python-code"),
            "Python source files.",
        ),
        DesktopAssociationType(
            "c",
            "C",
            (".c",),
            ("text/x-csrc",),
            "C source files.",
        ),
        DesktopAssociationType(
            "cpp",
            "C++",
            (".cc", ".cpp", ".cxx"),
            ("text/x-c++src",),
            "C++ source files.",
        ),
        DesktopAssociationType(
            "h",
            "C Header",
            (".h",),
            ("text/x-chdr",),
            "C header files.",
        ),
        DesktopAssociationType(
            "hpp",
            "C++ Header",
            (".hh", ".hpp", ".hxx"),
            ("text/x-c++hdr",),
            "C++ header files.",
        ),
        DesktopAssociationType(
            "rs",
            "Rust",
            _extensions_for_language("rust"),
            ("text/rust", "text/x-rust"),
            "Rust source files.",
        ),
        DesktopAssociationType(
            "javascript",
            "JavaScript",
            _extensions_for_language("javascript"),
            ("application/javascript", "text/javascript"),
            "JavaScript source files.",
        ),
        DesktopAssociationType(
            "jsx",
            "JSX",
            _extensions_for_language("javascriptreact"),
            ("text/jsx", "application/javascript"),
            "React JSX source files.",
        ),
        DesktopAssociationType(
            "typescript",
            "TypeScript",
            _extensions_for_language("typescript"),
            ("application/typescript", "text/typescript"),
            "TypeScript source files.",
        ),
        DesktopAssociationType(
            "tsx",
            "TSX",
            _extensions_for_language("typescriptreact"),
            ("text/tsx", "application/typescript"),
            "React TSX source files.",
        ),
        DesktopAssociationType(
            "json",
            "JSON",
            _extensions_for_language("json"),
            ("application/json", "text/x-json"),
            "JSON and GeoJSON data files.",
        ),
        DesktopAssociationType(
            "jsonc",
            "JSONC",
            _extensions_for_language("jsonc"),
            ("application/json", "application/x-json"),
            "JSON with comments.",
        ),
        DesktopAssociationType(
            "html",
            "HTML",
            _extensions_for_language("html"),
            ("text/html", "application/xhtml+xml"),
            "HTML and XHTML files.",
        ),
        DesktopAssociationType(
            "xml",
            "XML",
            _extensions_for_language("xml"),
            ("application/xml", "text/xml", "image/svg+xml"),
            "XML and SVG files.",
        ),
        DesktopAssociationType(
            "css",
            "CSS",
            _extensions_for_language("css"),
            ("text/css",),
            "CSS and QSS stylesheets.",
        ),
        DesktopAssociationType(
            "scss",
            "SCSS",
            _extensions_for_language("scss"),
            ("text/x-scss", "text/css"),
            "SCSS stylesheets.",
        ),
        DesktopAssociationType(
            "less",
            "Less",
            _extensions_for_language("less"),
            ("text/x-less", "text/css"),
            "Less stylesheets.",
        ),
        DesktopAssociationType(
            "toml",
            "TOML",
            _extensions_for_language("toml"),
            ("application/toml", "text/plain"),
            "TOML and QSST theme files.",
        ),
        DesktopAssociationType(
            "php",
            "PHP",
            _extensions_for_language("php"),
            ("application/x-php", "text/x-php"),
            "PHP source files.",
        ),
        DesktopAssociationType(
            "shell",
            "Shell",
            _extensions_for_language("shell"),
            ("application/x-shellscript", "text/x-shellscript"),
            "Shell scripts and shell config files.",
            filenames=_filenames_for_language("shell"),
        ),
        DesktopAssociationType(
            "make",
            "Makefile",
            (),
            ("text/x-makefile", "text/plain"),
            "Makefile build scripts.",
            filenames=_filenames_for_language("make"),
        ),
        DesktopAssociationType(
            "todo",
            "Todo Files",
            (".todo",),
            ("application/x-pytpo-todo",),
            "Barley todo lists.",
            custom_mime=True,
        ),
        DesktopAssociationType(
            "task",
            "Task Files",
            (".task",),
            ("application/x-pytpo-task",),
            "Barley task lists.",
            custom_mime=True,
        ),
        DesktopAssociationType(
            "lst",
            "List Files",
            (".lst",),
            ("application/x-pytpo-list",),
            "Barley checklist files.",
            custom_mime=True,
        ),
        DesktopAssociationType(
            "tdoc",
            "TDOC Documents",
            _extensions_for_language("tdoc"),
            ("application/x-pytpo-tdoc",),
            "Barley linked documentation files.",
            custom_mime=True,
        ),
        DesktopAssociationType(
            "tdocproject",
            "TDOC Projects",
            (),
            ("application/x-pytpo-tdocproject",),
            "Barley linked documentation project files.",
            custom_mime=True,
            filenames=_filenames_for_language("tdocproject"),
        ),
    )
