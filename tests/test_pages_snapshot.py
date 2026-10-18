"""The public projection must be gated and honor both forecast contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import Calendar
from scripts.build_pages_snapshot import build_snapshot


CALENDAR = ROOT / "config/nse_equity_calendar_2026.json"


class PagesSnapshotTests(unittest.TestCase):
    def test_default_does_not_open_or_export_private_store(self) -> None:
        payload = build_snapshot(Path("/nonexistent-private-store"), CALENDAR)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["stocks"], [])

    def test_projection_checks_targets_and_unchanged_fifth_policy(self) -> None:
        origin = date(2026, 10, 16)
        symbols = [f"S{i:02}" for i in range(50)]
        frame = pd.DataFrame({"date": pd.Timestamp(origin), "symbol": symbols,
                              "raw_close": [100.0] * 50})
        forecast = {"origin_session": origin.isoformat(), "issue_kind": "prospective",
                    "model_sha256": "model-hash", "calendar_sha256": Calendar(CALENDAR).sha256,
                    "issued_at_utc": "2026-10-18T04:45:00+00:00", "forecasts": []}
        for symbol in symbols:
            forecast["forecasts"].extend([
                {"symbol": symbol, "horizon": 1, "origin_close": 100.0,
                 "target_session": "2026-10-19", "policy": "frozen_one_day_lightgbm",
                 "status": "issued", "forecast_close": 101.0},
                {"symbol": symbol, "horizon": 5, "origin_close": 100.0,
                 "target_session": "2026-10-26", "policy": "unchanged_close",
                 "status": "issued", "forecast_close": 100.0},
            ])
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            config = state / "baseline/config"
            config.mkdir(parents=True)
            (config / "nifty50_universe.json").write_text(json.dumps({
                "symbols": symbols, "as_of_date": "2026-10-15"}))
            output = state / "forecasts/2026/10/2026-10-16.json"
            output.parent.mkdir(parents=True)
            output.write_text(json.dumps(forecast))
            with (patch("scripts.build_pages_snapshot.verify", return_value={"one_day_model_sha256": "model-hash"}),
                  patch("scripts.build_pages_snapshot.pd.read_csv", return_value=frame),
                  patch("scripts.build_pages_snapshot.read_sessions", return_value=(frame, [origin]))):
                payload = build_snapshot(state, CALENDAR, publication_authorized=True,
                                         now=datetime.fromisoformat("2026-10-18T04:50:00+00:00"))
                self.assertEqual(payload["status"], "current")
                self.assertEqual(len(payload["stocks"]), 50)
                self.assertEqual(payload["stocks"][0]["horizons"]["1"]["target_session"], "2026-10-19")
                self.assertEqual(payload["stocks"][0]["horizons"]["5"]["forecast_close"], 100.0)
                self.assertNotIn("model_sha256", payload)
                self.assertNotIn("reports", payload)
                stale = build_snapshot(state, CALENDAR, publication_authorized=True,
                                       now=datetime.fromisoformat("2026-10-19T04:00:00+00:00"))
                self.assertEqual(stale["status"], "resolved_or_stale")
                forecast["forecasts"][1]["forecast_close"] = 101.0
                output.write_text(json.dumps(forecast))
                with self.assertRaisesRegex(ValueError, "unchanged close"):
                    build_snapshot(state, CALENDAR, publication_authorized=True,
                                   now=datetime.fromisoformat("2026-10-18T04:50:00+00:00"))
                forecast["forecasts"][1]["forecast_close"] = 100.0
                forecast["forecasts"].append(forecast["forecasts"][0])
                output.write_text(json.dumps(forecast))
                with self.assertRaisesRegex(ValueError, "cover both horizons"):
                    build_snapshot(state, CALENDAR, publication_authorized=True,
                                   now=datetime.fromisoformat("2026-10-18T04:50:00+00:00"))


if __name__ == "__main__":
    unittest.main()
