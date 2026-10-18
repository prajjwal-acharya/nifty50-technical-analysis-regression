"""Exercise the Git issue boundary and equal-stock prospective metric."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import Calendar, path_for
from scripts.prospective_performance import build_report, committed_issue, summarize


CALENDAR = Calendar(ROOT / "config/nse_equity_calendar_2026.json")


class ProspectivePerformanceTests(unittest.TestCase):
    def commit_forecast(self, state: Path, commit_time: str) -> tuple[Path, dict]:
        subprocess.run(["git", "-C", str(state), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(state), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(state), "config", "user.email", "test@example.invalid"], check=True)
        path = path_for(state, "forecasts", date(2026, 10, 16))
        path.parent.mkdir(parents=True)
        forecast = {"origin_session": "2026-10-16", "issue_kind": "prospective",
                    "issued_at_utc": "2026-10-18T04:46:32+00:00"}
        path.write_text(json.dumps(forecast) + "\n")
        subprocess.run(["git", "-C", str(state), "add", "forecasts"], check=True)
        environment = {**os.environ, "GIT_AUTHOR_DATE": commit_time,
                       "GIT_COMMITTER_DATE": commit_time}
        subprocess.run(["git", "-C", str(state), "commit", "-qm", "Issue forecast"],
                       check=True, env=environment)
        return path, forecast

    def test_original_bytes_must_be_committed_before_next_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path, forecast = self.commit_forecast(state, "2026-10-18T04:47:00+00:00")
            self.assertEqual(len(committed_issue(state, path, forecast, CALENDAR)), 40)
            path.write_text(json.dumps({**forecast, "origin_session": "2026-10-15"}))
            with self.assertRaisesRegex(ValueError, "differ from their original"):
                committed_issue(state, path, forecast, CALENDAR)

    def test_late_commit_cannot_enter_performance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path, forecast = self.commit_forecast(state, "2026-10-19T03:31:00+00:00")
            with self.assertRaisesRegex(ValueError, "not committed inside"):
                committed_issue(state, path, forecast, CALENDAR)

    def test_equal_stock_metric_and_empty_status(self) -> None:
        self.assertEqual(summarize([])["status"], "awaiting_verified_targets")
        rows = [
            {"symbol": "A", "origin_session": "2026-10-16", "target_session": "2026-10-19",
             "absolute_error": 1.0, "unchanged_close_error": 2.0},
            {"symbol": "A", "origin_session": "2026-10-19", "target_session": "2026-10-21",
             "absolute_error": 3.0, "unchanged_close_error": 4.0},
            {"symbol": "B", "origin_session": "2026-10-16", "target_session": "2026-10-19",
             "absolute_error": 6.0, "unchanged_close_error": 2.0},
        ]
        result = summarize(rows)
        self.assertEqual(result["equal_stock_mae_inr"], 4.0)
        self.assertEqual(result["unchanged_close_mae_inr"], 2.5)
        self.assertEqual(result["delta_vs_unchanged_inr"], 1.5)

    def test_report_checks_realized_outcomes_against_verified_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            forecast_path, forecast = self.commit_forecast(state, "2026-10-18T04:47:00+00:00")
            symbols = {f"S{i:02}" for i in range(50)}
            (state / "baseline/config").mkdir(parents=True)
            (state / "baseline/config/nifty50_universe.json").write_text(
                json.dumps({"symbols": sorted(symbols)}))
            forecast.update(model_sha256="model", calendar_sha256=CALENDAR.sha256,
                            forecasts=[
                                {"symbol": symbol, "horizon": horizon, "status": "issued",
                                 "target_session": "2026-10-19" if horizon == 1 else "2026-10-26",
                                 "origin_close": 100.0, "forecast_close": 101.0}
                                for symbol in sorted(symbols) for horizon in (1, 5)])
            forecast_path.write_text(json.dumps(forecast) + "\n")
            subprocess.run(["git", "-C", str(state), "add", "forecasts"], check=True)
            environment = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-18T04:48:00+00:00",
                           "GIT_COMMITTER_DATE": "2026-10-18T04:48:00+00:00"}
            subprocess.run(["git", "-C", str(state), "commit", "-qm", "Complete forecast"],
                           check=True, env=environment)
            # The original Git addition remains incomplete, so this must fail.
            origin_prices = pd.DataFrame({"date": pd.to_datetime(["2026-10-16"]),
                                          "symbol": ["S00"], "raw_close": [100.0]})
            with (patch("scripts.prospective_performance.verify", return_value={"one_day_model_sha256": "model"}),
                  patch("scripts.prospective_performance.pd.read_csv", return_value=pd.DataFrame()),
                  patch("scripts.prospective_performance.read_sessions", return_value=(origin_prices, []))):
                with self.assertRaisesRegex(ValueError, "differ from their original"):
                    build_report(state, CALENDAR.path)

            # A complete forecast first added at issue time is eligible.
            subprocess.run(["git", "-C", str(state), "rm", "-q", "--cached",
                            str(forecast_path.relative_to(state))], check=True)
            subprocess.run(["git", "-C", str(state), "commit", "-qm", "Remove incomplete index entry"],
                           check=True, env=environment)
            subprocess.run(["git", "-C", str(state), "add", "forecasts"], check=True)
            subprocess.run(["git", "-C", str(state), "commit", "-qm", "Issue complete forecast"],
                           check=True, env=environment)
            outcome_path = path_for(state, "outcomes", date(2026, 10, 16), "-h1.json")
            outcome_path.parent.mkdir(parents=True)
            outcome = {"origin_session": "2026-10-16", "target_session": "2026-10-19",
                       "horizon": 1, "records": [
                           {"symbol": symbol, "origin_session": "2026-10-16",
                            "target_session": "2026-10-19", "horizon": 1,
                            "forecast_close": 101.0, "actual_close": 102.0,
                            "absolute_error": 1.0, "unchanged_close_error": 2.0}
                           for symbol in sorted(symbols)]}
            outcome_path.write_text(json.dumps(outcome))
            prices = pd.DataFrame([
                {"date": pd.Timestamp(day), "symbol": symbol, "raw_close": close}
                for day, close in ((date(2026, 10, 16), 100.0),
                                   (date(2026, 10, 19), 102.0))
                for symbol in sorted(symbols)])
            with (patch("scripts.prospective_performance.verify", return_value={"one_day_model_sha256": "model"}),
                  patch("scripts.prospective_performance.pd.read_csv", return_value=pd.DataFrame()),
                  patch("scripts.prospective_performance.read_sessions", return_value=(prices, [date(2026, 10, 19)]))):
                report = build_report(state, CALENDAR.path)
                self.assertEqual(report["horizons"]["1"]["equal_stock_mae_inr"], 1.0)
                self.assertEqual(report["horizons"]["1"]["unchanged_close_mae_inr"], 2.0)
                outcome["records"][0]["actual_close"] = 103.0
                outcome_path.write_text(json.dumps(outcome))
                with self.assertRaisesRegex(ValueError, "Outcome disagrees"):
                    build_report(state, CALENDAR.path)


if __name__ == "__main__":
    unittest.main()
