from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal


class ExecutionState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


class DebugLaunchKind(Enum):
    SCRIPT = "script"
    MODULE = "module"


@dataclass(slots=True)
class DebugLaunchRequest:
    file_path: str
    source_text: str
    launch_kind: DebugLaunchKind = DebugLaunchKind.SCRIPT
    module_name: str = ""
    interpreter: str = ""
    working_directory: str = ""
    arguments: tuple[str, ...] = field(default_factory=tuple)
    environment: dict[str, str] = field(default_factory=dict)
    just_my_code: bool = True
    use_source_snapshot: bool = False


class DebuggerBackend(QObject):
    stateChanged = Signal(str)
    stdoutReceived = Signal(str)
    stderrReceived = Signal(str)
    protocolError = Signal(str)
    started = Signal(dict)
    breakpointsSet = Signal(dict)
    paused = Signal(dict)
    watchValuesUpdated = Signal(dict)
    evaluationResult = Signal(dict)
    exceptionRaised = Signal(dict)
    fatalError = Signal(dict)
    processEnded = Signal(dict)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    @abstractmethod
    def state(self) -> ExecutionState:
        raise NotImplementedError

    @abstractmethod
    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints: dict[str, list[dict]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop_debugging(self, clean_only: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def request_stop(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def set_breakpoints(self, breakpoints: dict[str, list[dict]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_command(self, action: str, extra: dict | None = None) -> bool:
        raise NotImplementedError
