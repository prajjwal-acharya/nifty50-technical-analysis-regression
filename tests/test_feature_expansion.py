"""Boundary and formula checks for the pre-2026 feature expansion."""

import unittest

import numpy as np
import pandas as pd

from nifty50_ta.feature_expansion import NEW_FEATURES, expand_features, price_stock_weights


class FeatureExpansionTests(unittest.TestCase):
    def test_return_loss_weights_match_equal_stock_rupee_error(self) -> None:
        fit = pd.DataFrame({
            "symbol": ["A", "A", "B"],
            "origin_raw_close": [100.0, 200.0, 50.0],
        })
        np.testing.assert_allclose(price_stock_weights(fit), [0.75, 1.5, 0.75])

    def test_lags_and_windows_restart_at_segment_boundary(self) -> None:
        rows = []
        for segment, start in (("ABC:0", 100.0), ("ABC:1", 300.0)):
            for day in range(55):
                close = start + day
                rows.append({
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(
                        days=(0 if segment == "ABC:0" else 60) + day),
                    "symbol": "ABC", "segment_id": segment,
                    "raw_open": close - 1, "raw_high": close + 2,
                    "raw_low": close - 2, "raw_close": close,
                    "adj_open": close - 1, "adj_high": close + 2,
                    "adj_low": close - 2, "adj_close": close,
                    "adjustment_factor": 1.0, "volume": 100 + day,
                    "daily_price_return": np.nan if day == 0 else close / (close - 1) - 1,
                    "feature_ready": day >= 49,
                })
        expanded = expand_features(pd.DataFrame(rows))
        second = expanded.loc[expanded.segment_id.eq("ABC:1")].reset_index(drop=True)
        self.assertTrue(np.isnan(second.loc[0, "overnight_gap_return"]))
        self.assertTrue(np.isnan(second.loc[0, "return_2d"]))
        self.assertTrue(np.isnan(second.loc[0, "return_10d"]))
        self.assertFalse(np.isnan(second.loc[4, "parkinson_volatility"]))
        self.assertTrue(np.isnan(second.loc[3, "parkinson_volatility"]))
        self.assertTrue(np.isnan(second.loc[12, "natr_14"]))
        self.assertTrue(np.isnan(second.loc[18, "volume_ratio_20"]))
        self.assertTrue(np.isnan(second.loc[19, "vol_20"]))
        self.assertAlmostEqual(second.loc[10, "return_10d"], 310 / 300 - 1)
        self.assertAlmostEqual(second.loc[0, "intraday_return"], 1 / 299)
        self.assertAlmostEqual(second.loc[0, "close_location_value"], 0.0)
        self.assertTrue(np.isfinite(second.loc[49, list(NEW_FEATURES)].to_numpy(dtype=float)).all())

    def test_split_day_uses_a_consistent_share_basis(self) -> None:
        rows = []
        for i in range(55):
            factor = 0.1 if i < 50 else 1.0
            raw = 1000.0 if i < 50 else 100.0
            rows.append({
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                "symbol": "ABC", "segment_id": "ABC:0",
                "raw_close": raw, "raw_open": raw, "raw_high": raw * 1.01,
                "raw_low": raw * .99, "adj_close": raw * factor,
                "adj_open": raw * factor, "adj_high": raw * factor * 1.01,
                "adj_low": raw * factor * .99,
                "adjustment_factor": factor,
                "volume": 100 if i < 50 else 1000,
                "daily_price_return": np.nan if i == 0 else 0.0,
                "feature_ready": i >= 49,
            })
        frame = expand_features(pd.DataFrame(rows))
        split = frame.iloc[50]
        self.assertAlmostEqual(split.overnight_gap_return, 0.0)
        self.assertLess(split.natr_14, 0.05)
        self.assertAlmostEqual(split.volume_ratio_20, 1.0)


if __name__ == "__main__":
    unittest.main()
