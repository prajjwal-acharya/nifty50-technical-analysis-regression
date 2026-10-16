"""Checks for the quoted-price target and rupee-error objective."""

import unittest

import numpy as np
import pandas as pd

from nifty50_ta.price_forecasting import attach_quoted_prices, price_metrics


class PriceForecastingTests(unittest.TestCase):
    def test_raw_target_and_event_crossing_policy(self) -> None:
        table = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-01", "2025-01-02"]),
            "target_date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "symbol": ["ABC", "ABC"],
            "segment_id": ["ABC:0", "ABC:0"],
        })
        prices = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
            "symbol": ["ABC"] * 3,
            "segment_id": ["ABC:0"] * 3,
            "raw_close": [200.0, 210.0, 105.0],
            "adjustment_factor": [0.5, 0.5, 1.0],
        })
        result, excluded = attach_quoted_prices(table, prices)
        self.assertEqual(excluded, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.target_raw_close.iloc[0], 210.0)
        self.assertAlmostEqual(result.forward_raw_return.iloc[0], 0.05)

    def test_price_error_uses_rupees_not_return_error(self) -> None:
        frame = pd.DataFrame({
            "symbol": ["A", "B"],
            "origin_raw_close": [100.0, 1000.0],
            "target_raw_close": [102.0, 1020.0],
        })
        metrics = price_metrics(frame, np.zeros(2))
        self.assertEqual(metrics["row_price_mae_inr"], 11.0)
        self.assertEqual(metrics["equal_stock_price_mae_inr"], 11.0)
        self.assertAlmostEqual(metrics["row_absolute_percentage_error"],
                               (2 / 102 + 20 / 1020) / 2)


if __name__ == "__main__":
    unittest.main()
