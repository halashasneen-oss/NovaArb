from __future__ import annotations

import argparse
import json
from pathlib import Path

from novaarb.latency_baseline import build_latency_baseline


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate public capture feed-delay evidence across one or more files. "
            "Use captures recorded on the intended host for a deployment baseline."
        )
    )
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = build_latency_baseline(tuple(args.captures))
    encoded = json.dumps(report.to_payload(), indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
