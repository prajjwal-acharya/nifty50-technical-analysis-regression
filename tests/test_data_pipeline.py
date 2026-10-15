"""Boundary checks for the price-processing contract, using synthetic data."""

import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

from nifty50_ta.data_pipeline import (
    RAW_COLUMNS, apply_actions, check_action_coverage, check_universe,
    create_features, create_targets, read_bse_prices, read_raw_prices,
    seeded_ema, wilder_rsi,
)
from nifty50_ta.verify_actions import factor_from_subject


class DataPipelineTests(unittest.TestCase):
    def test_full_action_search_rejects_omitted_bonus(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "nse_actions.json"
            symbols = {f"S{i}" for i in range(49)} | {"BEL"}
            snapshot = {"symbols": {
                symbol: {"url": "https://example.test", "records": ([{
                    "series": "EQ", "exDate": "28-Sep-2017", "subject": "Bonus 1:10",
                }] if symbol == "BEL" else [])} for symbol in symbols
            }}
            path.write_text(json.dumps(snapshot))
            actions = pd.DataFrame(columns=["symbol", "ex_date"])
            with self.assertRaisesRegex(ValueError, "omits NSE events"):
                check_action_coverage(actions, path, symbols)

    def test_bse_extraction_must_match_archived_raw_row(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "bse.csv"
            raw = {
                "SYMBOL": "BSE", "SERIES": "EQ", "TIMESTAMP": "03-FEB-2017",
                "OPEN": "1085", "HIGH": "1200", "LOW": "1065.1",
                "PREVCLOSE": "806", "CLOSE": "1069.2", "TOTTRDQTY": "15771762",
            }
            row = {
                "date": "2017-02-03", "symbol": "BSE", "series": "EQ",
                "open": 1085, "high": 1200, "low": 1065.1,
                "prev_close": 806, "close": 1069.2, "volume": 15771762,
                "source_url": "https://nsearchives.nseindia.com/example",
                "source_sha256": "a" * 64,
                "source_raw_row_json": json.dumps(raw),
            }
            pd.DataFrame([row]).to_csv(path, index=False)
            self.assertEqual(read_bse_prices(path).raw_close.iloc[0], 1069.2)
            row["close"] = 1100
            pd.DataFrame([row]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "numeric value differs"):
                read_bse_prices(path)

    def test_constituent_mismatch_is_explicit(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "universe.json"
            path.write_text(json.dumps({
                "as_of_date": "2026-10-15", "source_url": "https://example.test",
                "replacement_press_release_url": "https://example.test/replacement",
                "symbols": ["BSE", *[f"S{i}" for i in range(49)]],
            }))
            result = check_universe({"WIPRO", *[f"S{i}" for i in range(49)]}, path)
            self.assertFalse(result["matches_official_snapshot"])
            self.assertEqual(result["missing_from_processed"], ["BSE"])
            self.assertEqual(result["extra_in_processed"], ["WIPRO"])

    def test_series_filter_keeps_only_eq_and_initial_allowed_be(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "raw.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(RAW_COLUMNS)
            for symbol, day, series in [
                ("JIOFIN", 1, "BE"), ("JIOFIN", 2, "EQ"),
                ("OTHER", 1, "EQ"), ("OTHER", 1, "BL"),
            ]:
                row = [None] * len(RAW_COLUMNS)
                row[0] = f"{symbol}_Historical_example.xlsx"
                row[1] = pd.Timestamp(2024, 1, day).to_pydatetime()
                row[2] = series
                for index in (3, 4, 5, 6, 7, 8, 9):
                    row[index] = 100.0
                row[10] = 1000
                sheet.append(row)
            workbook.save(path)
            prices, counts = read_raw_prices(path, {"JIOFIN": 1})
            self.assertEqual(counts["raw_rows"], 4)
            self.assertEqual(counts["kept_rows"], 3)
            self.assertEqual(counts["excluded_series_counts"], {"BL": 1})
            self.assertEqual(prices.loc[prices.symbol.eq("JIOFIN"), "series"].tolist(), ["BE", "EQ"])

    def test_split_factor_stays_inside_demerger_segment(self) -> None:
        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        prices = pd.DataFrame({
            "date": dates, "symbol": ["EXAMPLE"] * 4,
            "raw_open": [100, 101, 60, 31], "raw_high": [101, 102, 61, 32],
            "raw_low": [99, 100, 59, 30], "raw_close": [100, 101, 60, 31],
        })
        actions = pd.DataFrame({
            "symbol": ["EXAMPLE", "EXAMPLE"],
            "ex_date": pd.to_datetime(["2024-01-04", "2024-01-05"]),
            "action_type": ["Demerger", "Split"],
            "split_bonus_factor": [np.nan, 0.5],
        })
        result = apply_actions(prices, actions)
        self.assertEqual(result.segment_number.tolist(), [0, 0, 1, 1])
        self.assertEqual(result.adjustment_factor.tolist(), [1, 1, 0.5, 1])
        self.assertEqual(result.adj_close.tolist(), [100, 101, 30, 31])

    def test_rights_event_creates_a_segment_boundary(self) -> None:
        prices = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]),
            "symbol": ["EXAMPLE"] * 4,
            "raw_open": [100, 101, 90, 46], "raw_high": [101, 102, 91, 47],
            "raw_low": [99, 100, 89, 45], "raw_close": [100, 101, 90, 46],
        })
        actions = pd.DataFrame({
            "symbol": ["EXAMPLE", "EXAMPLE"],
            "ex_date": pd.to_datetime(["2024-01-04", "2024-01-05"]),
            "action_type": ["Rights", "Bonus"],
            "split_bonus_factor": [np.nan, 0.5],
        })
        result = apply_actions(prices, actions)
        self.assertEqual(result.segment_number.tolist(), [0, 0, 1, 1])
        self.assertEqual(result.adjustment_factor.tolist(), [1, 1, 0.5, 1])

    def test_features_and_targets_do_not_cross_segments_or_date_splits(self) -> None:
        dates = pd.bdate_range("2024-10-01", periods=130)
        prices = pd.DataFrame({
            "date": dates, "symbol": ["EXAMPLE"] * 130,
            "segment_id": ["EXAMPLE:0"] * 65 + ["EXAMPLE:1"] * 65,
            "adj_close": np.arange(100, 230, dtype=float),
            "adj_open": np.arange(99, 229, dtype=float),
        })
        features = create_features(prices)
        self.assertTrue(pd.isna(features.loc[65, "daily_price_return"]))
        self.assertFalse(features.loc[65, "feature_ready"])
        self.assertTrue(features.loc[64, "feature_ready"])
        config = {
            "train_end": "2024-12-31", "validation_end": "2025-12-31",
            "test_end": "2026-12-31",
        }
        one = create_targets(features, 1, config)
        five = create_targets(features, 5, config)
        self.assertFalse((one.date == dates[64]).any())
        self.assertTrue((five.target_date > five.date).all())
        self.assertFalse(((five.date <= "2024-12-31") & (five.target_date > "2024-12-31")).any())
        self.assertFalse((five.date == dates[64]).any())
        self.assertFalse(((five.date < dates[65]) & (five.target_date >= dates[65])).any())
        self.assertTrue((five.date == dates[50]).any())
        row = five.loc[five.date.eq(dates[50])].iloc[0]
        self.assertAlmostEqual(row.forward_close_return, prices.loc[55, "adj_close"] / prices.loc[50, "adj_close"] - 1)
        self.assertAlmostEqual(row.reference_gross_executable_return,
                               prices.loc[55, "adj_close"] / prices.loc[51, "adj_open"] - 1)

    def test_flat_history_is_neutral_and_ema_waits_for_seed(self) -> None:
        flat = np.full(40, 100.0)
        self.assertTrue(np.isnan(seeded_ema(flat, 12)[:11]).all())
        self.assertTrue(np.isnan(wilder_rsi(flat)[:14]).all())
        self.assertTrue((wilder_rsi(flat)[14:] == 50).all())

    def test_nse_action_ratio_parsing(self) -> None:
        self.assertEqual(factor_from_subject("Bonus 4:1"), 0.2)
        self.assertEqual(factor_from_subject("Bonus 1:2"), 2 / 3)
        self.assertEqual(factor_from_subject(" Fv Splt Frm Rs 10 To Re 1"), 0.1)
        self.assertIsNone(factor_from_subject("Demerger"))


if __name__ == "__main__":
    unittest.main()
