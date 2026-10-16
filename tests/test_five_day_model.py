"""Guard segment resets and future-prefix invariance."""

import unittest

import numpy as np
import pandas as pd

from nifty50_ta.five_day_model import engineer_features


class FiveDayFeatureTests(unittest.TestCase):
    def test_windows_reset_at_segment_and_use_only_history(self) -> None:
        dates = pd.bdate_range("2024-01-01", "2024-02-02")
        rows = []
        for i, date in enumerate(dates):
            segment = "A:0" if i < 21 else "A:1"
            rows.append({
                "date": date, "symbol": "A", "segment_id": segment,
                "sma_20": 100 + i, "sma_50": 200 + i,
                "daily_price_return": (-1) ** i * 0.01,
                "vol_20": 0.02, "mkt_dispersion_1d": 0.01 + i / 1000,
                "raw_close": 100.0, "adjustment_factor": 1.0,
                "feature_ready": True,
            })
        # The function needs the existing 26 predictor names, but their
        # numerical values do not affect these five checks.
        from nifty50_ta.five_day_model import EXISTING_FEATURES
        frame = pd.DataFrame(rows)
        for column in EXISTING_FEATURES:
            if column not in frame:
                frame[column] = 1.0
        result = engineer_features(frame).set_index("date")
        self.assertAlmostEqual(result.loc[pd.Timestamp("2024-01-08"), "sma_slope_5d"], 5 / 100)
        self.assertAlmostEqual(result.loc[pd.Timestamp("2024-01-29"), "sma_slope_20"], 20 / 200)
        self.assertTrue(np.isnan(result.loc[dates[21], "sma_slope_5d"]))
        self.assertTrue(np.isfinite(result.loc[dates[21], "mkt_dispersion_5d"]))
        self.assertNotIn("days_to_monthly_expiry", result.columns)
        prefix = engineer_features(frame.loc[frame.date.le("2024-01-24")])
        left = prefix.loc[prefix.date.eq("2024-01-24"), "vol_ratio_5_20"].iloc[0]
        right = result.loc[pd.Timestamp("2024-01-24"), "vol_ratio_5_20"]
        self.assertAlmostEqual(left, right)

    def test_rejects_2026_row(self) -> None:
        from nifty50_ta.five_day_model import EXISTING_FEATURES
        row = {name: 1.0 for name in EXISTING_FEATURES}
        row.update(date=pd.Timestamp("2026-01-01"), symbol="A", segment_id="A:0",
                   sma_20=100.0, sma_50=100.0, vol_20=0.01,
                   raw_close=100.0, adjustment_factor=1.0, feature_ready=True)
        with self.assertRaisesRegex(ValueError, "pre-2026"):
            engineer_features(pd.DataFrame([row]))
if __name__ == "__main__":
    unittest.main()
