"""Historical OHLCV predictors for the current price models."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nifty50_ta.model_common import KEY


CUTOFF = pd.Timestamp("2026-01-01")
OLD_FEATURES = (
    "daily_price_return", "price_vs_sma_20_pct", "price_vs_sma_50_pct",
    "macd_pct_close", "macd_histogram_pct_close", "rsi_14",
)
NEW_FEATURES = (
    "overnight_gap_return", "intraday_return", "close_location_value",
    "parkinson_volatility", "return_2d", "return_3d", "return_5d",
    "return_10d", "natr_14", "vol_20", "z_return_1d", "volume_ratio_20",
)
def expand_features(features: pd.DataFrame, *, allow_test_rows: bool = False,
                    legacy_raw_ohlcv: bool = False) -> pd.DataFrame:
    """Add historical features, with every lag and window reset by segment."""
    required = set(KEY) | {
        "raw_close", "adj_open", "adj_high", "adj_low", "adj_close",
        "adjustment_factor", "volume", "daily_price_return", "feature_ready",
    }
    if legacy_raw_ohlcv:
        required |= {"raw_open", "raw_high", "raw_low"}
    missing = required - set(features.columns)
    if missing:
        raise ValueError(f"Missing source feature columns: {sorted(missing)}")
    present = set(NEW_FEATURES) & set(features.columns)
    if present:
        raise ValueError(f"Expansion columns already exist: {sorted(present)}")
    if features[KEY].duplicated().any() or (
        not allow_test_rows and features.date.ge(CUTOFF).any()
    ):
        raise ValueError("Input must have unique keys and contain no 2026 rows")
    result = features.sort_values(["symbol", "date"]).copy()
    grouped = result.groupby("segment_id", sort=False)

    # The legacy switch reproduces the earlier 2026 result only. New model
    # development uses consistent OHLC/share units on split and bonus dates.
    price_basis = "raw" if legacy_raw_ohlcv else "adj"
    open_price = result[f"{price_basis}_open"]
    high_price = result[f"{price_basis}_high"]
    low_price = result[f"{price_basis}_low"]
    close_price = result[f"{price_basis}_close"]
    prior_close = close_price.groupby(result.segment_id, sort=False).shift(1)
    result["overnight_gap_return"] = (
        (open_price - prior_close) / prior_close
    )
    result["intraday_return"] = (
        (close_price - open_price) / open_price
    )
    spread = high_price - low_price
    result["close_location_value"] = (
        (close_price - low_price) - (high_price - close_price)
    ) / (spread + 1e-8)

    # sqrt(mean(log(high/low)^2) / (4 log 2)) over five trading observations.
    log_range_sq = np.log(high_price / low_price).pow(2)
    rolling_range = log_range_sq.groupby(result.segment_id, sort=False).transform(
        lambda values: values.rolling(5, min_periods=5).mean()
    )
    result["parkinson_volatility"] = np.sqrt(rolling_range / (4 * np.log(2)))

    for days in (2, 3, 5, 10):
        result[f"return_{days}d"] = (
            result.adj_close / grouped.adj_close.shift(days) - 1
        )

    true_range = pd.concat(
        [spread, (high_price - prior_close).abs(),
         (low_price - prior_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_14 = true_range.groupby(result.segment_id, sort=False).transform(
        lambda values: values.rolling(14, min_periods=14).mean()
    )
    result["natr_14"] = atr_14 / close_price
    result["vol_20"] = grouped.daily_price_return.transform(
        lambda values: values.rolling(20, min_periods=20).std(ddof=1)
    )
    result["z_return_1d"] = (
        result.daily_price_return / (result.vol_20 + 1e-8)
    )
    adjusted_volume = (result.volume if legacy_raw_ohlcv
                       else result.volume / result.adjustment_factor)
    mean_volume_20 = adjusted_volume.groupby(result.segment_id, sort=False).transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    result["volume_ratio_20"] = adjusted_volume / mean_volume_20

    if result.close_location_value.abs().gt(1 + 1e-8).any():
        raise ValueError("Close-location value exceeded [-1, 1]")
    ready = result.loc[result.feature_ready, list(NEW_FEATURES)]
    if not np.isfinite(ready.to_numpy(dtype=float)).all():
        raise ValueError("An originally feature-ready row has a non-finite new feature")
    return result.reset_index(drop=True)


def uniform_stock_weights(fit: pd.DataFrame) -> np.ndarray:
    stock_rows = fit.groupby("symbol").symbol.transform("size").to_numpy()
    weights = 1.0 / stock_rows
    return weights * (len(weights) / weights.sum())


def price_stock_weights(fit: pd.DataFrame) -> np.ndarray:
    """Match equal-stock rupee MAE when fitting a raw-return L1 model."""
    if not {"symbol", "origin_raw_close"}.issubset(fit.columns):
        raise ValueError("Price weights require a stock and origin quoted close")
    close = fit.origin_raw_close.to_numpy(dtype=float)
    if not np.isfinite(close).all() or np.any(close <= 0):
        raise ValueError("Origin quoted closes must be positive and finite")
    counts = fit.groupby("symbol").symbol.transform("size").to_numpy(dtype=float)
    weights = close / counts
    return weights * (len(weights) / weights.sum())
