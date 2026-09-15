from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


class JsonlRecorder:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: object) -> None:
        payload = json.dumps(event, default=_json_default, separators=(",", ":"), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
