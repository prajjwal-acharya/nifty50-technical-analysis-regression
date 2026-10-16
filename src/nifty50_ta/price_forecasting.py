"""Quoted-price joins and rupee error metrics shared by both horizons."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nifty50_ta.model_common import KEY


def attach_quoted_prices(table: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Join actual origin/endpoint quotes and omit split/bonus crossings.

    Existing target tables already prevent demerger/rights segment crossings.
    A split/bonus crossing cannot be forecast as a raw quote without known
    future event information, so it is omitted from this model comparison.
    """
    needed = KEY + ["raw_close", "adjustment_factor"]
    if prices.duplicated(KEY).any():
        raise ValueError("Duplicate daily-price key")
    origin = prices[needed].rename(columns={
        "raw_close": "origin_raw_close", "adjustment_factor": "origin_factor",
    })
    endpoint = prices[needed].rename(columns={
        "date": "target_date", "raw_close": "target_raw_close",
        "adjustment_factor": "target_factor",
    })
    result = table.merge(origin, on=KEY, how="left", validate="one_to_one", indicator=True)
    if not result._merge.eq("both").all():
        raise ValueError("A forecast origin has no quoted close")
    result = result.drop(columns="_merge")
    result = result.merge(endpoint, on=["target_date", "symbol", "segment_id"],
                          how="left", validate="one_to_one", indicator=True)
    if not result._merge.eq("both").all():
        raise ValueError("A forecast endpoint has no quoted close")
    result = result.drop(columns="_merge")
    if not (result[["origin_raw_close", "target_raw_close"]] > 0).all().all():
        raise ValueError("Quoted close must be positive")
    same_share_basis = np.isclose(result.origin_factor, result.target_factor,
                                  rtol=1e-12, atol=1e-12)
    excluded = int((~same_share_basis).sum())
    result = result.loc[same_share_basis].copy()
    result["forward_raw_return"] = (
        result.target_raw_close / result.origin_raw_close - 1
    )
    if not np.isfinite(result.forward_raw_return).all():
        raise ValueError("Non-finite raw-price return")
    return result, excluded


def price_metrics(frame: pd.DataFrame, predicted_return: np.ndarray) -> dict:
    if len(frame) != len(predicted_return):
        raise ValueError("Prediction count does not match observations")
    predicted_price = frame.origin_raw_close.to_numpy() * (1 + predicted_return)
    actual_price = frame.target_raw_close.to_numpy()
    error = predicted_price - actual_price
    absolute_error = np.abs(error)
    per_stock = pd.DataFrame({
        "symbol": frame.symbol.to_numpy(), "absolute_error": absolute_error,
    }).groupby("symbol").absolute_error.mean()
    return {
        "rows": len(frame),
        "stocks": int(frame.symbol.nunique()),
        "row_price_mae_inr": float(absolute_error.mean()),
        "equal_stock_price_mae_inr": float(per_stock.mean()),
        "row_price_rmse_inr": float(np.sqrt(np.mean(error ** 2))),
        "row_absolute_percentage_error": float(
            np.mean(absolute_error / actual_price)
        ),
    }
