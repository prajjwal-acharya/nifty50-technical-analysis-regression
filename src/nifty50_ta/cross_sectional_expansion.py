"""Same-session fixed-basket features for the current price models."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nifty50_ta.feature_expansion import CUTOFF
from nifty50_ta.model_common import KEY


CROSS_SECTIONAL_FEATURES = (
    "mkt_return_1d", "mkt_return_5d", "mkt_dispersion_1d",
    "residual_return_1d", "residual_return_5d", "cs_rank_return_1d",
    "cs_rank_rsi_14", "cs_rank_volume_ratio",
)


def expand_cross_sectional_features(
    features: pd.DataFrame, *, allow_test_rows: bool = False,
) -> pd.DataFrame:
    """Use only contemporaneous rows sharing each trading date."""
    required = set(KEY) | {
        "daily_price_return", "return_5d", "rsi_14", "volume_ratio_20",
        "feature_ready",
    }
    missing = required - set(features.columns)
    if missing:
        raise ValueError(f"Missing source columns: {sorted(missing)}")
    present = set(CROSS_SECTIONAL_FEATURES) & set(features.columns)
    if present:
        raise ValueError(f"Cross-sectional columns already exist: {sorted(present)}")
    if features[["date", "symbol"]].duplicated().any() or (
        not allow_test_rows and features.date.ge(CUTOFF).any()
    ):
        raise ValueError("Input must have unique stock-dates and contain no 2026 rows")

    result = features.copy()
    day = result.groupby("date", sort=False)
    result["mkt_return_1d"] = day.daily_price_return.transform("median")
    result["mkt_return_5d"] = day.return_5d.transform("median")
    result["mkt_dispersion_1d"] = day.daily_price_return.transform("std")
    result["residual_return_1d"] = (
        result.daily_price_return - result.mkt_return_1d
    )
    result["residual_return_5d"] = result.return_5d - result.mkt_return_5d

    for source, name in (
        ("daily_price_return", "cs_rank_return_1d"),
        ("rsi_14", "cs_rank_rsi_14"),
        ("volume_ratio_20", "cs_rank_volume_ratio"),
    ):
        ranks = day[source].rank(method="average")
        counts = day[source].transform("count")
        # A singleton gets the midpoint; missing source values remain missing.
        result[name] = np.where(
            counts.gt(1), (ranks - 1) / (counts - 1),
            np.where(counts.eq(1) & ranks.notna(), 0.5, np.nan),
        )

    rank_values = result[list(CROSS_SECTIONAL_FEATURES[-3:])]
    if rank_values.lt(0).any().any() or rank_values.gt(1).any().any():
        raise ValueError("Cross-sectional rank fell outside [0, 1]")
    ready = result.loc[result.feature_ready, list(CROSS_SECTIONAL_FEATURES)]
    if not np.isfinite(ready.to_numpy(dtype=float)).all():
        raise ValueError("A feature-ready row has a non-finite cross-sectional feature")
    return result
