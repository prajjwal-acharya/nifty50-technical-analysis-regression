"""Independently verify generated files, targets, and the final release gates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .data_pipeline import ROOT, SEGMENT_BOUNDARY_TYPES, sha256


def audit(output: Path, universe: Path, raw: Path, bse: Path) -> dict:
    quality = json.loads((output / "quality_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    expected_symbols = set(json.loads(universe.read_text(encoding="utf-8"))["symbols"])
    if quality["status"] != "finalized_for_downstream" or quality["release_gates"]:
        raise ValueError("The pipeline has not released a final dataset")
    for name, digest in manifest["outputs_sha256"].items():
        if sha256(output / name) != digest:
            raise ValueError(f"Output hash differs from manifest: {name}")
    if manifest["raw_sha256"] != sha256(raw) or manifest["bse_nse_raw_sha256"] != sha256(bse):
        raise ValueError("Raw input hash differs from manifest")
    prices = pd.read_csv(output / "daily_prices.csv.gz", parse_dates=["date"], low_memory=False)
    features = pd.read_csv(output / "features.csv.gz", parse_dates=["date"])
    if set(prices.symbol) != expected_symbols or len(prices) != quality["price_rows"]:
        raise ValueError("Price rows or official universe differ")
    if prices[["symbol", "date"]].duplicated().any() or features[["symbol", "date"]].duplicated().any():
        raise ValueError("Duplicate stock-date key")
    if not prices[["symbol", "date", "segment_id"]].equals(
        features[["symbol", "date", "segment_id"]]
    ):
        raise ValueError("Feature rows do not align with prices")
    if prices.loc[prices.symbol.eq("BSE")].shape[0] != quality["counts"]["supplemental_nse_bse_rows"]:
        raise ValueError("BSE row count differs")
    if "WIPRO" in set(prices.symbol):
        raise ValueError("Excluded WIPRO remains in the current universe")
    if int(features.feature_ready.sum()) != quality["feature_ready_rows"]:
        raise ValueError("Feature readiness count differs")
    core = ["sma_20", "sma_50", "macd_signal", "rsi_14"]
    if features.loc[features.feature_ready, core].isna().any().any():
        raise ValueError("A model-ready feature is blank")
    actions = pd.read_csv(output / "corporate_actions.csv", parse_dates=["ex_date"])
    for action in actions.loc[actions.action_type.isin(SEGMENT_BOUNDARY_TYPES)].itertuples(index=False):
        stock = prices.loc[prices.symbol.eq(action.symbol)]
        before = stock.loc[stock.date.lt(action.ex_date)].iloc[-1]
        after = stock.loc[stock.date.eq(action.ex_date)].iloc[0]
        if before.segment_id == after.segment_id:
            raise ValueError(f"Entitlement event crosses a segment: {action.action_id}")
    target_counts = {}
    for horizon in (1, 5):
        targets = pd.read_csv(output / f"target_{horizon}d.csv.gz",
                              parse_dates=["date", "target_date", "reference_entry_date"])
        if targets[["symbol", "date"]].duplicated().any() or not targets.eligible.all():
            raise ValueError(f"Invalid {horizon}d target keys")
        expected = features[["symbol", "date", "segment_id", "adj_open", "adj_close"]].copy()
        groups = expected.groupby("segment_id", sort=False)
        expected["expected_target_date"] = groups.date.shift(-horizon)
        expected["expected_exit_close"] = groups.adj_close.shift(-horizon)
        expected["expected_entry_date"] = groups.date.shift(-1)
        expected["expected_entry_open"] = groups.adj_open.shift(-1)
        joined = targets.merge(expected, on=["symbol", "date", "segment_id"],
                               how="left", validate="one_to_one")
        if len(joined) != len(targets) or joined.expected_target_date.isna().any():
            raise ValueError(f"Missing {horizon}d target origin")
        if not joined.target_date.equals(joined.expected_target_date):
            raise ValueError(f"{horizon}d target dates differ")
        if not joined.reference_entry_date.equals(joined.expected_entry_date):
            raise ValueError(f"{horizon}d entry dates differ")
        expected_return = joined.expected_exit_close / joined.adj_close - 1
        expected_execution = joined.expected_exit_close / joined.expected_entry_open - 1
        if not np.allclose(joined.forward_close_return, expected_return, rtol=0, atol=1e-8):
            raise ValueError(f"{horizon}d forecast returns differ")
        if not np.allclose(joined.reference_gross_executable_return, expected_execution,
                           rtol=0, atol=1e-8):
            raise ValueError(f"{horizon}d execution references differ")
        target_counts[f"target_{horizon}d_rows"] = len(targets)
    return {
        "status": "passed",
        "audited_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_sha256": sha256(output / "manifest.json"),
        "official_symbols": len(expected_symbols),
        "price_rows": len(prices),
        "feature_ready_rows": int(features.feature_ready.sum()),
        "entitlement_boundaries_checked": int(actions.action_type.isin(SEGMENT_BOUNDARY_TYPES).sum()),
        "output_hashes_checked": len(manifest["outputs_sha256"]),
        **target_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--universe", type=Path, default=ROOT / "config/nifty50_universe.json")
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/50Nifty50.xlsx")
    parser.add_argument("--bse", type=Path, default=ROOT / "data/raw/bse_nse_prices.csv")
    args = parser.parse_args()
    report = audit(args.output, args.universe, args.raw, args.bse)
    (args.output / "release_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
