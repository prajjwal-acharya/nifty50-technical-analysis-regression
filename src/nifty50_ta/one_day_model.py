"""Frozen 26-feature next-session LightGBM and quoted-price scoring."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from nifty50_ta.cross_sectional_expansion import CROSS_SECTIONAL_FEATURES
from nifty50_ta.feature_expansion import CUTOFF, NEW_FEATURES, OLD_FEATURES
from nifty50_ta.model_common import KEY
from nifty50_ta.price_forecasting import price_metrics


FEATURES = list(OLD_FEATURES + NEW_FEATURES + CROSS_SECTIONAL_FEATURES)
EXPECTED_VALIDATION_ROWS = 12245
EXPECTED_BASELINE_MAE = 27.11221751592158


def load_pre2026_features(path: Path) -> pd.DataFrame:
    """Accept a pre-2026-only input; never open any mixed test container."""
    frame = pd.read_csv(path, parse_dates=["date"], low_memory=False)
    required = set(KEY + FEATURES + [
        "adj_close", "raw_close", "adjustment_factor", "feature_ready",
    ])
    if missing := required - set(frame):
        raise ValueError(f"Missing feature columns: {sorted(missing)}")
    if frame.date.isna().any() or frame.date.ge(CUTOFF).any():
        raise ValueError("The supplied feature table must contain only pre-2026 dates")
    if frame[KEY].duplicated().any() or frame[["date", "symbol"]].duplicated().any():
        raise ValueError("Duplicate feature key or stock-date")
    if not frame.segment_id.str.split(":").str[0].eq(frame.symbol).all():
        raise ValueError("Segment ID does not match the stock symbol")
    return frame.sort_values(["symbol", "date"]).reset_index(drop=True)


def build_horizon_table(features: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, int]:
    """Reproduce same-split segment targets and raw-quote event exclusion."""
    if horizon != 1:
        raise ValueError(f"Unsupported horizon: {horizon}")
    if features.date.ge(CUTOFF).any():
        raise ValueError("2026 feature row supplied")
    table = features.copy()
    segment = table.groupby("segment_id", sort=False)
    table["target_date"] = segment.date.shift(-horizon)
    table["target_raw_close"] = segment.raw_close.shift(-horizon)
    table["target_adj_close"] = segment.adj_close.shift(-horizon)
    table["target_factor"] = segment.adjustment_factor.shift(-horizon)
    train_end = pd.Timestamp("2024-12-31")
    table["split"] = np.where(table.date.le(train_end), "train", "validation")
    target_split = np.where(table.target_date.le(train_end), "train", "validation")
    eligible = (
        table.feature_ready.eq(True) & table.target_date.notna()
        & table.target_adj_close.notna() & table.split.eq(target_split)
    )
    table = table.loc[eligible].copy()
    same_share_basis = np.isclose(
        table.adjustment_factor, table.target_factor, rtol=1e-12, atol=1e-12,
    )
    excluded = int((~same_share_basis).sum())
    table = table.loc[same_share_basis].copy()
    if table.target_date.ge(CUTOFF).any():
        raise ValueError("Target crossed the pre-2026 boundary")
    if not (table[["raw_close", "target_raw_close"]] > 0).all().all():
        raise ValueError("Raw closes must be positive")
    table = table.rename(columns={"raw_close": "origin_raw_close"})
    table["forward_raw_return"] = (
        table.target_raw_close / table.origin_raw_close - 1.0
    )
    table = table.sort_values(KEY).reset_index(drop=True)
    if not np.isfinite(table[FEATURES + ["forward_raw_return"]].to_numpy(dtype=float)).all():
        raise ValueError("Non-finite feature or label in eligible forecast table")
    validation = table.loc[table.split.eq("validation")]
    if len(validation) != EXPECTED_VALIDATION_ROWS:
        raise ValueError(f"Unexpected {horizon}d validation count: {len(validation)}")
    baseline = price_metrics(validation, np.zeros(len(validation)))[
        "equal_stock_price_mae_inr"
    ]
    if not np.isclose(baseline, EXPECTED_BASELINE_MAE, atol=1e-8):
        raise ValueError(f"Unexpected {horizon}d baseline MAE: {baseline}")
    return table, excluded


def constrained_lgbm() -> LGBMRegressor:
    return LGBMRegressor(
        objective="regression_l1", learning_rate=0.01, n_estimators=60,
        num_leaves=7, max_depth=3, min_child_samples=500,
        subsample=0.7, subsample_freq=1, colsample_bytree=0.8,
        reg_lambda=50.0, random_state=42, verbosity=-1, n_jobs=4,
    )


def information_coefficients(frame: pd.DataFrame, prediction: np.ndarray) -> dict:
    if np.all(prediction == prediction[0]):
        return dict.fromkeys(("within_pearson", "within_spearman",
                              "overall_pearson", "overall_spearman"))
    paired = pd.DataFrame({
        "symbol": frame.symbol.to_numpy(),
        "predicted": prediction,
        "actual": frame.forward_raw_return.to_numpy(),
    })
    within = paired.groupby("symbol").apply(
        lambda stock: pd.Series({
            "pearson": stock.predicted.corr(stock.actual, method="pearson"),
            "spearman": stock.predicted.corr(stock.actual, method="spearman"),
        }), include_groups=False,
    )
    return {
        "within_pearson": float(within.pearson.mean()),
        "within_spearman": float(within.spearman.mean()),
        "overall_pearson": float(paired.predicted.corr(paired.actual, method="pearson")),
        "overall_spearman": float(paired.predicted.corr(paired.actual, method="spearman")),
    }


def score_validation(frame: pd.DataFrame, predicted_return: np.ndarray) -> dict:
    metrics = price_metrics(frame, predicted_return)
    error = np.abs(frame.origin_raw_close.to_numpy() * (1 + predicted_return)
                   - frame.target_raw_close.to_numpy())
    baseline_error = np.abs(frame.origin_raw_close - frame.target_raw_close)
    stocks = pd.DataFrame({
        "symbol": frame.symbol.to_numpy(), "mae": error,
        "baseline_mae": baseline_error.to_numpy(),
    }).groupby("symbol").mean()
    return {
        "mae": metrics["equal_stock_price_mae_inr"],
        "stocks_beating_baseline": int(stocks.mae.lt(stocks.baseline_mae).sum()),
        "stocks": stocks,
        "ic": information_coefficients(frame, predicted_return),
    }
