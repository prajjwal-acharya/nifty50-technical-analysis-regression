"""Checks the live store's exchange-date and immutable-result boundaries."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import (Calendar, build_outcomes, eligible_for_issue,
                                  parse_session, path_for, write_once)


CALENDAR = ROOT / "config/nse_equity_calendar_2026.json"


class LiveEodTests(unittest.TestCase):
    def test_exact_exchange_observation_horizons(self) -> None:
        calendar = Calendar(CALENDAR)
        self.assertEqual(calendar.forward(date(2026, 10, 16), 1), date(2026, 10, 19))
        self.assertEqual(calendar.forward(date(2026, 10, 16), 5), date(2026, 10, 26))
        self.assertFalse(calendar.is_session(date(2026, 10, 20)))
        self.assertTrue(calendar.is_session(date(2026, 11, 8)))
        with self.assertRaises(ValueError):
            calendar.forward(date(2026, 12, 30), 5)

    def test_issue_cutoff_is_before_next_session_open(self) -> None:
        self.assertTrue(eligible_for_issue(datetime.fromisoformat("2026-10-18T10:00:00+05:30"),
                                           date(2026, 10, 19)))
        self.assertFalse(eligible_for_issue(datetime.fromisoformat("2026-10-19T09:00:00+05:30"),
                                            date(2026, 10, 19)))

    def test_reports_must_agree_and_cover_entire_basket(self) -> None:
        day = date(2026, 10, 16)
        symbols = {f"S{i:02}" for i in range(50)}
        isins = {symbol: f"INE{i:09}" for i, symbol in enumerate(sorted(symbols))}
        sec = ["SYMBOL,SERIES,DATE1,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,PREV_CLOSE,CLOSE_PRICE,TTL_TRD_QNTY"]
        udf = ["TckrSymb,SctySrs,Sgmt,FinInstrmTp,TradDt,ISIN,OpnPric,HghPric,LwPric,PrvsClsgPric,ClsPric,TtlTradgVol"]
        for symbol in sorted(symbols):
            sec.append(f"{symbol},EQ,16-Oct-2026,100,104,99,101,103,5000")
            udf.append(f"{symbol},EQ,CM,STK,2026-10-16,{isins[symbol]},100,104,99,101,103,5000")
        security = ("\n".join(sec) + "\n").encode()

        def archive(lines: list[str]) -> bytes:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as zipped:
                zipped.writestr("report.csv", "\n".join(lines) + "\n")
            return stream.getvalue()

        with patch("nifty50_ta.live_eod.report_body", side_effect=[security, archive(udf)]):
            frame, provenance = parse_session(day, symbols, isins, None, "2026-10-18T00:00:00+00:00")
        self.assertEqual(len(frame), 50)
        self.assertEqual(provenance["row_count"], 50)
        bad = udf.copy()
        bad[1] = bad[1].replace(",103,5000", ",102,5000")
        with patch("nifty50_ta.live_eod.report_body", side_effect=[security, archive(bad)]):
            with self.assertRaisesRegex(ValueError, "report mismatch"):
                parse_session(day, symbols, isins, None, "2026-10-18T00:00:00+00:00")

    def test_outcome_uses_target_close_and_does_not_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            origin = date(2026, 10, 16)
            path = path_for(state, "forecasts", origin)
            issued = {"origin_session": origin.isoformat(), "forecasts": [
                {"symbol": "ABC", "horizon": 1, "status": "issued",
                 "forecast_close": 105.0, "origin_close": 100.0},
                {"symbol": "ABC", "horizon": 5, "status": "issued",
                 "forecast_close": 100.0, "origin_close": 100.0},
            ]}
            write_once(path, (json.dumps(issued) + "\n").encode())
            prices = pd.DataFrame({"date": pd.to_datetime(["2026-10-19", "2026-10-26"]),
                                   "symbol": ["ABC", "ABC"], "raw_close": [103.0, 98.0]})
            now = datetime.fromisoformat("2026-10-27T00:00:00+00:00")
            dates = [date(2026, 10, 19), date(2026, 10, 26)]
            self.assertEqual(build_outcomes(state, prices, dates, Calendar(CALENDAR), now), 2)
            outcome_path = path_for(state, "outcomes", origin, "-h5.json")
            first = outcome_path.read_bytes()
            self.assertEqual(json.loads(first)["records"][0]["absolute_error"], 2.0)
            self.assertEqual(build_outcomes(state, prices, dates, Calendar(CALENDAR), now), 0)
            self.assertEqual(outcome_path.read_bytes(), first)
            with self.assertRaisesRegex(ValueError, "Immutable record differs"):
                write_once(path, b"changed")


if __name__ == "__main__":
    unittest.main()
