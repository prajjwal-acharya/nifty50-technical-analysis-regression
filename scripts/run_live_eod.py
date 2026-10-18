"""Run one idempotent private-store NSE EOD update."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--calendar", type=Path, default=ROOT / "config/nse_equity_calendar_2026.json")
    parser.add_argument("--source-dir", type=Path,
                        help="Read previously downloaded official report pairs from this directory")
    args = parser.parse_args()
    print(json.dumps(run(args.state_dir, args.calendar, source_dir=args.source_dir), indent=2))


if __name__ == "__main__":
    main()
