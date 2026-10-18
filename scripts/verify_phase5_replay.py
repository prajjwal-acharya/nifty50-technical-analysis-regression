"""Replay the saved October NSE reports and compare private and public outputs.

This verification reads the private store and cached official report pairs. It
never writes to either repository and never contacts NSE. The previously saved
roster/action review is reused so the replay tests data, features, inference,
forecast timing, and publication without changing the historical observation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from unittest.mock import patch

from nifty50_ta.data_pipeline import ROOT
from nifty50_ta.live_eod import Calendar, IST, path_for, run, validate_prospective_issue
from scripts.build_pages_snapshot import build_snapshot
from scripts.verify_live_store import verify

DATES = [date(2026, 10, day) for day in (14, 15, 16)]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(state: Path) -> dict[str, str]:
    return {str(path.relative_to(state)): digest(path) for folder in
            ("sessions", "forecasts", "reviews", "outcomes")
            for path in (state / folder).rglob("*") if path.is_file()}


def seed(source: Path, destination: Path) -> None:
    destination.mkdir()
    for name in ("baseline", "models"):
        shutil.copytree(source / name, destination / name)
    shutil.copy2(source / "store_manifest.json", destination / "store_manifest.json")


def replay(state: Path, reports: Path, public_json: Path | None,
           public_url: str | None) -> dict:
    started = time.perf_counter()
    verify(state)
    saved_forecast = json.loads(path_for(state, "forecasts", DATES[-1]).read_text())
    saved_review = json.loads(path_for(state, "reviews", DATES[-1]).read_text())
    issued_at = datetime.fromisoformat(saved_forecast["issued_at_utc"])
    calendar = ROOT / "config/nse_equity_calendar_2026.json"
    validate_prospective_issue(saved_forecast, Calendar(calendar))
    relative_forecast = str(path_for(state, "forecasts", DATES[-1]).relative_to(state))
    committed_at = subprocess.run(
        ["git", "-C", str(state), "log", "--diff-filter=A", "-1", "--format=%cI",
         "--", relative_forecast], check=True, capture_output=True, text=True).stdout.strip()
    target_open = datetime.combine(Calendar(calendar).forward(DATES[-1], 1), clock_time(9), IST)
    if (not committed_at or not issued_at <= datetime.fromisoformat(committed_at) < target_open):
        raise AssertionError("Forecast was not committed after issue and before the next session opened")
    with tempfile.TemporaryDirectory(prefix="nifty50-phase5-") as temporary:
        sample = Path(temporary) / "replay"
        seed(state, sample)
        with (patch("nifty50_ta.live_eod.roster_check", return_value=saved_review["roster"]),
              patch("nifty50_ta.live_eod.action_review", return_value=copy.deepcopy(saved_review))):
            first = run(sample, calendar, source_dir=reports, now=issued_at)
            if first["appended_sessions"] != [day.isoformat() for day in DATES] or first["issued_forecast"] != DATES[-1].isoformat():
                raise AssertionError(f"Replay did not append and issue the expected records: {first}")
            for day in DATES:
                replay_data = path_for(sample, "sessions", day, ".csv.gz")
                if digest(replay_data) != digest(path_for(state, "sessions", day, ".csv.gz")):
                    raise AssertionError(f"Replayed price rows differ for {day}")
                actual_meta = json.loads(path_for(state, "sessions", day).read_text())
                replay_meta = json.loads(path_for(sample, "sessions", day).read_text())
                if actual_meta["reports"] != replay_meta["reports"]:
                    raise AssertionError(f"Official report hashes differ for {day}")
            if json.loads(path_for(sample, "forecasts", DATES[-1]).read_text()) != saved_forecast:
                raise AssertionError("Replayed forecast differs from the immutable issued forecast")
            before = records(sample)
            second = run(sample, calendar, source_dir=reports, now=issued_at)
            if second["status"] != "current" or records(sample) != before:
                raise AssertionError("Duplicate run changed an immutable record")

        missing = sample / "missing-reports"
        missing.mkdir()
        for kind, extension in (("security", ".csv"), ("udiff", ".zip")):
            filename = f"20261014_{kind}{extension}"
            shutil.copy2(reports / filename, missing / filename)
        isolated = sample / "missing-state"
        seed(state, isolated)
        delayed = run(isolated, calendar, source_dir=missing, now=issued_at)
        if delayed["status"] != "waiting_for_reports" or delayed["missing_session"] != "2026-10-15" or records(isolated):
            raise AssertionError("Missing middle report was not handled without partial writes")

        future_action = copy.deepcopy(saved_review)
        future_action["price_basis_events"] = [{"symbol": "ADANIENT", "ex_date": "2026-10-21",
                                                 "subject": "test split", "isin": "test"}]
        event_state = sample / "future-action-state"
        seed(state, event_state)
        with (patch("nifty50_ta.live_eod.roster_check", return_value=saved_review["roster"]),
              patch("nifty50_ta.live_eod.action_review", return_value=future_action)):
            run(event_state, calendar, source_dir=reports, now=issued_at)
        event_forecast = json.loads(path_for(event_state, "forecasts", DATES[-1]).read_text())
        selected = {(item["symbol"], item["horizon"]): item for item in event_forecast["forecasts"]}
        if (selected[("ADANIENT", 1)]["status"] != "issued"
                or selected[("ADANIENT", 5)]["status"] != "abstained_known_corporate_action"
                or selected[("ADANIPORTS", 5)]["status"] != "issued"):
            raise AssertionError("Known future action did not abstain only the affected horizon")

        origin_action = copy.deepcopy(saved_review)
        origin_action["price_basis_events"] = [{"symbol": "ADANIENT", "ex_date": "2026-10-16",
                                                 "subject": "test split", "isin": "test"}]
        blocked_state = sample / "origin-action-state"
        seed(state, blocked_state)
        with (patch("nifty50_ta.live_eod.roster_check", return_value=saved_review["roster"]),
              patch("nifty50_ta.live_eod.action_review", return_value=origin_action)):
            try:
                run(blocked_state, calendar, source_dir=reports, now=issued_at)
            except ValueError as exc:
                if "Unreviewed price-basis" not in str(exc):
                    raise
            else:
                raise AssertionError("Unreviewed origin action was accepted")
        if records(blocked_state):
            raise AssertionError("Unreviewed origin action wrote partial records")

        # If checks and scoring cross the next-session open, preserve the new
        # prices but never label the late inference as a prospective forecast.
        late_state = sample / "late-scoring-state"
        seed(state, late_state)
        instants = iter(datetime.fromisoformat(value) for value in (
            "2026-10-19T03:25:00+00:00", "2026-10-19T03:27:00+00:00",
            "2026-10-19T03:31:00+00:00"))

        class ClockedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return next(instants)

        with (patch("nifty50_ta.live_eod.datetime", ClockedDateTime),
              patch("nifty50_ta.live_eod.roster_check", return_value=saved_review["roster"]),
              patch("nifty50_ta.live_eod.action_review", return_value=copy.deepcopy(saved_review))):
            late = run(late_state, calendar, source_dir=reports)
        if (late["appended_sessions"] != [day.isoformat() for day in DATES]
                or late["issued_forecast"] is not None
                or late["missed_forecast"] != DATES[-1].isoformat()
                or path_for(late_state, "forecasts", DATES[-1]).exists()):
            raise AssertionError("Scoring that crossed market open issued a retrospective forecast")

    projected = build_snapshot(state, calendar, publication_authorized=True,
                               now=issued_at + timedelta(minutes=1))
    # Simulate a publisher that built the file but stopped before pushing it.
    # A later run must rebuild the same bytes from immutable private records.
    state_before = records(state)
    first_bytes = (json.dumps(projected, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    recovered = build_snapshot(state, calendar, publication_authorized=True,
                               now=issued_at + timedelta(minutes=1))
    recovered_bytes = (json.dumps(recovered, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if first_bytes != recovered_bytes or records(state) != state_before:
        raise AssertionError("Publication retry changed snapshot bytes or private records")
    if public_json is not None and json.loads(public_json.read_text()) != projected:
        raise AssertionError("Committed public JSON differs from the verified private projection")
    if public_url is not None:
        with urllib.request.urlopen(public_url, timeout=20) as response:
            if response.status != 200 or json.load(response) != projected:
                raise AssertionError("Deployed Pages JSON differs from the verified private projection")
    return {"status": "passed", "sessions_replayed": len(DATES),
            "forecast_origin": DATES[-1].isoformat(), "stocks": len(projected["stocks"]),
            "forecast_git_commit": "before_target_open",
            "duplicate_run": "unchanged", "missing_middle_report": "no_partial_write",
            "future_action": "affected_horizon_abstained", "origin_action": "blocked_before_write",
            "late_scoring": "prices_only_no_forecast",
            "publication_retry": "identical_bytes_no_private_change",
            "public_json": "matched" if public_json else "not_checked",
            "public_url": "matched" if public_url else "not_checked",
            "elapsed_seconds": round(time.perf_counter() - started, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--public-json", type=Path)
    parser.add_argument("--public-url")
    args = parser.parse_args()
    print(json.dumps(replay(args.state_dir, args.source_dir, args.public_json, args.public_url), indent=2))


if __name__ == "__main__":
    main()
