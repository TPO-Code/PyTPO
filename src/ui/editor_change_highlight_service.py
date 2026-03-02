"""Shared dirty/uncommitted in-editor change region highlighting."""

from __future__ import annotations

import os
import weakref
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor

from TPOPyside.widgets.editor_change_regions import (
    DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
    DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
    parse_editor_overlay_color,
)


@dataclass(slots=True)
class _TrackedWidget:
    ref: weakref.ReferenceType[object]
    timer: QTimer


@dataclass(slots=True)
class _UncommittedCacheEntry:
    disk_sig: tuple[bool, int, int]
    git_generation: int
    lines: set[int]


class EditorChangeHighlightService(QObject):
    """Computes and applies editor line overlays for dirty/uncommitted changes."""

    def __init__(
        self,
        *,
        ide: object,
        git_service: object,
        canonicalize: Callable[[str], str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ide = ide
        self._git_service = git_service
        self._canonicalize = canonicalize

        self._tracked: dict[int, _TrackedWidget] = {}
        self._debounce_ms = 140

        self._git_file_states: dict[str, str] = {}
        self._git_generation = 0
        self._tracked_cache: dict[tuple[str, str], bool] = {}
        self._head_text_cache: dict[tuple[str, str], str | None] = {}
        self._uncommitted_cache: dict[str, _UncommittedCacheEntry] = {}

        self._dirty_color = parse_editor_overlay_color(
            DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
            DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
        )
        self._uncommitted_color = parse_editor_overlay_color(
            DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
            DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
        )

    @property
    def dirty_color(self) -> QColor:
        return QColor(self._dirty_color)

    @property
    def uncommitted_color(self) -> QColor:
        return QColor(self._uncommitted_color)

    def set_overlay_colors(self, *, dirty_background: object, uncommitted_background: object) -> None:
        dirty = parse_editor_overlay_color(dirty_background, DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX)
        uncommitted = parse_editor_overlay_color(
            uncommitted_background,
            DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
        )
        if dirty == self._dirty_color and uncommitted == self._uncommitted_color:
            return
        self._dirty_color = dirty
        self._uncommitted_color = uncommitted
        self.refresh_all(delay_ms=0)

    def track_widget(self, widget: object) -> None:
        if not self._supports_widget(widget):
            return

        key = int(id(widget))
        tracked = self._tracked.get(key)
        if tracked is not None:
            ref_obj = tracked.ref()
            if ref_obj is widget:
                self.refresh_widget(widget, delay_ms=0)
                return
            self._drop_tracked_widget(key)

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda widget_key=key: self._refresh_widget_by_key(widget_key))
        ref = weakref.ref(widget)
        self._tracked[key] = _TrackedWidget(ref=ref, timer=timer)

        text_changed = getattr(widget, "textChanged", None)
        if text_changed is not None and hasattr(text_changed, "connect"):
            text_changed.connect(lambda *_args, wref=ref: self._schedule_from_ref(wref))

        doc = self._widget_document(widget)
        if doc is not None:
            mod_changed = getattr(doc, "modificationChanged", None)
            if mod_changed is not None and hasattr(mod_changed, "connect"):
                mod_changed.connect(lambda *_args, wref=ref: self._schedule_from_ref(wref))

        destroyed = getattr(widget, "destroyed", None)
        if destroyed is not None and hasattr(destroyed, "connect"):
            destroyed.connect(lambda *_args, widget_key=key: self._drop_tracked_widget(widget_key))

        self.refresh_widget(widget, delay_ms=0)

    def refresh_widget(self, widget: object, *, delay_ms: int = 0) -> None:
        key = int(id(widget))
        tracked = self._tracked.get(key)
        if tracked is None:
            self.track_widget(widget)
            tracked = self._tracked.get(key)
        if tracked is None:
            return
        tracked.timer.start(max(0, int(delay_ms)))

    def refresh_all(self, *, delay_ms: int = 0) -> None:
        for key, tracked in list(self._tracked.items()):
            if tracked.ref() is None:
                self._drop_tracked_widget(key)
                continue
            tracked.timer.start(max(0, int(delay_ms)))

    def notify_file_saved(self, file_path: str) -> None:
        target = self._canonical_path(file_path)
        if not target:
            return
        self._uncommitted_cache.pop(target, None)
        self.refresh_for_path(target, delay_ms=0)

    def notify_file_reloaded(self, file_path: str) -> None:
        self.notify_file_saved(file_path)

    def notify_file_path_changed(self, old_path: str | None, new_path: str | None) -> None:
        old_c = self._canonical_path(old_path) if old_path else ""
        new_c = self._canonical_path(new_path) if new_path else ""
        if old_c:
            self._uncommitted_cache.pop(old_c, None)
        if new_c:
            self._uncommitted_cache.pop(new_c, None)
            self.refresh_for_path(new_c, delay_ms=0)

    def on_git_status_changed(self, file_states: dict[str, str] | None) -> None:
        normalized: dict[str, str] = {}
        for raw_path, raw_state in (file_states or {}).items():
            cpath = self._canonical_path(raw_path)
            if not cpath:
                continue
            state = str(raw_state or "").strip().lower()
            if state not in {"clean", "dirty", "untracked"}:
                continue
            normalized[cpath] = state
        self._git_file_states = normalized
        self._git_generation += 1
        self._tracked_cache.clear()
        self._head_text_cache.clear()
        self._uncommitted_cache.clear()
        self.refresh_all(delay_ms=0)

    def _supports_widget(self, widget: object) -> bool:
        if widget is None:
            return False
        setter = getattr(widget, "set_change_region_highlights", None)
        if not callable(setter):
            return False
        return callable(getattr(widget, "document", None)) and (
            callable(getattr(widget, "toPlainText", None))
            or callable(getattr(widget, "serialized_text", None))
        )

    def _widget_document(self, widget: object):
        getter = getattr(widget, "document", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _schedule_from_ref(self, wref: weakref.ReferenceType[object]) -> None:
        widget = wref()
        if widget is None:
            return
        self.refresh_widget(widget, delay_ms=self._debounce_ms)

    def _drop_tracked_widget(self, key: int) -> None:
        tracked = self._tracked.pop(int(key), None)
        if tracked is None:
            return
        try:
            tracked.timer.stop()
        except Exception:
            pass
        try:
            tracked.timer.deleteLater()
        except Exception:
            pass

    def _refresh_widget_by_key(self, key: int) -> None:
        tracked = self._tracked.get(int(key))
        if tracked is None:
            return
        widget = tracked.ref()
        if widget is None:
            self._drop_tracked_widget(key)
            return
        self._refresh_widget(widget)

    def _refresh_widget(self, widget: object) -> None:
        setter = getattr(widget, "set_change_region_highlights", None)
        if not callable(setter):
            return

        file_path = self._widget_file_path(widget)
        if not file_path:
            setter(
                dirty_lines=set(),
                uncommitted_lines=set(),
                dirty_background=self._dirty_color,
                uncommitted_background=self._uncommitted_color,
            )
            return

        live_text = self._widget_text_for_diff(widget)
        disk_text = self._read_disk_text(file_path)

        dirty_lines = self._changed_target_lines(source_text=disk_text, target_text=live_text)

        uncommitted_disk_lines = self._cached_uncommitted_disk_lines(file_path=file_path, disk_text=disk_text)
        uncommitted_live_lines = self._map_source_lines_to_target(
            source_text=disk_text,
            target_text=live_text,
            source_lines=uncommitted_disk_lines,
        )

        setter(
            dirty_lines=dirty_lines,
            uncommitted_lines=uncommitted_live_lines,
            dirty_background=self._dirty_color,
            uncommitted_background=self._uncommitted_color,
        )

    def refresh_for_path(self, file_path: str, *, delay_ms: int = 0) -> None:
        target = self._canonical_path(file_path)
        if not target:
            return
        for tracked in list(self._tracked.values()):
            widget = tracked.ref()
            if widget is None:
                continue
            path = self._widget_file_path(widget)
            if not path or path != target:
                continue
            tracked.timer.start(max(0, int(delay_ms)))

    def _cached_uncommitted_disk_lines(self, *, file_path: str, disk_text: str) -> set[int]:
        cpath = self._canonical_path(file_path)
        if not cpath:
            return set()

        sig = self._disk_signature(cpath)
        cached = self._uncommitted_cache.get(cpath)
        if (
            cached is not None
            and cached.disk_sig == sig
            and cached.git_generation == self._git_generation
        ):
            return set(cached.lines)

        lines = self._compute_uncommitted_disk_lines(file_path=cpath, disk_text=disk_text)
        self._uncommitted_cache[cpath] = _UncommittedCacheEntry(
            disk_sig=sig,
            git_generation=self._git_generation,
            lines=set(lines),
        )
        return lines

    def _compute_uncommitted_disk_lines(self, *, file_path: str, disk_text: str) -> set[int]:
        state = str(self._git_file_states.get(file_path, "") or "").strip().lower()
        if state == "clean":
            return set()
        if state == "untracked":
            return self._all_line_numbers(disk_text)

        repo_root = self._repo_root_for_path(file_path)
        if not repo_root:
            return set()

        rel_path = self._repo_rel_path(repo_root=repo_root, file_path=file_path)
        if not rel_path:
            return set()

        key = (repo_root, rel_path)
        tracked = self._tracked_cache.get(key)
        if tracked is None:
            tracked = self._is_tracked(repo_root=repo_root, rel_path=rel_path)
            self._tracked_cache[key] = bool(tracked)

        if not tracked:
            return self._all_line_numbers(disk_text)

        head_text = self._head_text_cache.get(key)
        if key not in self._head_text_cache:
            head_text = self._read_head_text(repo_root=repo_root, rel_path=rel_path)
            self._head_text_cache[key] = head_text

        if head_text is None:
            return self._all_line_numbers(disk_text)

        return self._changed_target_lines(source_text=head_text, target_text=disk_text)

    def _read_head_text(self, *, repo_root: str, rel_path: str) -> str | None:
        reader = getattr(self._git_service, "read_head_file_text", None)
        if not callable(reader):
            return None
        try:
            text = reader(repo_root, rel_path)
        except Exception:
            return None
        if text is None:
            return None
        return str(text)

    def _is_tracked(self, *, repo_root: str, rel_path: str) -> bool:
        checker = getattr(self._git_service, "is_tracked_path", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(repo_root, rel_path))
        except Exception:
            return False

    def _repo_root_for_path(self, file_path: str) -> str | None:
        getter = getattr(self._ide, "_repo_root_for_path", None)
        if callable(getter):
            try:
                root = getter(file_path)
            except Exception:
                root = None
            if isinstance(root, str) and root.strip():
                return self._canonical_path(root)
        return None

    def _repo_rel_path(self, *, repo_root: str, file_path: str) -> str:
        try:
            rel = os.path.relpath(file_path, repo_root)
        except Exception:
            return ""
        clean = str(rel or "").strip()
        if not clean or clean.startswith(".."):
            return ""
        return clean.replace("\\", "/")

    def _widget_file_path(self, widget: object) -> str:
        try:
            raw = str(getattr(widget, "file_path", "") or "").strip()
        except Exception:
            raw = ""
        if not raw:
            return ""
        return self._canonical_path(raw)

    def _widget_text_for_diff(self, widget: object) -> str:
        serializer = getattr(widget, "serialized_text", None)
        if callable(serializer):
            try:
                return str(serializer())
            except Exception:
                pass
        to_plain = getattr(widget, "toPlainText", None)
        if callable(to_plain):
            try:
                return str(to_plain())
            except Exception:
                return ""
        return ""

    @staticmethod
    def _read_disk_text(file_path: str) -> str:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception:
            try:
                return Path(file_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""

    @staticmethod
    def _disk_signature(file_path: str) -> tuple[bool, int, int]:
        try:
            stat = os.stat(file_path)
        except FileNotFoundError:
            return (False, 0, 0)
        except Exception:
            return (False, 0, 0)
        return (True, int(stat.st_mtime_ns), int(stat.st_size))

    def _canonical_path(self, path: object) -> str:
        text = str(path or "").strip()
        if not text:
            return ""
        try:
            return self._canonicalize(text)
        except Exception:
            try:
                return str(Path(text).resolve())
            except Exception:
                return os.path.abspath(text)

    @staticmethod
    def _normalized_lines(text: str) -> list[str]:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        return normalized.split("\n")

    @classmethod
    def _all_line_numbers(cls, text: str) -> set[int]:
        if str(text or "") == "":
            return set()
        lines = cls._normalized_lines(text)
        return {idx + 1 for idx in range(len(lines))}

    @classmethod
    def _changed_target_lines(cls, *, source_text: str, target_text: str) -> set[int]:
        source_lines = cls._normalized_lines(source_text)
        target_lines = cls._normalized_lines(target_text)
        matcher = SequenceMatcher(a=source_lines, b=target_lines, autojunk=False)

        changed: set[int] = set()
        target_count = len(target_lines)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag in {"replace", "insert"}:
                for line_zero in range(j1, j2):
                    changed.add(int(line_zero) + 1)
                continue
            if tag == "delete" and target_count > 0:
                anchor = max(0, min(j1, target_count - 1))
                changed.add(int(anchor) + 1)
        return changed

    @classmethod
    def _map_source_lines_to_target(
        cls,
        *,
        source_text: str,
        target_text: str,
        source_lines: set[int],
    ) -> set[int]:
        if not source_lines:
            return set()

        src_lines = cls._normalized_lines(source_text)
        tgt_lines = cls._normalized_lines(target_text)
        target_count = len(tgt_lines)
        valid_source_lines = {
            int(line)
            for line in source_lines
            if 1 <= int(line) <= len(src_lines)
        }
        if not valid_source_lines:
            return set()

        matcher = SequenceMatcher(a=src_lines, b=tgt_lines, autojunk=False)
        out: set[int] = set()

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert" or i2 <= i1:
                continue

            if tag == "equal":
                for src_zero in range(i1, i2):
                    src_line = src_zero + 1
                    if src_line not in valid_source_lines:
                        continue
                    out.add((j1 + (src_zero - i1)) + 1)
                continue

            if tag == "replace":
                if j2 > j1:
                    for tgt_zero in range(j1, j2):
                        out.add(tgt_zero + 1)
                elif target_count > 0:
                    out.add(min(target_count, j1 + 1))
                continue

            if tag == "delete" and target_count > 0:
                out.add(min(target_count, j1 + 1))

        return {line for line in out if line > 0}
