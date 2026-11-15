"""Migrated jwave_flow agent workflow and its restricted bridge.

Imports stay lazy so the gateway can import the adapter without installing the
workflow's LangChain/LangGraph dependency set.
"""

__all__ = ["Settings", "SimulationWorkflow"]


def __getattr__(name):
    if name == "Settings":
        from .workflow.config import Settings

        return Settings
    if name == "SimulationWorkflow":
        from .workflow.graph import SimulationWorkflow

        return SimulationWorkflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
