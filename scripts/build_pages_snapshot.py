"""Build the explicit public dashboard projection from the private live store."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import Calendar, IST, UTC, read_sessions
from scripts.verify_live_store import verify


def unavailable(reason: str, latest_session: str | None = None) -> dict:
    return {"schema_version": 1, "status": "unavailable", "reason": reason,
            "latest_verified_session": latest_session, "stocks": []}


def build_snapshot(state_dir: Path, calendar_path: Path,
                   *, publication_authorized: bool = False,
                   now: datetime | None = None) -> dict:
    """Export only display fields; never copy private source or model files."""
    if not publication_authorized:
        return unavailable("Public market-data display requires documented NSE permission.")

    state_dir = state_dir.resolve()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Build time must include a timezone")
    manifest = verify(state_dir)
    calendar = Calendar(calendar_path)
    universe = json.loads((state_dir / "baseline/config/nifty50_universe.json").read_text())
    symbols = set(universe["symbols"])
    baseline = pd.read_csv(state_dir / "baseline/daily_prices.csv.gz", parse_dates=["date"], low_memory=False)
    prices, stored_days = read_sessions(state_dir, baseline, symbols, calendar)
    latest = (stored_days[-1] if stored_days else baseline.date.max().date()).isoformat()
    forecasts = sorted((state_dir / "forecasts").glob("*/*/*.json"))
    if not forecasts:
        return unavailable("No prospective forecast has been issued.", latest)
    issued = json.loads(forecasts[-1].read_text())
    origin = date.fromisoformat(issued["origin_session"])
    if (origin.isoformat() > latest or issued["issue_kind"] != "prospective"
            or issued["model_sha256"] != manifest["one_day_model_sha256"]
            or issued["calendar_sha256"] != calendar.sha256):
        raise ValueError("Latest forecast does not match the verified store and calendar")
    origin_rows = prices.loc[prices.date.eq(pd.Timestamp(origin))].set_index("symbol")
    if set(origin_rows.index) != symbols or len(origin_rows) != 50:
        raise ValueError("Forecast origin has incomplete verified prices")

    keyed = {(entry["symbol"], entry["horizon"]): entry for entry in issued["forecasts"]}
    if len(keyed) != 100 or set(keyed) != {(symbol, horizon) for symbol in symbols for horizon in (1, 5)}:
        raise ValueError("Forecast must cover both horizons for the full basket")
    actuals = prices.set_index([prices.date.dt.date, "symbol"]).raw_close
    stocks = []
    for symbol in sorted(symbols):
        reference_close = float(origin_rows.loc[symbol, "raw_close"])
        horizons = {}
        for horizon in (1, 5):
            entry = keyed[(symbol, horizon)]
            target = calendar.forward(origin, horizon)
            if (entry["target_session"] != target.isoformat()
                    or abs(float(entry["origin_close"]) - reference_close) > 0.0001):
                raise ValueError(f"Forecast origin or target mismatch for {symbol} h{horizon}")
            status = entry["status"]
            forecast_close = entry["forecast_close"]
            expected_policy = "frozen_one_day_lightgbm" if horizon == 1 else "unchanged_close"
            if entry["policy"] != expected_policy:
                raise ValueError(f"Unexpected forecast policy for {symbol} h{horizon}")
            if status not in ("issued", "abstained_known_corporate_action"):
                raise ValueError(f"Unknown forecast status for {symbol} h{horizon}")
            if (status == "issued") != (forecast_close is not None):
                raise ValueError(f"Invalid abstention value for {symbol} h{horizon}")
            if horizon == 5 and status == "issued" and abs(float(forecast_close) - reference_close) > 0.0001:
                raise ValueError("Fifth-session policy must be unchanged close")
            realized = target in stored_days
            actual = float(actuals.loc[(target, symbol)]) if realized else None
            horizons[str(horizon)] = {
                "target_session": target.isoformat(), "policy": entry["policy"],
                "status": status, "forecast_close": forecast_close,
                "actual_close": actual,
                "absolute_error": round(abs(float(forecast_close) - actual), 4)
                    if realized and forecast_close is not None else None,
            }
        stocks.append({"symbol": symbol, "origin_close": reference_close, "horizons": horizons})

    next_target = calendar.forward(origin, 1)
    issued_at = datetime.fromisoformat(issued["issued_at_utc"])
    next_open = datetime.combine(next_target, time(9), IST)
    if issued_at.tzinfo is None or issued_at.astimezone(IST) >= next_open:
        raise ValueError("Forecast was not issued before the next target opened")
    return {
        "schema_version": 1,
        "status": "current" if latest < next_target.isoformat() and now.astimezone(IST) < next_open
            else "resolved_or_stale",
        "origin_session": origin.isoformat(),
        "latest_verified_session": latest,
        "issued_at_utc": issued["issued_at_utc"],
        "generated_at_utc": now.astimezone(UTC).isoformat(),
        "basket_as_of": universe["as_of_date"],
        "source_label": "NSE end-of-day security report and CM UDiFF bhavcopy",
        "model_label": "Frozen 26-feature LightGBM for next session",
        "fifth_session_policy": "unchanged_close",
        "stocks": stocks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calendar", type=Path, default=ROOT / "config/nse_equity_calendar_2026.json")
    parser.add_argument("--publication-authorized", action="store_true",
                        help="Use only after obtaining permission for public NSE market-data display")
    args = parser.parse_args()
    payload = build_snapshot(args.state_dir, args.calendar,
                             publication_authorized=args.publication_authorized)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": payload["status"], "stocks": len(payload["stocks"]),
                      "output": str(args.output)}))


if __name__ == "__main__":
    main()
