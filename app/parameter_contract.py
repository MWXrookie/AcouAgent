"""Shared aliases for canonical simulation parameter names.

The portal accepts both the historical MCP field names and the newer explicit-SI
field names. Normalization is deterministic and never fills missing values.
"""

from __future__ import annotations

from typing import Any, Mapping


ALIASES = {
    "sound_speed_m_s": "sound_speed",
    "density_kg_m3": "density",
    "grid_shape": "domain_N",
    "dx_m": "domain_dx",
    "t_end_s": "t_end",
    "source_type": "source_kind",
    "frequency_hz": "source_frequency",
}


def canonicalize_parameters(parameters: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return explicit-SI/history aliases in one namespace and report conflicts."""
    normalized: dict[str, Any] = {}
    conflicts: list[str] = []
    for key, value in parameters.items():
        target = ALIASES.get(key, key)
        if target in normalized and normalized[target] != value:
            if target not in conflicts:
                conflicts.append(target)
            continue
        normalized[target] = value
    return normalized, conflicts


__all__ = ["ALIASES", "canonicalize_parameters"]
