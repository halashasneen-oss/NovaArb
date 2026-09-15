from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from novaarb.venue_health import VenueHealthConfig, analyze_venue_health


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value)!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze venue/symbol feed health from a NovaArb research capture."
    )
    parser.add_argument("capture")
    parser.add_argument("--stale-gap-ms", type=int, default=2_000)
    parser.add_argument("--max-p95-feed-delay-ms", type=int, default=1_000)
    parser.add_argument("--min-events", type=int, default=2)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON file for dashboard/monitoring ingestion.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = analyze_venue_health(
        args.capture,
        config=VenueHealthConfig(
            stale_gap_ms=args.stale_gap_ms,
            max_p95_feed_delay_ms=args.max_p95_feed_delay_ms,
            min_events=args.min_events,
        ),
    )
    payload = json.dumps(
        asdict(report),
        default=_default,
        separators=(",", ":"),
        sort_keys=True,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
