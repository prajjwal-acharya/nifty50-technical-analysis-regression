"""Corrected fifth-session features and LightGBM fold selection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from nifty50_ta.cross_sectional_expansion import CROSS_SECTIONAL_FEATURES
from nifty50_ta.feature_expansion import CUTOFF, NEW_FEATURES, OLD_FEATURES, price_stock_weights
from nifty50_ta.model_common import KEY, expanding_folds


EXISTING_FEATURES = OLD_FEATURES + NEW_FEATURES + CROSS_SECTIONAL_FEATURES
FIVE_DAY_FEATURES = (
    "sma_slope_5d", "sma_slope_20", "vol_ratio_5_20",
    "mkt_dispersion_5d",
)
MODEL_FEATURES = EXISTING_FEATURES + FIVE_DAY_FEATURES
GAMMAS = (0.0, 0.25, 0.50, 0.75, 1.0)
FOLD_YEARS = (2021, 2022, 2023, 2024)


def engineer_features(features: pd.DataFrame, *, allow_test_rows: bool = False) -> pd.DataFrame:
    """Add four historical features without consulting future calendar rows."""
    required = set(KEY + list(EXISTING_FEATURES)) | {
        "sma_20", "sma_50", "vol_20", "daily_price_return",
        "mkt_dispersion_1d", "raw_close", "adjustment_factor", "feature_ready",
    }
    if missing := required - set(features):
        raise ValueError(f"Missing source columns: {sorted(missing)}")
    if present := set(FIVE_DAY_FEATURES) & set(features):
        raise ValueError(f"Optimized columns already exist: {sorted(present)}")
    if features.date.isna().any() or (not allow_test_rows and features.date.ge(CUTOFF).any()):
        raise ValueError("Input must contain only pre-2026 dates")
    if features[KEY].isna().any().any() or features[KEY].duplicated().any():
        raise ValueError("Missing or duplicate feature key")
    if features[["date", "symbol"]].duplicated().any():
        raise ValueError("Duplicate stock-date")
    if not features.segment_id.str.split(":").str[0].eq(features.symbol).all():
        raise ValueError("Segment ID does not match the stock symbol")

    result = features.sort_values(["symbol", "date"]).copy()
    grouped = result.groupby("segment_id", sort=False)
    sma_20_prior = grouped.sma_20.shift(5).replace(0, np.nan)
    sma_50_prior = grouped.sma_50.shift(20).replace(0, np.nan)
    result["sma_slope_5d"] = (result.sma_20 - sma_20_prior) / sma_20_prior
    result["sma_slope_20"] = (result.sma_50 - sma_50_prior) / sma_50_prior
    vol_5 = grouped.daily_price_return.transform(
        lambda values: values.rolling(5, min_periods=5).std(ddof=1)
    ).replace(0, np.nan)
    result["vol_ratio_5_20"] = vol_5 / result.vol_20.replace(0, np.nan)
    # Market dispersion is date-wide and should not reset because one stock
    # enters a new corporate-action segment.
    daily_dispersion = result.groupby("date").mkt_dispersion_1d.first().sort_index()
    result["mkt_dispersion_5d"] = result.date.map(
        daily_dispersion.rolling(5, min_periods=5).mean()
    )

    values = result[list(FIVE_DAY_FEATURES)].to_numpy(dtype=float)
    if np.isinf(values).any():
        raise ValueError("Infinite optimized feature")
    if len(MODEL_FEATURES) != 30 or len(set(MODEL_FEATURES)) != 30:
        raise ValueError("Expected exactly 30 distinct model features")
    return result.reset_index(drop=True)


def write_feature_store(features: pd.DataFrame, output: Path) -> None:
    """Create the expanded store, or verify an existing store is identical."""
    if output.exists():
        existing = pd.read_csv(output, parse_dates=["date"], low_memory=False)
        pd.testing.assert_frame_equal(
            existing, features, check_dtype=False, check_exact=False,
            rtol=1e-10, atol=1e-12,
        )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(
        output, index=False, compression={"method": "gzip", "mtime": 0},
        date_format="%Y-%m-%d", float_format="%.12g",
    )


def rupee_mae(frame: pd.DataFrame, predicted_return: np.ndarray) -> float:
    errors = np.abs(
        frame.origin_raw_close.to_numpy(dtype=float) * (1 + predicted_return)
        - frame.target_raw_close.to_numpy(dtype=float)
    )
    return float(pd.DataFrame({"symbol": frame.symbol.to_numpy(), "error": errors})
                 .groupby("symbol").error.mean().mean())


def summarize_grid(table: pd.DataFrame) -> dict:
    """Select the corrected model on pre-2025 chronological folds."""
    folds = expanding_folds(table, list(FOLD_YEARS))
    baseline_folds = [rupee_mae(score, np.zeros(len(score))) for _, _, score in folds]
    candidates: list[dict] = []

    # Frozen two-setting comparison for the corrected fifth-session candidate.
    settings = ((60, 7, 3, 500), (150, 7, 3, 500))
    for n_estimators, num_leaves, max_depth, min_child_samples in settings:
        params = dict(n_estimators=n_estimators, num_leaves=num_leaves,
                      max_depth=max_depth, min_child_samples=min_child_samples)
        predictions = []
        for _, fit, score in folds:
            model = LGBMRegressor(
                objective="regression_l1", learning_rate=0.01,
                reg_lambda=50.0, subsample=0.7, subsample_freq=1,
                colsample_bytree=0.8, random_state=42, verbosity=-1,
                n_jobs=4, **params,
            )
            model.fit(fit[list(MODEL_FEATURES)], fit.forward_raw_return,
                      sample_weight=price_stock_weights(fit))
            prediction = np.asarray(model.predict(score[list(MODEL_FEATURES)]), dtype=float)
            if not np.isfinite(prediction).all():
                raise ValueError(f"Non-finite LightGBM prediction for {params}")
            predictions.append(prediction)
        for gamma in GAMMAS:
            scores = [
                rupee_mae(score, gamma * prediction)
                for (_, _, score), prediction in zip(folds, predictions)
            ]
            candidates.append({"name": "Corrected LightGBM 30", "params": params,
                               "gamma": gamma, "folds": scores,
                               "mean": float(np.mean(scores))})

    def best(name: str) -> dict:
        return min((item for item in candidates if item["name"] == name),
                   key=lambda item: (item["mean"], item["gamma"],
                                     item.get("alpha") or 0,
                                     tuple(item.get("params", {}).values())))

    return {"baseline_folds": baseline_folds, "fold_years": FOLD_YEARS,
            "fold_sizes": [(len(fit), len(score)) for _, fit, score in folds],
            "all_candidates": candidates,
            "best": [best("Corrected LightGBM 30")]}
