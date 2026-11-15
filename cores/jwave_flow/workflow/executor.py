"""Executor contract for the migrated workflow.

The migrated core intentionally contains no subprocess, Jupyter or MCP executor.
It must be supplied the project-owned sandbox adapter by the bridge.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .config import Settings


@dataclass
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    results: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    execution_time: float = 0.0
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.stderr.strip() == ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "results": self.results,
            "outputs": self.outputs,
            "new_files": self.new_files,
            "execution_time": round(self.execution_time, 3),
            "backend": self.backend,
        }


class BaseExecutor(ABC):
    name = "base"

    @abstractmethod
    def run(self, code: str) -> ExecutionResult:
        raise NotImplementedError

    def close(self) -> None:
        pass


def get_executor(settings: Settings) -> BaseExecutor:
    del settings
    raise RuntimeError("jwave_flow migrated core requires an injected project executor")


__all__ = ["BaseExecutor", "ExecutionResult", "get_executor"]
