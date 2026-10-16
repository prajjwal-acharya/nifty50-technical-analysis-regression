"""Check the 2025-only label boundary and equal-stock metric."""

import unittest

import numpy as np
import pandas as pd

from scripts.evaluate_5d_2025 import FEATURES, build_5d_table, fit_selected, stock_metrics


class EvaluateFiveDay2025Tests(unittest.TestCase):
    def test_zero_adjustment_uses_unchanged_price(self) -> None:
        valid = pd.DataFrame({"origin_raw_close": [100.0, 250.0]})
        model, predictions, params = fit_selected(pd.DataFrame(), valid,
                                                   {"name": "Unchanged price", "gamma": 0.0})
        self.assertIsNone(model)
        self.assertIsNone(params)
        np.testing.assert_array_equal(predictions, [0.0, 0.0])

    def test_train_and_validation_labels_stay_in_their_own_period(self) -> None:
        dates = pd.bdate_range("2024-12-16", "2025-01-15")
        rows = []
        for symbol in ("A", "B"):
            for i, date in enumerate(dates):
                row = {name: 1.0 for name in FEATURES}
                row.update(date=date, symbol=symbol, segment_id=f"{symbol}:0",
                           raw_close=100 + i, adj_close=100 + i,
                           adjustment_factor=1.0, feature_ready=True)
                rows.append(row)
        train, valid, excluded = build_5d_table(pd.DataFrame(rows))
        self.assertGreater(len(train), 0)
        self.assertGreater(len(valid), 0)
        self.assertEqual(excluded, 0)
        self.assertTrue(train.target_date.lt("2025-01-01").all())
        self.assertTrue(valid.date.ge("2025-01-01").all())
        self.assertTrue(valid.target_date.lt("2026-01-01").all())
        self.assertFalse(train.date.eq(pd.Timestamp("2024-12-31")).any())

    def test_equal_stock_mae_does_not_weight_longer_stock_more(self) -> None:
        valid = pd.DataFrame({
            "symbol": ["A", "A", "B"],
            "origin_raw_close": [100.0, 100.0, 100.0],
            "target_raw_close": [101.0, 101.0, 110.0],
        })
        stock = stock_metrics(valid, np.zeros(3))
        self.assertEqual(len(stock), 2)
        self.assertAlmostEqual(stock.model_mae_inr.mean(), 5.5)
        self.assertAlmostEqual(stock.baseline_mae_inr.mean(), 5.5)

    def test_rejects_a_2026_input_row(self) -> None:
        with self.assertRaisesRegex(ValueError, "2026"):
            build_5d_table(pd.DataFrame({"date": [pd.Timestamp("2026-01-01")]}))


if __name__ == "__main__":
    unittest.main()
