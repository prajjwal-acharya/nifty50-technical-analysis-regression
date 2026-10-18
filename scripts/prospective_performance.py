"""Audit committed prospective forecasts against verified target closes."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path

import pandas as pd

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import Calendar, IST, path_for, read_sessions, validate_prospective_issue
from scripts.verify_live_store import verify


def committed_issue(state: Path, path: Path, forecast: dict, calendar: Calendar) -> str:
    """Require the exact saved forecast bytes in a timely Git addition commit."""
    relative = path.relative_to(state).as_posix()
    result = subprocess.run(
        ["git", "-C", str(state), "log", "--diff-filter=A", "-1",
         "--format=%H%x09%cI", "--", relative],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not result:
        raise ValueError(f"Forecast has no Git addition commit: {relative}")
    commit, committed_at_text = result.split("\t", 1)
    committed_at = datetime.fromisoformat(committed_at_text)
    issued_at = datetime.fromisoformat(forecast["issued_at_utc"])
    origin = validate_prospective_issue(forecast, calendar)
    next_open = datetime.combine(calendar.forward(origin, 1), time(9), IST)
    if not issued_at <= committed_at < next_open:
        raise ValueError(f"Forecast was not committed inside its prospective window: {relative}")
    saved = subprocess.run(
        ["git", "-C", str(state), "show", f"{commit}:{relative}"],
        check=True, capture_output=True,
    ).stdout
    if saved != path.read_bytes():
        raise ValueError(f"Forecast bytes differ from their original Git addition: {relative}")
    return commit


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"status": "awaiting_verified_targets", "forecast_rows": 0,
                "origin_sessions": 0, "target_sessions": 0,
                "equal_stock_mae_inr": None, "unchanged_close_mae_inr": None,
                "delta_vs_unchanged_inr": None}
    by_stock: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_stock[row["symbol"]].append(row)
    model = sum(sum(row["absolute_error"] for row in stock_rows) / len(stock_rows)
                for stock_rows in by_stock.values()) / len(by_stock)
    baseline = sum(sum(row["unchanged_close_error"] for row in stock_rows) / len(stock_rows)
                   for stock_rows in by_stock.values()) / len(by_stock)
    return {"status": "measured", "forecast_rows": len(rows),
            "stocks": len(by_stock),
            "origin_sessions": len({row["origin_session"] for row in rows}),
            "target_sessions": len({row["target_session"] for row in rows}),
            "equal_stock_mae_inr": round(model, 4),
            "unchanged_close_mae_inr": round(baseline, 4),
            "delta_vs_unchanged_inr": round(model - baseline, 4)}


def build_report(state: Path, calendar_path: Path) -> dict:
    state = state.resolve()
    manifest = verify(state)
    calendar = Calendar(calendar_path)
    universe = json.loads((state / "baseline/config/nifty50_universe.json").read_text())
    symbols = set(universe["symbols"])
    baseline = pd.read_csv(state / "baseline/daily_prices.csv.gz", parse_dates=["date"], low_memory=False)
    prices, stored_days = read_sessions(state, baseline, symbols, calendar)
    closes = prices.set_index([prices.date.dt.date, "symbol"]).raw_close
    rows: dict[int, list[dict]] = {1: [], 5: []}
    forecast_count = 0
    for path in sorted((state / "forecasts").glob("*/*/*.json")):
        forecast = json.loads(path.read_text())
        origin = validate_prospective_issue(forecast, calendar)
        if (path != path_for(state, "forecasts", origin)
                or forecast["model_sha256"] != manifest["one_day_model_sha256"]
                or forecast["calendar_sha256"] != calendar.sha256):
            raise ValueError(f"Forecast path, model, or calendar mismatch: {path}")
        committed_issue(state, path, forecast, calendar)
        keyed = {(entry["symbol"], entry["horizon"]): entry for entry in forecast["forecasts"]}
        if (len(forecast["forecasts"]) != len(symbols) * 2
                or len(keyed) != len(forecast["forecasts"])
                or set(keyed) != {(symbol, horizon) for symbol in symbols for horizon in (1, 5)}):
            raise ValueError(f"Forecast basket or horizon coverage mismatch: {path}")
        forecast_count += 1
        for horizon in (1, 5):
            target = calendar.forward(origin, horizon)
            if target not in stored_days:
                continue
            issued = {entry["symbol"]: entry for entry in forecast["forecasts"]
                      if entry["horizon"] == horizon and entry["status"] == "issued"}
            if not issued:
                continue
            outcome_path = path_for(state, "outcomes", origin, f"-h{horizon}.json")
            if not outcome_path.exists():
                raise ValueError(f"Verified target has no outcome: {outcome_path}")
            outcome = json.loads(outcome_path.read_text())
            if (outcome["origin_session"] != origin.isoformat()
                    or outcome["target_session"] != target.isoformat()
                    or outcome["horizon"] != horizon):
                raise ValueError(f"Outcome metadata mismatch: {outcome_path}")
            recorded = {entry["symbol"]: entry for entry in outcome["records"]}
            if len(recorded) != len(outcome["records"]) or set(recorded) != set(issued):
                raise ValueError(f"Outcome coverage mismatch: {outcome_path}")
            for symbol, entry in issued.items():
                if (symbol not in symbols or entry["target_session"] != target.isoformat()
                        or not math.isclose(float(entry["origin_close"]), float(closes.loc[(origin, symbol)]), abs_tol=1e-4)
                        or entry["forecast_close"] is None):
                    raise ValueError(f"Forecast value or target mismatch: {symbol} {origin} h{horizon}")
                actual = float(closes.loc[(target, symbol)])
                predicted = float(entry["forecast_close"])
                unchanged = float(entry["origin_close"])
                saved = recorded[symbol]
                if (saved["origin_session"] != origin.isoformat()
                        or saved["target_session"] != target.isoformat()
                        or saved["horizon"] != horizon
                        or not math.isclose(float(saved["forecast_close"]), predicted, abs_tol=1e-4)
                        or not math.isclose(float(saved["actual_close"]), actual, abs_tol=1e-4)
                        or not math.isclose(float(saved["absolute_error"]), abs(predicted - actual), abs_tol=1e-4)
                        or not math.isclose(float(saved["unchanged_close_error"]), abs(unchanged - actual), abs_tol=1e-4)):
                    raise ValueError(f"Outcome disagrees with verified closes: {symbol} {target} h{horizon}")
                rows[horizon].append({"symbol": symbol, "origin_session": origin.isoformat(),
                                      "target_session": target.isoformat(),
                                      "absolute_error": abs(predicted - actual),
                                      "unchanged_close_error": abs(unchanged - actual)})
    return {"schema_version": 1, "forecast_files_audited": forecast_count,
            "commit_rule": "original_forecast_bytes_committed_after_issue_before_next_open",
            "horizons": {str(horizon): summarize(rows[horizon]) for horizon in (1, 5)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--calendar", type=Path, default=ROOT / "config/nse_equity_calendar_2026.json")
    args = parser.parse_args()
    print(json.dumps(build_report(args.state_dir, args.calendar), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
