from dataclasses import dataclass, field
from abc import abstractmethod
from enum import Enum

from PySide6.QtCore import QObject, Signal


class ExecutionState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


@dataclass(slots=True)
class DebugLaunchRequest:
    file_path: str = ""
    source_text: str = ""
    working_directory: str = ""
    arguments: tuple[str, ...] = field(default_factory=tuple)
    environment: dict[str, str] = field(default_factory=dict)
    use_source_snapshot: bool = True


class DebuggerBackend(QObject):
    stateChanged = Signal(str)
    stdoutReceived = Signal(str)
    stderrReceived = Signal(str)
    protocolError = Signal(str)
    started = Signal(dict)
    breakpointsSet = Signal(dict)
    paused = Signal(dict)
    exceptionRaised = Signal(dict)
    fatalError = Signal(dict)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    @abstractmethod
    def state(self):
        raise NotImplementedError

    @abstractmethod
    def start_debugging(self, launch_request: DebugLaunchRequest, breakpoints):
        raise NotImplementedError

    @abstractmethod
    def stop_debugging(self, clean_only=False):
        raise NotImplementedError

    @abstractmethod
    def set_breakpoints(self, lines):
        raise NotImplementedError

    @abstractmethod
    def send_command(self, action, extra=None):
        raise NotImplementedError
