"""Backward-compatible import for the research recorder."""

from novaarb.research import ResearchRecorder

JsonlRecorder = ResearchRecorder

__all__ = ["JsonlRecorder", "ResearchRecorder"]
