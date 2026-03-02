"""IDE-side CodeEditor extension point.

The reusable base implementation remains in ``TPOPyside.widgets.code_editor``.
This module defines the IDE-owned subclass used by ``src`` so IDE-specific
behavior can be added without coupling ``TPOPyside`` to the IDE.
"""

import re

from TPOPyside.widgets.code_editor import (
    CodeEditor as BaseCodeEditor,
    _extract_compact_signature,
    _normalize_signature_text,
)


class CodeEditor(BaseCodeEditor):
    """IDE-local CodeEditor subclass.

    Starts as a behavior-preserving pass-through; extend here for IDE-only
    editor features.
    """

    def _resolve_completion_insert_text(
        self,
        *,
        item: dict,
        insert_text: str,
        label: str,
        prefix_text: str,
        source_text: str,
    ) -> str:
        value = str(insert_text or "")
        if not value:
            return value

        # IDE behavior: some providers (notably Jedi) return only suffix text
        # in insert_text. Expand to the full label when suffix semantics match.
        if label and prefix_text:
            pfx_low = prefix_text.lower()
            lbl_low = label.lower()
            if lbl_low.startswith(pfx_low):
                suffix = label[len(prefix_text):]
                if value.lower() == suffix.lower():
                    return label
        return value

    def _completion_matches_prefix(self, item: dict, prefix: str) -> bool:
        if bool(item.get("is_ai_suggestion")):
            return True
        label = str(item.get("label") or item.get("insert_text") or "")
        if not prefix:
            return True
        low = label.lower()
        p = prefix.lower()
        return low.startswith(p) or p in low

    def _completion_ui_sort_key(
        self,
        item: dict,
        prefix: str,
        base_index: int,
    ) -> tuple[int, int]:
        if bool(item.get("is_ai_suggestion")):
            return -1, base_index
        label = str(item.get("label") or item.get("insert_text") or "")
        demote = 0
        if label.startswith("__") and label.endswith("__") and not prefix.startswith("_"):
            demote = 2
        elif label.startswith("_") and prefix == "":
            demote = 1
        return demote, base_index

    def _completion_right_label_for_item(self, item: dict) -> str:
        for key in ("source_label", "type_label", "owner", "module"):
            value = str(item.get(key) or "").strip()
            if value:
                return value[:28]

        detail = str(item.get("detail") or "").strip()
        source = str(item.get("source") or "").strip()
        scope = str(item.get("source_scope") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()

        module_m = re.search(r"\bmodule\s+([A-Za-z_][\w\.]*)", detail)
        if module_m:
            return module_m.group(1)[:28]
        class_m = re.search(r"\bclass\s+([A-Za-z_]\w*)", detail)
        if class_m:
            return class_m.group(1)[:28]
        from_m = re.search(r"\bfrom\s+([A-Za-z_][\w\.]*)", detail)
        if from_m:
            return from_m.group(1)[:28]
        arrow_m = re.search(r"->\s*([A-Za-z_][\w\.\[\], ]*)$", detail)
        if arrow_m:
            return arrow_m.group(1).strip()[:28]

        if scope == "builtins":
            return "builtins"
        if scope == "interpreter_modules":
            return "stdlib"
        if scope == "project":
            return "project"
        if scope == "current_file":
            return "file"
        if source and source.lower() not in {"fallback", "jedi"}:
            return source[:28]
        if source:
            return source[:28]
        if kind == "keyword":
            return "keyword"
        return ""

    def _completion_primary_text_for_item(self, item: dict, *, label: str, detail: str) -> str:
        primary = str(label or "")
        if bool(self._completion_ui_cfg.get("show_signatures", True)) and self._is_callable_item(
            item
        ):
            sig = _extract_compact_signature(primary, detail)
            primary = sig if sig else self._best_effort_callable_signature(item, primary)
            primary = _normalize_signature_text(primary, label) or primary
        return primary or str(label or "")

    def _normalize_lint_diagnostic_item(self, item: object) -> dict | None:
        if not isinstance(item, dict):
            return None
        try:
            line = int(item.get("line") or 0)
            col = int(item.get("column") or 1)
        except Exception:
            return None
        if line <= 0:
            return None
        sev = str(item.get("severity") or "warning").strip().lower()
        try:
            end_line = int(item.get("end_line") or line)
        except Exception:
            end_line = line
        try:
            end_col = int(item.get("end_column") or item.get("end_col") or (col + 1))
        except Exception:
            end_col = col + 1
        return {
            "line": max(1, line),
            "column": max(1, col),
            "end_line": max(1, end_line),
            "end_column": max(1, end_col),
            "severity": sev,
        }

    def _signature_owner_from_lookup_payload(self, payload: dict, label: str) -> str:
        full_name = str(payload.get("full_name") or "").strip()
        if full_name:
            return full_name
        module_name = str(payload.get("module_name") or "").strip()
        if module_name:
            return f"{module_name}.{label}"
        return str(payload.get("source") or "")
