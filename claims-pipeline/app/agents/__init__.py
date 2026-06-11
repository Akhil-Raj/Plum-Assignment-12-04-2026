"""Agent container. Stages receive their agents through this set, so tests swap in
fakes without touching pipeline wiring. Fields are added as agents are built."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentSet:
    classifier: Any = None
    reader: Any = None
    consistency: Any = None
