"""Explicit configuration for the migrated jwave_flow workflow.

Unlike the source project, this copy has no ``.env`` loader and no environment
variable model-selection path. The bridge constructs ``Settings`` once from its
dedicated deployment environment and the request cannot mutate it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KB = PACKAGE_ROOT / "kb" / "jwave_kb_organized.md"


@dataclass
class Settings:
    """Runtime knobs used only by the agent workflow."""

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    analyst_model: str = "deepseek-flash"
    coder_model: str = "deepseek-flash"
    temperature: float = 0.7
    request_timeout: float = 120.0
    max_retries: int = 2

    kb_path: Path = field(default_factory=lambda: DEFAULT_KB)
    retrieval_backend: str = "bm25"
    top_k: int = 4
    embedding_model: str = "text-embedding-3-small"

    exec_timeout: float = 30.0
    workdir: Path = field(default_factory=lambda: PACKAGE_ROOT / "run")

    param_check_enabled: bool = True
    min_points_per_wavelength: float = 6.0
    param_check_max_rounds: int = 2
    default_cfl: float = 0.3

    static_check_enabled: bool = True

    semantic_check_enabled: bool = True
    require_selfcheck: bool = True
    min_abs_max: float = 0.0
    max_abs_max: float = 1e12

    completion_max_rounds: int = 3
    max_iterations: int = 10

    skill_mode: str = "full"
    skill_port_url: str = ""
    skill_port_token: str = ""
    skill_timeout: float = 2.0
    verbose: bool = False

    def resolved_kb_path(self) -> Path:
        return self.kb_path if self.kb_path.is_absolute() else PACKAGE_ROOT / self.kb_path


__all__ = ["DEFAULT_KB", "PACKAGE_ROOT", "Settings"]
