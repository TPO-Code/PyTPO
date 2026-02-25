"""Keybinding models, defaults, normalization, and conflict helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from PySide6.QtGui import QKeySequence

KeybindingScope = str


@dataclass(frozen=True, slots=True)
class KeyChord:
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False

    def to_portable_text(self) -> str:
        parts: list[str] = []
        if self.ctrl:
            parts.append("Ctrl")
        if self.alt:
            parts.append("Alt")
        if self.shift:
            parts.append("Shift")
        if self.meta:
            parts.append("Meta")
        parts.append(str(self.key or "").strip())
        return "+".join(part for part in parts if part)

    @staticmethod
    def from_portable_text(chord_text: str) -> "KeyChord | None":
        text = str(chord_text or "").strip()
        if not text:
            return None
        tokens = [tok.strip() for tok in text.split("+") if tok.strip()]
        if not tokens:
            return None
        mods = {tok.lower() for tok in tokens[:-1]}
        key = tokens[-1]
        return KeyChord(
            key=key,
            ctrl=("ctrl" in mods),
            alt=("alt" in mods),
            shift=("shift" in mods),
            meta=("meta" in mods or "cmd" in mods),
        )


@dataclass(frozen=True, slots=True)
class KeySequenceSpec:
    chords: tuple[KeyChord, ...]

    def to_portable_text(self) -> str:
        return ", ".join(chord.to_portable_text() for chord in self.chords)

    @staticmethod
    def from_chord_texts(chords: list[str]) -> "KeySequenceSpec":
        built: list[KeyChord] = []
        for raw in chords:
            chord = KeyChord.from_portable_text(raw)
            if chord is not None:
                built.append(chord)
        return KeySequenceSpec(tuple(built))


@dataclass(frozen=True, slots=True)
class KeybindingAction:
    scope: KeybindingScope
    action_id: str
    action_name: str
    default_sequence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KeybindingConflict:
    scope: KeybindingScope
    action_id: str
    action_name: str
    sequence_text: str


KEYBINDING_ACTIONS: tuple[KeybindingAction, ...] = (
    KeybindingAction("general", "action.new_file", "New File", ("Ctrl+N",)),
    KeybindingAction("general", "action.open_file", "Open File", ("Ctrl+O",)),
    KeybindingAction("general", "action.open_project", "Open Project", ("Ctrl+Shift+O",)),
    KeybindingAction("general", "action.new_project", "New Project", ("Ctrl+Shift+N",)),
    KeybindingAction("general", "action.find_in_files", "Find in Files", ("Ctrl+Shift+F",)),
    KeybindingAction("general", "action.save", "Save", ("Ctrl+S",)),
    KeybindingAction("general", "action.save_as", "Save As", ("Ctrl+Shift+S",)),
    KeybindingAction("general", "action.close_editor", "Close Active Editor", ("Ctrl+W",)),
    KeybindingAction("general", "action.exit", "Exit IDE", ("Ctrl+Q",)),
    KeybindingAction("general", "action.copy", "Copy", ("Ctrl+C",)),
    KeybindingAction("general", "action.paste", "Paste", ("Ctrl+V",)),
    KeybindingAction("general", "action.find", "Find", ("Ctrl+F",)),
    KeybindingAction("general", "action.replace", "Replace", ("Ctrl+H",)),
    KeybindingAction("general", "action.go_to_definition", "Go to Definition", ("F12",)),
    KeybindingAction("general", "action.find_usages", "Find Usages", ("Shift+F12",)),
    KeybindingAction("general", "action.rename_symbol", "Rename Symbol", ("F2",)),
    KeybindingAction("general", "action.extract_variable", "Extract Variable", ("Ctrl+Alt+V",)),
    KeybindingAction("general", "action.extract_method", "Extract Method", ("Ctrl+Alt+M",)),
    KeybindingAction("general", "action.trigger_completion", "Trigger Completion", ("Ctrl+Space",)),
    KeybindingAction("general", "action.ai_inline_assist", "AI Inline Assist", ("Alt+\\",)),
    KeybindingAction(
        "general",
        "action.ai_inline_assist_alt_space",
        "AI Inline Assist (Alt+Space)",
        ("Alt+Space",),
    ),
    KeybindingAction(
        "general",
        "action.ai_inline_assist_ctrl_alt_space",
        "AI Inline Assist (Ctrl+Alt+Space)",
        ("Ctrl+Alt+Space",),
    ),
    KeybindingAction("general", "action.new_terminal", "New Terminal", ("Ctrl+Alt+T",)),
    KeybindingAction("general", "action.build_current_file", "Build Current File", ("Ctrl+Shift+B",)),
    KeybindingAction(
        "general",
        "action.build_and_run_current_file",
        "Build + Run Current File",
        ("Ctrl+Shift+F5",),
    ),
    KeybindingAction("general", "action.run_current_file", "Run Current File", ("F5",)),
    KeybindingAction("general", "action.rerun_current_file", "Rerun Current File", ("Ctrl+F5",)),
    KeybindingAction("general", "action.stop_current_run", "Stop Current Run", ("Shift+F5",)),
    KeybindingAction("general", "action.tree_copy", "Explorer Copy", ("Ctrl+C",)),
    KeybindingAction("general", "action.tree_cut", "Explorer Cut", ("Ctrl+X",)),
    KeybindingAction("general", "action.tree_paste", "Explorer Paste", ("Ctrl+V",)),
    KeybindingAction("general", "action.tree_delete", "Explorer Delete", ("Delete",)),
    KeybindingAction("python", "action.python_comment_toggle", "Toggle Comment", ("Ctrl+/",)),
    KeybindingAction("cpp", "action.cpp_comment_toggle", "Toggle Comment", ("Shift+/",)),
)

_ACTION_BY_SCOPE_ID: dict[tuple[KeybindingScope, str], KeybindingAction] = {
    (entry.scope, entry.action_id): entry for entry in KEYBINDING_ACTIONS
}


def default_keybindings() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {"general": {}, "python": {}, "cpp": {}}
    for action in KEYBINDING_ACTIONS:
        out.setdefault(action.scope, {})[action.action_id] = list(action.default_sequence)
    return out


def keybinding_actions_for_scope(scope: KeybindingScope) -> list[KeybindingAction]:
    target = str(scope or "").strip().lower()
    return [entry for entry in KEYBINDING_ACTIONS if entry.scope == target]


def action_definition(scope: KeybindingScope, action_id: str) -> KeybindingAction | None:
    return _ACTION_BY_SCOPE_ID.get((str(scope or "").strip().lower(), str(action_id or "").strip()))


def _split_sequence_tokens(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _manual_canonical_chord(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts = [part.strip() for part in raw.split("+") if part.strip()]
    if not parts:
        return ""

    has_ctrl = False
    has_alt = False
    has_shift = False
    has_meta = False
    key_token = ""
    for part in parts:
        low = part.lower()
        if low in {"ctrl", "control"}:
            has_ctrl = True
            continue
        if low == "alt":
            has_alt = True
            continue
        if low == "shift":
            has_shift = True
            continue
        if low in {"meta", "cmd", "command", "super", "win"}:
            has_meta = True
            continue
        key_token = part

    if not key_token:
        return ""
    if key_token.lower() in {"slash", "?", "/"}:
        key_token = "/"
    elif len(key_token) == 1 and key_token.isalpha():
        key_token = key_token.upper()

    out: list[str] = []
    if has_ctrl:
        out.append("Ctrl")
    if has_alt:
        out.append("Alt")
    if has_shift:
        out.append("Shift")
    if has_meta:
        out.append("Meta")
    out.append(key_token)
    return "+".join(out)


def _modifiers_from_chord(chord: str) -> set[str]:
    text = str(chord or "").strip()
    if not text:
        return set()
    parts = [part.strip() for part in text.split("+") if part.strip()]
    if len(parts) <= 1:
        return set()
    return {part for part in parts[:-1]}


def canonicalize_chord_text(text: str) -> str:
    chord_text = str(text or "").strip()
    if not chord_text:
        return ""
    manual = _manual_canonical_chord(chord_text)
    sequence = QKeySequence(chord_text)
    normalized = sequence.toString(QKeySequence.PortableText).strip()
    normalized_manual = _manual_canonical_chord(normalized)
    if not normalized:
        return manual or chord_text
    # Keep user-intended modifiers if Qt's parser dropped them for punctuation chords.
    if manual and _modifiers_from_chord(manual) and not _modifiers_from_chord(normalized_manual):
        return manual
    # Keep only the first chord when an entire sequence string is provided.
    text_out = _split_sequence_tokens(normalized)[0] if "," in normalized else normalized
    normalized_text = _manual_canonical_chord(text_out)
    return normalized_text or text_out


def normalize_sequence(value: Any) -> list[str]:
    tokens: list[str] = []
    if isinstance(value, str):
        tokens.extend(_split_sequence_tokens(value))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                tokens.extend(_split_sequence_tokens(item))
    normalized: list[str] = []
    for token in tokens:
        text = canonicalize_chord_text(token)
        if text:
            normalized.append(text)
    return normalized


def sequence_to_text(sequence: list[str] | tuple[str, ...]) -> str:
    normalized = normalize_sequence(list(sequence))
    return ", ".join(normalized)


def normalize_keybindings(raw: Any) -> dict[str, dict[str, list[str]]]:
    merged = default_keybindings()
    if not isinstance(raw, Mapping):
        return merged

    for scope_key, scope_payload in raw.items():
        scope = str(scope_key or "").strip().lower()
        if not scope:
            continue
        if not isinstance(scope_payload, Mapping):
            continue
        scope_map = merged.setdefault(scope, {})
        for action_key, value in scope_payload.items():
            action_id = str(action_key or "").strip()
            if not action_id:
                continue
            normalized = normalize_sequence(value)
            if normalized:
                scope_map[action_id] = normalized
    return merged


def get_action_sequence(
    keybindings: Mapping[str, Mapping[str, list[str]]] | None,
    *,
    scope: KeybindingScope,
    action_id: str,
) -> list[str]:
    normalized = normalize_keybindings(keybindings)
    scope_key = str(scope or "").strip().lower()
    action_key = str(action_id or "").strip()
    from_scope = normalized.get(scope_key, {})
    if action_key in from_scope:
        return normalize_sequence(from_scope.get(action_key))
    spec = action_definition(scope_key, action_key)
    if spec is not None:
        return list(spec.default_sequence)
    return []


def set_action_sequence(
    keybindings: Mapping[str, Mapping[str, list[str]]] | None,
    *,
    scope: KeybindingScope,
    action_id: str,
    sequence: list[str],
) -> dict[str, dict[str, list[str]]]:
    normalized = normalize_keybindings(keybindings)
    scope_key = str(scope or "").strip().lower()
    action_key = str(action_id or "").strip()
    normalized.setdefault(scope_key, {})[action_key] = normalize_sequence(sequence)
    return normalized


def reset_scope_to_defaults(
    keybindings: Mapping[str, Mapping[str, list[str]]] | None,
    *,
    scope: KeybindingScope,
) -> dict[str, dict[str, list[str]]]:
    normalized = normalize_keybindings(keybindings)
    defaults = default_keybindings()
    scope_key = str(scope or "").strip().lower()
    normalized[scope_key] = deepcopy(defaults.get(scope_key, {}))
    return normalized


def reset_action_to_default(
    keybindings: Mapping[str, Mapping[str, list[str]]] | None,
    *,
    scope: KeybindingScope,
    action_id: str,
) -> dict[str, dict[str, list[str]]]:
    normalized = normalize_keybindings(keybindings)
    defaults = default_keybindings()
    scope_key = str(scope or "").strip().lower()
    action_key = str(action_id or "").strip()
    fallback = list(defaults.get(scope_key, {}).get(action_key, []))
    normalized.setdefault(scope_key, {})[action_key] = fallback
    return normalized


def qkeysequence_from_sequence(sequence: list[str] | tuple[str, ...]) -> QKeySequence:
    return QKeySequence(sequence_to_text(list(sequence)))


def sequence_equals(left: list[str], right: list[str]) -> bool:
    return normalize_sequence(left) == normalize_sequence(right)


def find_conflicts_for_sequence(
    keybindings: Mapping[str, Mapping[str, list[str]]] | None,
    *,
    scope: KeybindingScope,
    action_id: str,
    sequence: list[str],
) -> list[KeybindingConflict]:
    normalized = normalize_keybindings(keybindings)
    target_scope = str(scope or "").strip().lower()
    target_action = str(action_id or "").strip()
    target_sequence = normalize_sequence(sequence)
    if not target_sequence:
        return []

    conflicts: list[KeybindingConflict] = []
    for candidate in KEYBINDING_ACTIONS:
        if candidate.scope == target_scope and candidate.action_id == target_action:
            continue
        other_sequence = get_action_sequence(
            normalized,
            scope=candidate.scope,
            action_id=candidate.action_id,
        )
        if not sequence_equals(target_sequence, other_sequence):
            continue
        same_scope = candidate.scope == target_scope
        cross_general_language = {candidate.scope, target_scope} in ({"general", "python"}, {"general", "cpp"})
        if not (same_scope or cross_general_language):
            continue
        conflicts.append(
            KeybindingConflict(
                scope=candidate.scope,
                action_id=candidate.action_id,
                action_name=candidate.action_name,
                sequence_text=sequence_to_text(other_sequence),
            )
        )
    return conflicts


__all__ = [
    "KeybindingScope",
    "KeyChord",
    "KeySequenceSpec",
    "KeybindingAction",
    "KeybindingConflict",
    "KEYBINDING_ACTIONS",
    "default_keybindings",
    "keybinding_actions_for_scope",
    "action_definition",
    "canonicalize_chord_text",
    "normalize_sequence",
    "sequence_to_text",
    "normalize_keybindings",
    "get_action_sequence",
    "set_action_sequence",
    "reset_scope_to_defaults",
    "reset_action_to_default",
    "qkeysequence_from_sequence",
    "sequence_equals",
    "find_conflicts_for_sequence",
]
