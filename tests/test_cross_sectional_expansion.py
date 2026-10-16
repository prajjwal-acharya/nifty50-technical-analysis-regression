"""Checks that market features use only stocks on the same date."""

import unittest

import numpy as np
import pandas as pd

from nifty50_ta.cross_sectional_expansion import expand_cross_sectional_features


class CrossSectionalExpansionTests(unittest.TestCase):
    def test_medians_residuals_and_zero_to_one_ranks(self) -> None:
        rows = []
        values = {
            "2024-01-02": [
                ("A", -0.1, 0.05, 30, 2.0),
                ("B", 0.0, 0.10, 50, 1.0),
                ("C", 0.2, 0.15, 70, 0.5),
            ],
            "2024-01-03": [
                ("A", 0.2, 0.20, 40, 1.0),
                ("B", 0.2, 0.30, 60, 2.0),
                ("C", 0.0, 0.10, 20, 3.0),
            ],
        }
        for day, stocks in values.items():
            for symbol, daily, five, rsi, volume_ratio in stocks:
                rows.append({
                    "date": pd.Timestamp(day), "symbol": symbol,
                    "segment_id": f"{symbol}:0", "daily_price_return": daily,
                    "return_5d": five, "rsi_14": rsi,
                    "volume_ratio_20": volume_ratio, "feature_ready": True,
                })
        result = expand_cross_sectional_features(pd.DataFrame(rows))
        first = result.loc[result.date.eq(pd.Timestamp("2024-01-02"))].set_index("symbol")
        second = result.loc[result.date.eq(pd.Timestamp("2024-01-03"))].set_index("symbol")
        self.assertAlmostEqual(first.loc["A", "mkt_return_1d"], 0.0)
        self.assertAlmostEqual(first.loc["A", "mkt_return_5d"], 0.1)
        self.assertAlmostEqual(first.loc["A", "mkt_dispersion_1d"],
                               np.std([-0.1, 0, 0.2], ddof=1))
        self.assertAlmostEqual(first.loc["A", "residual_return_1d"], -0.1)
        self.assertAlmostEqual(first.loc["A", "residual_return_5d"], -0.05)
        self.assertEqual(first.cs_rank_return_1d.tolist(), [0.0, 0.5, 1.0])
        self.assertEqual(first.cs_rank_rsi_14.tolist(), [0.0, 0.5, 1.0])
        self.assertEqual(first.cs_rank_volume_ratio.tolist(), [1.0, 0.5, 0.0])
        self.assertAlmostEqual(second.loc["A", "mkt_return_1d"], 0.2)
        self.assertAlmostEqual(second.loc["A", "cs_rank_return_1d"], 0.75)
        self.assertAlmostEqual(second.loc["B", "cs_rank_return_1d"], 0.75)
        self.assertAlmostEqual(second.loc["C", "cs_rank_return_1d"], 0.0)

    def test_rejects_2026_rows(self) -> None:
        frame = pd.DataFrame({
            "date": [pd.Timestamp("2026-01-02")], "symbol": ["A"],
            "segment_id": ["A:0"], "daily_price_return": [0.01],
            "return_5d": [0.02], "rsi_14": [50.0],
            "volume_ratio_20": [1.0], "feature_ready": [True],
        })
        with self.assertRaisesRegex(ValueError, "2026"):
            expand_cross_sectional_features(frame)


if __name__ == "__main__":
    unittest.main()
