"""Score the saved corrected five-session candidate on the 2026 snapshot.

The model is loaded from the 2025 run, never refitted. The 2026 period was
already inspected for an earlier candidate, so this is an exploratory test.
Run: python -m scripts.evaluate_corrected_5d_2026
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import Booster

from nifty50_ta.cross_sectional_expansion import expand_cross_sectional_features
from nifty50_ta.data_pipeline import create_targets
from nifty50_ta.feature_expansion import expand_features
from nifty50_ta.model_common import KEY, ROOT, sha256
from nifty50_ta.price_forecasting import attach_quoted_prices, price_metrics
from nifty50_ta.five_day_model import MODEL_FEATURES, engineer_features


TEST_START = pd.Timestamp("2026-01-01")
TEST_END = pd.Timestamp("2026-10-13")
EXPECTED_TEST_ROWS = 9342


def verify_feature_parity(saved: pd.DataFrame, complete: pd.DataFrame) -> None:
    """Adding test rows must leave every saved pre-2026 predictor unchanged."""
    columns = KEY + list(MODEL_FEATURES) + ["raw_close", "adjustment_factor"]
    before = saved[columns].merge(
        complete.loc[complete.date.lt(TEST_START), columns],
        on=KEY, how="outer", validate="one_to_one",
        suffixes=("_saved", "_rebuilt"), indicator=True,
    )
    if len(before) != len(saved) or not before._merge.eq("both").all():
        raise ValueError("Corrected feature keys changed after adding 2026 rows")
    for name in list(MODEL_FEATURES) + ["raw_close", "adjustment_factor"]:
        if not np.allclose(before[f"{name}_saved"], before[f"{name}_rebuilt"],
                           rtol=1e-8, atol=1e-10, equal_nan=True):
            raise ValueError(f"Historical feature changed after adding 2026: {name}")


def build_test_table(complete: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, int]:
    if (config["train_end"] != "2024-12-31"
            or config["validation_end"] != "2025-12-31"
            or config["test_end"] != "2026-10-13"):
        raise ValueError("Unexpected release split dates")
    targets = create_targets(complete, 5, config)
    targets = targets.loc[targets.split.eq("test"), KEY + ["target_date", "split"]]
    table = targets.merge(complete[KEY + list(MODEL_FEATURES) + ["feature_ready"]],
                          on=KEY, how="left", validate="one_to_one", indicator=True)
    if not table._merge.eq("both").all() or not table.feature_ready.eq(True).all():
        raise ValueError("A test target has missing or unready origin features")
    table = table.drop(columns="_merge")
    prices = complete[KEY + ["raw_close", "adjustment_factor"]]
    table, excluded = attach_quoted_prices(table, prices)
    table = table.sort_values(KEY).reset_index(drop=True)
    if (len(table) != EXPECTED_TEST_ROWS or table.symbol.nunique() != 50
            or not table.date.between(TEST_START, TEST_END).all()
            or not table.target_date.between(TEST_START, TEST_END).all()
            or np.isinf(table[list(MODEL_FEATURES)].to_numpy(dtype=float)).any()):
        raise ValueError("Unexpected corrected five-day test coverage or predictors")
    return table, excluded


def run(model_run: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite {output_dir}")
    manifest = json.loads((model_run / "manifest.json").read_text())
    selected = json.loads((model_run / "metrics.json").read_text())["selected"]
    if (manifest["status"] != "exploratory_2025_validation_of_fold_selected_5d_model"
            or manifest["model_file"] != "selected_lightgbm.txt"
            or manifest["feature_order"] != list(MODEL_FEATURES)
            or selected["name"] != "Corrected LightGBM 30"):
        raise ValueError("Model run is not the fixed corrected 30-feature candidate")
    model_path = model_run / manifest["model_file"]
    if sha256(model_path) != manifest["model_file_sha256"]:
        raise ValueError("Saved model checksum mismatch")

    release_path = ROOT / "docs/data-release-2026-10-15.json"
    release = json.loads(release_path.read_text())
    source_path = ROOT / "data/processed/features.csv.gz"
    saved_path = ROOT / "data/processed/features_5d_corrected_pre2026.csv.gz"
    if (release["audit_status"] != "passed"
            or sha256(source_path) != release["output_sha256"][source_path.name]
            or sha256(saved_path) != manifest["feature_store_sha256"]):
        raise ValueError("Feature data does not match the audited release")
    source = pd.read_csv(source_path, parse_dates=["date"], low_memory=False)
    if source.date.max() != TEST_END:
        raise ValueError("Complete data must end on the fixed 2026-10-13 test date")
    complete = engineer_features(
        expand_cross_sectional_features(
            expand_features(source, allow_test_rows=True), allow_test_rows=True
        ), allow_test_rows=True,
    )
    saved = pd.read_csv(saved_path, parse_dates=["date"], low_memory=False)
    verify_feature_parity(saved, complete)
    config_path = ROOT / "config/data_pipeline.json"
    table, excluded = build_test_table(complete, json.loads(config_path.read_text()))

    booster = Booster(model_file=str(model_path))
    predicted_return = selected["gamma"] * np.asarray(
        booster.predict(table[list(MODEL_FEATURES)]), dtype=float
    )
    if not np.isfinite(predicted_return).all():
        raise ValueError("Saved model produced non-finite returns")
    baseline_return = np.zeros(len(table))
    model_metrics = price_metrics(table, predicted_return)
    baseline_metrics = price_metrics(table, baseline_return)
    price = table.origin_raw_close.to_numpy(dtype=float)
    actual = table.target_raw_close.to_numpy(dtype=float)
    candidate_price = price * (1 + predicted_return)
    predictions = table[KEY + ["target_date", "origin_raw_close", "target_raw_close"]].copy()
    predictions["actual_return"] = table.forward_raw_return.to_numpy(dtype=float)
    predictions["candidate_return"] = predicted_return
    predictions["candidate_close_inr"] = candidate_price
    predictions["unchanged_close_inr"] = price
    predictions["candidate_absolute_error_inr"] = np.abs(candidate_price - actual)
    predictions["baseline_absolute_error_inr"] = np.abs(price - actual)
    # The previously frozen 2025 forecast policy was unchanged price.
    predictions["forecast_policy_close_inr"] = price
    by_stock = predictions.groupby("symbol").agg(
        rows=("date", "size"),
        candidate_mae_inr=("candidate_absolute_error_inr", "mean"),
        baseline_mae_inr=("baseline_absolute_error_inr", "mean"),
    ).reset_index()
    by_stock["candidate_minus_baseline_inr"] = (
        by_stock.candidate_mae_inr - by_stock.baseline_mae_inr
    )

    result = {
        "status": "exploratory_2026_test_of_saved_corrected_5d_candidate",
        "model_was_refitted": False,
        "model_training_end": "2024-12-31",
        "forecast_policy_selected_on_2025": "unchanged_price",
        "test_origin_first": str(table.date.min().date()),
        "test_origin_last": str(table.date.max().date()),
        "test_target_last": str(table.target_date.max().date()),
        "test_rows": len(table), "test_stocks": int(table.symbol.nunique()),
        "split_bonus_crossings_excluded": excluded,
        "candidate": model_metrics,
        "unchanged_price": baseline_metrics,
        "candidate_minus_unchanged_equal_stock_mae_inr": (
            model_metrics["equal_stock_price_mae_inr"]
            - baseline_metrics["equal_stock_price_mae_inr"]
        ),
        "candidate_stocks_beating_unchanged": int(by_stock.candidate_minus_baseline_inr.lt(0).sum()),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    predictions.to_csv(output_dir / "test_predictions.csv.gz", index=False,
                       compression={"method": "gzip", "mtime": 0},
                       date_format="%Y-%m-%d", float_format="%.12g")
    by_stock.to_csv(output_dir / "by_stock.csv", index=False)
    (output_dir / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    run_manifest = {
        "saved_model_run": str(model_run.relative_to(ROOT)),
        "saved_model_sha256": sha256(model_path),
        "base_release_sha256": sha256(release_path),
        "source_features_sha256": sha256(source_path),
        "corrected_pre2026_features_sha256": sha256(saved_path),
        "evaluation_source_sha256": sha256(Path(__file__)),
        "data_config_sha256": sha256(config_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-run", type=Path,
                        help="2025 model run; defaults to latest saved run")
    parser.add_argument("--output-dir", type=Path, default=(
        ROOT / "reports/generated/price_forecasting_5d_2026_corrected" /
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ))
    args = parser.parse_args()
    if args.model_run is None:
        runs = sorted((ROOT / "reports/generated/price_forecasting_5d_2025").glob(
            "*/selected_lightgbm.txt"))
        if not runs:
            parser.error("No saved fifth-session model. Run scripts.evaluate_5d_2025 first")
        args.model_run = runs[-1].parent
    score = run(args.model_run, args.output_dir)
    print(f"2026 corrected 5d candidate: ₹{score['candidate']['equal_stock_price_mae_inr']:.4f}")
    print(f"2026 unchanged price:       ₹{score['unchanged_price']['equal_stock_price_mae_inr']:.4f}")
    print(f"Candidate minus unchanged:  ₹{score['candidate_minus_unchanged_equal_stock_mae_inr']:+.4f}")
    print(f"Stocks improved: {score['candidate_stocks_beating_unchanged']}/{score['test_stocks']}")
    print(f"Saved result: {args.output_dir}")


if __name__ == "__main__":
    main()
