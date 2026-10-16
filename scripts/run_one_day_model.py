"""Rerun the frozen next-session model on 2025 and inspected 2026 data.

Run after scripts.build_model_features: python -m scripts.run_one_day_model
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from nifty50_ta.cross_sectional_expansion import expand_cross_sectional_features
from nifty50_ta.data_pipeline import create_targets
from nifty50_ta.feature_expansion import CUTOFF, expand_features, uniform_stock_weights
from nifty50_ta.model_common import KEY, ROOT, sha256
from nifty50_ta.one_day_model import (FEATURES, build_horizon_table,
    constrained_lgbm, load_pre2026_features, score_validation)
from nifty50_ta.price_forecasting import attach_quoted_prices

TRAIN_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2026-01-01")
TEST_END = pd.Timestamp("2026-10-13")


def load_complete_features(path: Path) -> pd.DataFrame:
    """Rebuild the 26 features with history, entirely in memory."""
    original = pd.read_csv(path, parse_dates=["date"], low_memory=False)
    if original.date.isna().any() or original.date.max() != TEST_END:
        raise ValueError("Complete feature store must end on 2026-10-13")
    if original.date.min() >= CUTOFF or not original.date.ge(TEST_START).any():
        raise ValueError("Complete feature store lacks pre-2026 history or test rows")
    # Reproduce the historical 26-feature result with its original formulas.
    eighteen = expand_features(original, allow_test_rows=True, legacy_raw_ohlcv=True)
    return expand_cross_sectional_features(eighteen, allow_test_rows=True)


def check_training_feature_parity(saved: pd.DataFrame, complete: pd.DataFrame) -> None:
    """Confirm that adding 2026 history did not alter saved training features."""
    earlier = complete.loc[complete.date.lt(CUTOFF),
                           KEY + FEATURES + ["raw_close", "adj_close",
                                              "adjustment_factor"]]
    compare = saved[KEY + FEATURES + ["raw_close", "adj_close",
                                     "adjustment_factor"]].merge(
        earlier, on=KEY, how="outer", validate="one_to_one",
        suffixes=("_saved", "_rebuilt"), indicator=True,
    )
    if len(compare) != len(saved) or not compare._merge.eq("both").all():
        raise ValueError("Saved and complete feature stores have different pre-2026 keys")
    for column in FEATURES + ["raw_close", "adj_close", "adjustment_factor"]:
        if not np.allclose(compare[f"{column}_saved"],
                           compare[f"{column}_rebuilt"],
                           rtol=1e-8, atol=1e-10, equal_nan=True):
            raise ValueError(f"Saved and rebuilt feature values differ: {column}")


def build_test_table(complete: pd.DataFrame, horizon: int,
                     data_config: dict) -> tuple[pd.DataFrame, int]:
    """Use the pipeline's within-segment, same-split target eligibility."""
    targets = create_targets(complete, horizon, data_config)
    targets = targets.loc[targets.split.eq("test")].copy()
    if targets.empty or not targets.date.between(TEST_START, TEST_END).all():
        raise ValueError(f"No eligible {horizon}d test origins in the fixed period")
    if not targets.target_date.between(TEST_START, TEST_END).all():
        raise ValueError("A test label crossed the fixed 2026 test boundary")
    test = targets[KEY + ["target_date", "split", "eligible"]].merge(
        complete[KEY + FEATURES + ["feature_ready"]],
        on=KEY, how="left", validate="one_to_one", indicator=True,
    )
    if not test._merge.eq("both").all() or not test.feature_ready.eq(True).all():
        raise ValueError("Test target lacks a ready feature origin")
    test = test.drop(columns="_merge")
    prices = complete[KEY + ["raw_close", "adjustment_factor"]]
    test, excluded = attach_quoted_prices(test, prices)
    test = test.sort_values(KEY).reset_index(drop=True)
    if test.empty or not np.isfinite(test[FEATURES].to_numpy(dtype=float)).all():
        raise ValueError("Empty test table or non-finite test feature")
    return test, excluded


def forecast_rows(test: pd.DataFrame, predicted_return: np.ndarray) -> pd.DataFrame:
    origin = test.origin_raw_close.to_numpy(dtype=float)
    actual = test.target_raw_close.to_numpy(dtype=float)
    predicted_price = origin * (1.0 + predicted_return)
    result = test[KEY + ["target_date"]].copy()
    result["origin_raw_close_inr"] = origin
    result["actual_close_inr"] = actual
    result["actual_return"] = test.forward_raw_return.to_numpy(dtype=float)
    result["predicted_return"] = predicted_return
    result["predicted_close_inr"] = predicted_price
    result["unchanged_price_baseline_close_inr"] = origin
    result["model_absolute_error_inr"] = np.abs(predicted_price - actual)
    result["baseline_absolute_error_inr"] = np.abs(origin - actual)
    return result


def run(train_path: Path, complete_path: Path, data_config_path: Path,
        output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite {output_dir}")
    saved = load_pre2026_features(train_path)
    complete = load_complete_features(complete_path)
    check_training_feature_parity(saved, complete)
    config = json.loads(data_config_path.read_text(encoding="utf-8"))
    release_path = ROOT / "docs/data-release-2026-10-15.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if (release["audit_status"] != "passed"
            or sha256(complete_path) != release["output_sha256"]["features.csv.gz"]):
        raise ValueError("Complete features do not match the audited release")
    if (config["train_end"] != "2024-12-31"
            or config["validation_end"] != "2025-12-31"
            or config["test_end"] != "2026-10-13"):
        raise ValueError("Unexpected data split boundaries")
    table, train_excluded = build_horizon_table(saved, 1)
    fit = table.loc[table.split.eq("train")].copy()
    valid = table.loc[table.split.eq("validation")].copy()
    test, test_excluded = build_test_table(complete, 1, config)
    if fit.empty or not fit.target_date.le(TRAIN_END).all():
        raise ValueError("Fit labels crossed the training boundary")
    model = constrained_lgbm()
    model.fit(fit[FEATURES], fit.forward_raw_return,
              sample_weight=uniform_stock_weights(fit))
    valid_prediction = np.asarray(model.predict(valid[FEATURES]), dtype=float)
    test_prediction = np.asarray(model.predict(test[FEATURES]), dtype=float)
    if not np.isfinite(valid_prediction).all() or not np.isfinite(test_prediction).all():
        raise ValueError("Non-finite model prediction")
    result = {"status": "historical_rerun_one_day", "feature_count": len(FEATURES),
              "feature_order": FEATURES, "training_rows": len(fit),
              "training_end": "2024-12-31", "model": "Constrained LightGBM",
              "gamma": 1.0, "training_crossings_excluded": train_excluded,
              "test_crossings_excluded": test_excluded,
              "source_sha256": sha256(complete_path), "training_store_sha256": sha256(train_path),
              "periods": {}}
    output_dir.mkdir(parents=True)
    model.booster_.save_model(str(output_dir / "one_day_lightgbm.txt"))
    for name, frame, prediction in (("validation_2025", valid, valid_prediction),
                                     ("test_2026", test, test_prediction)):
        score = score_validation(frame, prediction)
        baseline = score_validation(frame, np.zeros(len(frame)))
        result["periods"][name] = {
            "rows": len(frame), "stocks": int(frame.symbol.nunique()),
            "model_equal_stock_mae_inr": score["mae"],
            "baseline_equal_stock_mae_inr": baseline["mae"],
            "stocks_beating_baseline": score["stocks_beating_baseline"],
            "first_origin": str(frame.date.min().date()),
            "last_origin": str(frame.date.max().date()),
            "last_target": str(frame.target_date.max().date()),
        }
        forecast_rows(frame, prediction).to_csv(
            output_dir / f"{name}_predictions.csv.gz", index=False,
            compression={"method": "gzip", "mtime": 0},
            date_format="%Y-%m-%d", float_format="%.12g",
        )
    (output_dir / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    sources = [Path(__file__), ROOT / "src/nifty50_ta/one_day_model.py",
               ROOT / "src/nifty50_ta/feature_expansion.py",
               ROOT / "src/nifty50_ta/cross_sectional_expansion.py"]
    manifest = {
        "model_file": "one_day_lightgbm.txt",
        "model_sha256": sha256(output_dir / "one_day_lightgbm.txt"),
        "release_lock_sha256": sha256(release_path),
        "data_config_sha256": sha256(data_config_path),
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
        "library_versions": {name: version(name) for name in
                             ("lightgbm", "numpy", "pandas")},
        "feature_order": FEATURES,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-features", type=Path, default=(
        ROOT / "data/processed/features_cross_sectional_pre2026.csv.gz"))
    parser.add_argument("--complete-features", type=Path, default=(
        ROOT / "data/processed/features.csv.gz"))
    parser.add_argument("--data-config", type=Path, default=ROOT / "config/data_pipeline.json")
    parser.add_argument("--output-dir", type=Path, default=(
        ROOT / "reports/generated/one_day" /
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")))
    args = parser.parse_args()
    result = run(args.training_features, args.complete_features,
                 args.data_config, args.output_dir)
    for period, score in result["periods"].items():
        print(f"{period}: model ₹{score['model_equal_stock_mae_inr']:.4f}; "
              f"unchanged ₹{score['baseline_equal_stock_mae_inr']:.4f}; "
              f"rows {score['rows']:,}")
    print(f"Saved: {args.output_dir}")


if __name__ == "__main__":
    main()
