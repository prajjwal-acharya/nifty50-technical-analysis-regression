"""Select on 2021-2024 folds and inspect corrected five-day forecasts on 2025.

Reads only features_5d_corrected_pre2026.csv.gz. Five-session targets are
rebuilt within each segment, and no 2026 test file is opened or scored.

Run: python -B -m scripts.evaluate_5d_2025
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from nifty50_ta.feature_expansion import CUTOFF, price_stock_weights
from nifty50_ta.model_common import KEY, ROOT, sha256
from nifty50_ta.five_day_model import MODEL_FEATURES, summarize_grid


FEATURES = list(MODEL_FEATURES)
VALIDATION_START = pd.Timestamp("2025-01-01")
EXPECTED_VALIDATION_ROWS = 12019
EXPECTED_BASELINE_MAE = 64.44317194622782
BASE_MODEL_PARAMS = {
    "learning_rate": 0.01, "objective": "regression_l1", "reg_lambda": 50.0,
    "subsample": 0.7, "subsample_freq": 1,
    "colsample_bytree": 0.8, "random_state": 42,
    "verbosity": -1, "n_jobs": 4,
}


def load_features(path: Path) -> pd.DataFrame:
    """Read the dedicated pre-2026 feature store and reject mixed inputs."""
    features = pd.read_csv(path, parse_dates=["date"], low_memory=False)
    required = set(KEY + FEATURES + [
        "raw_close", "adj_close", "adjustment_factor", "feature_ready",
    ])
    if missing := required - set(features):
        raise ValueError(f"Missing feature columns: {sorted(missing)}")
    if len(FEATURES) != 30 or len(set(FEATURES)) != 30:
        raise ValueError("Expected exactly 30 distinct model predictors")
    if features.date.isna().any() or features.date.ge(CUTOFF).any():
        raise ValueError("Feature store must contain only pre-2026 rows")
    if features[KEY].isna().any().any() or features[KEY].duplicated().any():
        raise ValueError("Missing or duplicate feature key")
    if features[["date", "symbol"]].duplicated().any():
        raise ValueError("Duplicate stock-date")
    if not features.segment_id.str.split(":").str[0].eq(features.symbol).all():
        raise ValueError("Segment ID does not match symbol")
    return features.sort_values(["symbol", "date"]).reset_index(drop=True)


def build_5d_table(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Keep only labels ending inside the origin's train/validation split."""
    if features.date.ge(CUTOFF).any():
        raise ValueError("2026 feature row supplied")
    table = features.copy()
    segment = table.groupby("segment_id", sort=False)
    table["target_date"] = segment.date.shift(-5)
    table["target_raw_close"] = segment.raw_close.shift(-5)
    table["target_adj_close"] = segment.adj_close.shift(-5)
    table["target_factor"] = segment.adjustment_factor.shift(-5)
    train_origin = table.date.lt(VALIDATION_START)
    train_target = table.target_date.lt(VALIDATION_START)
    eligible = (
        table.feature_ready.eq(True) & table.target_date.notna()
        & table.target_adj_close.notna() & table.target_date.lt(CUTOFF)
        & train_origin.eq(train_target)
    )
    table = table.loc[eligible].copy()
    same_share_basis = np.isclose(
        table.adjustment_factor, table.target_factor, rtol=1e-12, atol=1e-12,
    )
    excluded = int((~same_share_basis).sum())
    table = table.loc[same_share_basis].copy()
    table = table.rename(columns={"raw_close": "origin_raw_close"})
    if not (table[["origin_raw_close", "target_raw_close"]] > 0).all().all():
        raise ValueError("Raw closes must be positive")
    table["forward_raw_return"] = (
        table.target_raw_close / table.origin_raw_close - 1.0
    )
    table["split"] = np.where(table.date.lt(VALIDATION_START), "train", "validation")
    if not np.isfinite(table[["forward_raw_return"]].to_numpy(dtype=float)).all():
        raise ValueError("Non-finite return")
    if np.isinf(table[FEATURES].to_numpy(dtype=float)).any():
        raise ValueError("Infinite optimized predictor")
    train = table.loc[table.date.lt(VALIDATION_START)].sort_values(KEY).reset_index(drop=True)
    valid = table.loc[table.date.ge(VALIDATION_START)].sort_values(KEY).reset_index(drop=True)
    if train.empty or valid.empty:
        raise ValueError("Empty train or validation partition")
    if not (train.target_date.lt(VALIDATION_START).all()
            and valid.date.ge(VALIDATION_START).all()
            and valid.target_date.between(VALIDATION_START, CUTOFF - pd.Timedelta(days=1)).all()):
        raise ValueError("Five-day label crossed a split boundary")
    return train, valid, excluded


def stock_metrics(valid: pd.DataFrame, prediction: np.ndarray) -> pd.DataFrame:
    origin = valid.origin_raw_close.to_numpy(dtype=float)
    target = valid.target_raw_close.to_numpy(dtype=float)
    if len(prediction) != len(valid) or not np.isfinite(prediction).all():
        raise ValueError("Invalid validation prediction")
    return pd.DataFrame({
        "symbol": valid.symbol.to_numpy(),
        "model_error_inr": np.abs(origin * (1 + prediction) - target),
        "baseline_error_inr": np.abs(origin - target),
    }).groupby("symbol").agg(
        rows=("model_error_inr", "size"),
        model_mae_inr=("model_error_inr", "mean"),
        baseline_mae_inr=("baseline_error_inr", "mean"),
    ).reset_index()


def information_coefficients(valid: pd.DataFrame, prediction: np.ndarray) -> dict:
    if len(prediction) and np.all(prediction == prediction[0]):
        return dict.fromkeys(("within_stock_pearson_ic", "within_stock_spearman_ic",
                              "overall_pearson_ic", "overall_spearman_ic"))
    paired = pd.DataFrame({
        "symbol": valid.symbol.to_numpy(), "predicted": prediction,
        "actual": valid.forward_raw_return.to_numpy(dtype=float),
    })
    within = paired.groupby("symbol").apply(
        lambda stock: pd.Series({
            "pearson": stock.predicted.corr(stock.actual, method="pearson"),
            "spearman": stock.predicted.corr(stock.actual, method="spearman"),
        }), include_groups=False,
    )
    values = {
        "within_stock_pearson_ic": float(within.pearson.mean()),
        "within_stock_spearman_ic": float(within.spearman.mean()),
        "overall_pearson_ic": float(paired.predicted.corr(paired.actual, method="pearson")),
        "overall_spearman_ic": float(paired.predicted.corr(paired.actual, method="spearman")),
    }
    if not np.isfinite(list(values.values())).all():
        raise ValueError("IC is undefined for a validation series")
    return values


def fit_selected(train: pd.DataFrame, valid: pd.DataFrame, selected: dict):
    """Fit the fold-selected candidate once through 2024."""
    gamma = selected["gamma"]
    if gamma == 0:
        return None, np.zeros(len(valid)), None
    if selected["name"] == "Corrected LightGBM 30":
        params = dict(BASE_MODEL_PARAMS, **selected["params"])
        model = LGBMRegressor(**params)
        model.fit(train[FEATURES], train.forward_raw_return,
                  sample_weight=price_stock_weights(train))
        prediction = np.asarray(model.predict(valid[FEATURES]), dtype=float)
    else:
        raise ValueError(f"Unknown selected model: {selected['name']}")
    prediction *= gamma
    if not np.isfinite(prediction).all():
        raise ValueError("Selected model returned a non-finite prediction")
    return model, prediction, params


def evaluate(input_path: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict, object]:
    features = load_features(input_path)
    train, valid, excluded = build_5d_table(features)
    if len(valid) != EXPECTED_VALIDATION_ROWS or valid.symbol.nunique() != 50:
        raise ValueError(f"Unexpected validation coverage: {len(valid)} rows, "
                         f"{valid.symbol.nunique()} stocks")
    baseline_error = np.abs(valid.origin_raw_close - valid.target_raw_close)
    baseline = float(baseline_error.groupby(valid.symbol).mean().mean())
    if not np.isclose(baseline, EXPECTED_BASELINE_MAE, rtol=0, atol=1e-8):
        raise ValueError(f"Unexpected 2025 unchanged-price baseline: ₹{baseline}")

    search = summarize_grid(train)
    baseline_candidate = {"name": "Unchanged price", "gamma": 0.0,
                          "mean": float(np.mean(search["baseline_folds"]))}
    candidates = [baseline_candidate] + search["best"]
    selected = min(candidates, key=lambda item: item["mean"])
    model, prediction, params = fit_selected(train, valid, selected)
    by_stock = stock_metrics(valid, prediction)
    model_mae = float(by_stock.model_mae_inr.mean())
    beating = int(by_stock.model_mae_inr.lt(by_stock.baseline_mae_inr).sum())
    ic = information_coefficients(valid, prediction)

    predictions = valid[KEY + ["target_date", "origin_raw_close", "target_raw_close",
                               "forward_raw_return"]].copy()
    predictions["predicted_return"] = prediction
    predictions["predicted_close_inr"] = (
        predictions.origin_raw_close.to_numpy(dtype=float) * (1 + prediction)
    )
    predictions["unchanged_price_inr"] = predictions.origin_raw_close
    predictions["model_absolute_error_inr"] = np.abs(
        predictions.predicted_close_inr - predictions.target_raw_close
    )
    predictions["baseline_absolute_error_inr"] = np.abs(
        predictions.unchanged_price_inr - predictions.target_raw_close
    )
    # The holdout is exploratory, but a losing candidate is never presented
    # as the forecast to use from this study.
    passed_validation_gate = bool(model_mae < baseline)
    predictions["forecast_close_inr"] = (
        predictions.predicted_close_inr if passed_validation_gate
        else predictions.unchanged_price_inr
    )
    metrics = {
        "status": "2025_validation_only", "test_2026_evaluated": False,
        "horizon_sessions": 5, "train_rows": len(train),
        "validation_rows": len(valid), "validation_stocks": valid.symbol.nunique(),
        "train_origin_latest": train.date.max().strftime("%Y-%m-%d"),
        "train_target_latest": train.target_date.max().strftime("%Y-%m-%d"),
        "validation_origin_earliest": valid.date.min().strftime("%Y-%m-%d"),
        "validation_target_latest": valid.target_date.max().strftime("%Y-%m-%d"),
        "split_bonus_crossing_rows_excluded": excluded,
        "primary_metric": "equal_stock_price_mae_inr",
        "unchanged_price_mae_inr": baseline,
        "selected_model_mae_inr": model_mae,
        "delta_vs_unchanged_inr": model_mae - baseline,
        "stocks_beating_unchanged_price": beating,
        "passed_exploratory_validation_gate": passed_validation_gate,
        "forecast_policy": selected["name"] if passed_validation_gate else "unchanged_price",
        "model_features": FEATURES,
        "selection_period": "2021-2024 expanding folds",
        "selection_metric": "equal_stock_price_mae_inr",
        "selection_included_unchanged_price": True,
        "selected": selected,
        "model_params": params,
        "sample_weight": "origin_raw_close / training rows per stock, normalized to mean 1",
        "feature_count": len(FEATURES),
        "new_feature_missing_counts_train": train[FEATURES].isna().sum().loc[lambda s: s.gt(0)].to_dict(),
        "new_feature_missing_counts_validation": valid[FEATURES].isna().sum().loc[lambda s: s.gt(0)].to_dict(),
        **ic,
    }
    return metrics, predictions, by_stock, search, model


def save_results(output_dir: Path, input_path: Path, metrics: dict,
                 predictions: pd.DataFrame, by_stock: pd.DataFrame,
                 search: dict, model) -> None:
    try:
        input_label = str(input_path.resolve().relative_to(ROOT))
    except ValueError:
        input_label = str(input_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    predictions.to_csv(output_dir / "validation_predictions.csv.gz", index=False,
                       compression={"method": "gzip", "mtime": 0},
                       date_format="%Y-%m-%d", float_format="%.12g")
    by_stock.to_csv(output_dir / "validation_by_stock.csv", index=False,
                    float_format="%.12g")
    (output_dir / "all_fold_scores.json").write_text(json.dumps(search, indent=2) + "\n")
    model_file = None
    if isinstance(model, LGBMRegressor):
        model_file = "selected_lightgbm.txt"
        model.booster_.save_model(str(output_dir / model_file))
    metrics = dict(metrics, input_feature_store=input_label,
                   input_sha256=sha256(input_path),
                   model_file=model_file,
                   generated_at_utc=datetime.now(timezone.utc).isoformat())
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    sources = [Path(__file__), ROOT / "src/nifty50_ta/five_day_model.py",
               ROOT / "scripts/build_model_features.py",
               ROOT / "src/nifty50_ta/feature_expansion.py",
               ROOT / "src/nifty50_ta/cross_sectional_expansion.py"]
    manifest = {
        "status": "exploratory_2025_validation_of_fold_selected_5d_model",
        "test_2026_evaluated_in_this_run": False,
        "earlier_2026_test_was_already_inspected": True,
        "feature_store_sha256": sha256(input_path),
        "data_release_sha256": sha256(ROOT / "docs/data-release-2026-10-15.json"),
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
        "library_versions": {name: version(name) for name in
                             ("lightgbm", "numpy", "pandas", "scikit-learn")},
        "model_file": model_file,
        "model_file_sha256": sha256(output_dir / model_file) if model_file else None,
        "forecast_policy": metrics["forecast_policy"],
        "feature_order": FEATURES if model_file else [],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def print_report(metrics: dict, output_dir: Path) -> None:
    print(f"5-day 2025 validation | train {metrics['train_rows']:,} rows | "
          f"score {metrics['validation_rows']:,} rows / {metrics['validation_stocks']} stocks")
    print("Model                         Equal-stock MAE (₹)  Delta vs unchanged (₹)  Stocks beating baseline")
    print(f"Unchanged price               {metrics['unchanged_price_mae_inr']:>19.8f}  "
          f"{0:>+22.8f}  —")
    print(f"Fold-selected {metrics['selected']['name']:<20} {metrics['selected_model_mae_inr']:>19.8f}  "
          f"{metrics['delta_vs_unchanged_inr']:>+22.8f}  "
          f"{metrics['stocks_beating_unchanged_price']}/{metrics['validation_stocks']}")
    print(f"Forecast policy after exploratory validation: {metrics['forecast_policy']}")
    print("2025 IC                 Within stock   Overall")
    def fmt(value):
        return "N/A" if value is None else f"{value:.4f}"
    print(f"Pearson                 {fmt(metrics['within_stock_pearson_ic']):>12}  "
          f"{fmt(metrics['overall_pearson_ic']):>8}")
    print(f"Spearman                {fmt(metrics['within_stock_spearman_ic']):>12}  "
          f"{fmt(metrics['overall_spearman_ic']):>8}")
    print(f"Saved predictions and metrics: {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=(
        ROOT / "reports/generated/price_forecasting_5d_2025" /
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ))
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output_dir}")
    input_path = ROOT / "data/processed/features_5d_corrected_pre2026.csv.gz"
    metrics, predictions, by_stock, search, model = evaluate(input_path)
    save_results(args.output_dir, input_path, metrics, predictions, by_stock, search, model)
    print_report(metrics, args.output_dir)


if __name__ == "__main__":
    main()
