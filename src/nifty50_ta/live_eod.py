"""Append verified NSE closes and issue point-in-time, fixed-horizon forecasts."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np
import pandas as pd

from nifty50_ta.cross_sectional_expansion import expand_cross_sectional_features
from nifty50_ta.data_pipeline import apply_actions, create_features, load_actions
from nifty50_ta.feature_expansion import expand_features
from nifty50_ta.fetch_all_actions import fetch_symbol
from nifty50_ta.model_common import sha256
from nifty50_ta.one_day_model import FEATURES

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
RAW_COLUMNS = ["date", "symbol", "series", "raw_open", "raw_high", "raw_low",
               "raw_prev_close", "raw_close", "volume", "source_name",
               "source_excel_row", "source_url"]
FIELD_NAMES = {
    "raw_open": ("OPEN_PRICE", "OpnPric"),
    "raw_high": ("HIGH_PRICE", "HghPric"),
    "raw_low": ("LOW_PRICE", "LwPric"),
    "raw_prev_close": ("PREV_CLOSE", "PrvsClsgPric"),
    "raw_close": ("CLOSE_PRICE", "ClsPric"),
    "volume": ("TTL_TRD_QNTY", "TtlTradgVol"),
}
PRICE_BASIS_TERMS = ("bonus", "split", "splt", "demerg", "arrangement",
                     "rights", "merger", "amalgam", "consolidat")
ROSTER_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,application/zip,*/*"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path_for(state: Path, kind: str, day: date, extension: str = ".json") -> Path:
    return state / kind / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}{extension}"


def write_once(path: Path, body: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
    except FileExistsError:
        if path.read_bytes() != body:
            raise ValueError(f"Immutable record differs: {path}")
        return False
    return True


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


class Calendar:
    def __init__(self, path: Path):
        self.path = path
        payload = json.loads(path.read_text())
        self.year = int(payload["year"])
        self.holidays = {date.fromisoformat(day) for day in payload["holidays"]}
        self.special = {date.fromisoformat(day) for day in payload["special_sessions"]}
        if any(day.year != self.year for day in self.holidays | self.special):
            raise ValueError("Calendar has dates outside its stated year")
        self.sha256 = sha256(path)
        self.source_url = payload["source_url"]

    def is_session(self, day: date) -> bool:
        if day.year != self.year:
            raise ValueError(f"No reviewed NSE equity calendar for {day.year}")
        return day in self.special or (day.weekday() < 5 and day not in self.holidays)

    def forward(self, origin: date, count: int) -> date:
        day = origin
        for _ in range(count):
            day += timedelta(days=1)
            while not self.is_session(day):
                day += timedelta(days=1)
        return day

    def between(self, start: date, end: date) -> list[date]:
        days = []
        day = start + timedelta(days=1)
        while day <= end:
            if self.is_session(day):
                days.append(day)
            day += timedelta(days=1)
        return days


def report_url(day: date, kind: str) -> str:
    if kind == "security":
        return f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv"
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def download(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc


def report_body(day: date, kind: str, source_dir: Path | None) -> bytes | None:
    if source_dir is None:
        return download(report_url(day, kind))
    path = source_dir / f"{day:%Y%m%d}_{kind}{'.zip' if kind == 'udiff' else '.csv'}"
    return path.read_bytes() if path.exists() else None


def report_rows(body: bytes, kind: str, day: date) -> dict[str, dict]:
    if kind == "udiff":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"Unexpected UDiFF archive on {day}")
            content = archive.read(names[0]).decode("utf-8-sig")
    else:
        content = body.decode("utf-8-sig")
    rows = {}
    for raw in csv.DictReader(io.StringIO(content)):
        row = {key.strip(): value.strip() for key, value in raw.items() if key}
        if kind == "security":
            if row.get("SERIES") != "EQ":
                continue
            symbol, date_field = row["SYMBOL"], "DATE1"
        else:
            if (row.get("SctySrs"), row.get("Sgmt"), row.get("FinInstrmTp")) != ("EQ", "CM", "STK"):
                continue
            symbol, date_field = row["TckrSymb"], "TradDt"
        if pd.to_datetime(row[date_field], dayfirst=kind == "security").date() != day:
            raise ValueError(f"Wrong report date for {symbol} on {day}")
        if symbol in rows:
            raise ValueError(f"Duplicate EQ row for {symbol} on {day}")
        rows[symbol] = row
    return rows


def parse_session(day: date, symbols: set[str], isins: dict[str, str],
                  source_dir: Path | None, retrieved_at: str) -> tuple[pd.DataFrame, dict] | None:
    bodies = {kind: report_body(day, kind, source_dir) for kind in ("security", "udiff")}
    if any(body is None for body in bodies.values()):
        return None
    reports = {kind: report_rows(body, kind, day) for kind, body in bodies.items()}
    for kind, rows in reports.items():
        if missing := symbols - set(rows):
            raise ValueError(f"{kind} on {day} lacks basket symbols: {sorted(missing)}")
    result = []
    for symbol in sorted(symbols):
        sec, udf = reports["security"][symbol], reports["udiff"][symbol]
        if udf["ISIN"] != isins[symbol]:
            raise ValueError(f"ISIN changed for {symbol} on {day}")
        values = {}
        for field, (left, right) in FIELD_NAMES.items():
            a, b = float(sec[left]), float(udf[right])
            if not math.isfinite(a) or not math.isclose(a, b, rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"NSE report mismatch for {symbol} on {day}: {field}")
            values[field] = a
        if (min(values["raw_open"], values["raw_low"], values["raw_close"]) <= 0
                or values["volume"] < 0
                or values["raw_high"] < max(values["raw_open"], values["raw_close"], values["raw_low"])
                or values["raw_low"] > min(values["raw_open"], values["raw_close"])):
            raise ValueError(f"Invalid OHLCV for {symbol} on {day}")
        result.append({"date": pd.Timestamp(day), "symbol": symbol, "series": "EQ", **values,
                       "source_name": "NSE_sec_bhavdata_full", "source_excel_row": np.nan,
                       "source_url": report_url(day, "security")})
    provenance = {
        "schema_version": 1, "session": day.isoformat(), "retrieved_at_utc": retrieved_at,
        "source_mode": "local_verified_reports" if source_dir else "direct_nse_download",
        "row_count": 50, "validation": "dual_report_50_eq_isin_ohlcv_and_prev_close_passed",
        "reports": {kind: {"url": report_url(day, kind), "sha256": digest(body)}
                    for kind, body in bodies.items()},
    }
    return pd.DataFrame(result, columns=RAW_COLUMNS), provenance


def roster_check(universe: dict) -> dict:
    body = download(ROSTER_URL)
    if body is None:
        raise ValueError("Official Nifty 50 constituent file unavailable")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    eq = {row["Symbol"].strip(): row["ISIN Code"].strip() for row in rows
          if row["Series"].strip() == "EQ"}
    if len(rows) != 50 or len(eq) != 50 or eq != universe["current_isin_by_symbol"]:
        raise ValueError("Official Nifty 50 basket differs from pinned basket; review membership")
    return {"url": ROSTER_URL, "sha256": digest(body), "rows": 50,
            "pinned_as_of": universe["as_of_date"]}


def action_review(symbols: set[str], start: date, end: date) -> dict:
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_symbol, symbol, start.isoformat(), end.isoformat()): symbol
                   for symbol in symbols}
        for future in as_completed(futures):
            symbol, source = future.result()
            if symbol != futures[future]:
                raise ValueError("Corporate-action response symbol mismatch")
            results[symbol] = source
    events = []
    for symbol, source in sorted(results.items()):
        for record in source["records"]:
            if record.get("series") != "EQ":
                continue
            if record.get("symbol") != symbol:
                raise ValueError(f"Corporate-action response mismatch for {symbol}")
            subject = str(record.get("subject", ""))
            if any(term in subject.lower() for term in PRICE_BASIS_TERMS):
                event_day = datetime.strptime(record["exDate"], "%d-%b-%Y").date()
                if start <= event_day <= end:
                    events.append({"symbol": symbol, "ex_date": event_day.isoformat(),
                                   "subject": subject, "isin": record.get("isin")})
    return {"start": start.isoformat(), "end": end.isoformat(),
            "sources": dict(sorted(results.items())), "price_basis_events": events}


def read_sessions(state: Path, baseline: pd.DataFrame,
                  symbols: set[str], calendar: Calendar) -> tuple[pd.DataFrame, list[date]]:
    expected = baseline.date.max().date()
    rows = [baseline[RAW_COLUMNS]]
    days = []
    for path in sorted((state / "sessions").glob("*/*/*.csv.gz")):
        day = date.fromisoformat(path.name.removesuffix(".csv.gz"))
        if day <= expected or (days and day <= days[-1]):
            raise ValueError("Duplicate or out-of-order stored session")
        meta = json.loads(path.with_suffix("").with_suffix(".json").read_text())
        if meta["session"] != day.isoformat() or meta["rows_sha256"] != sha256(path):
            raise ValueError(f"Session provenance mismatch: {path}")
        frame = pd.read_csv(path, parse_dates=["date"])
        if (len(frame) != 50 or frame.date.nunique() != 1 or frame.date.iloc[0].date() != day
                or set(frame.symbol) != symbols or not frame.series.eq("EQ").all()):
            raise ValueError(f"Malformed stored session: {path}")
        rows.append(frame[RAW_COLUMNS])
        days.append(day)
    prices = pd.concat(rows, ignore_index=True)
    if prices[["date", "symbol", "series"]].duplicated().any():
        raise ValueError("Duplicate price key in live store")
    if days and days != calendar.between(expected, days[-1]):
        raise ValueError("Stored sessions have a missing NSE trading date")
    return prices, days


def score(prices: pd.DataFrame, state: Path, origin: date, symbols: set[str]) -> list[dict]:
    actions = load_actions(state / "baseline/config/corporate_actions.csv")
    prepared = apply_actions(prices.sort_values(["symbol", "date"]).reset_index(drop=True), actions)
    expanded = expand_cross_sectional_features(
        expand_features(create_features(prepared), allow_test_rows=True, legacy_raw_ohlcv=True),
        allow_test_rows=True)
    model_info = json.loads((state / "models/one_day/manifest.json").read_text())
    booster = lgb.Booster(model_file=str(state / "models/one_day/one_day_lightgbm.txt"))
    if model_info["feature_order"] != FEATURES:
        raise ValueError("Pinned model feature order changed")
    saved = pd.read_csv(state / "models/one_day/test_2026_predictions.csv.gz", parse_dates=["date"])
    audit_day = pd.Timestamp("2026-10-12")
    audit = expanded.loc[expanded.date.eq(audit_day)].sort_values("symbol")
    reference = saved.loc[saved.date.eq(audit_day)].sort_values("symbol")
    if (len(audit) != 50 or audit.symbol.tolist() != reference.symbol.tolist()
            or not np.allclose(booster.predict(audit[FEATURES]), reference.predicted_return,
                               atol=1e-10, rtol=1e-8)):
        raise ValueError("Frozen model parity check failed")
    latest = expanded.loc[expanded.date.eq(pd.Timestamp(origin))].sort_values("symbol")
    if (len(latest) != 50 or set(latest.symbol) != symbols or not latest.feature_ready.all()
            or not np.isfinite(latest[FEATURES].to_numpy(dtype=float)).all()):
        raise ValueError("Origin has incomplete or invalid features")
    predicted = booster.predict(latest[FEATURES])
    if not np.isfinite(predicted).all():
        raise ValueError("Model produced non-finite predictions")
    return [{"symbol": symbol, "origin_close": float(close),
             "predicted_return": float(change),
             "predicted_close": round(float(close) * (1 + float(change)), 4)}
            for symbol, close, change in zip(latest.symbol, latest.raw_close, predicted)]


def eligible_for_issue(now: datetime, target: date) -> bool:
    # Never backdate an issue; the next target session must not have opened.
    return now.astimezone(IST) < datetime.combine(target, time(9), IST)


def build_outcomes(state: Path, prices: pd.DataFrame, stored_days: list[date],
                   calendar: Calendar, now: datetime) -> int:
    if not stored_days:
        return 0
    closes = prices.set_index([prices.date.dt.date, "symbol"]).raw_close
    count = 0
    for path in sorted((state / "forecasts").glob("*/*/*.json")):
        forecast = json.loads(path.read_text())
        origin = date.fromisoformat(forecast["origin_session"])
        for horizon in (1, 5):
            target = calendar.forward(origin, horizon)
            if target not in stored_days:
                continue
            records = []
            for item in forecast["forecasts"]:
                if item["horizon"] != horizon or item["status"] != "issued":
                    continue
                actual = float(closes.loc[(target, item["symbol"])])
                records.append({"symbol": item["symbol"], "horizon": horizon,
                                "origin_session": origin.isoformat(), "target_session": target.isoformat(),
                                "forecast_close": item["forecast_close"], "actual_close": actual,
                                "absolute_error": round(abs(item["forecast_close"] - actual), 4),
                                "unchanged_close_error": round(abs(item["origin_close"] - actual), 4)})
            if not records:
                continue
            output = {"schema_version": 1, "origin_session": origin.isoformat(),
                      "target_session": target.isoformat(), "horizon": horizon,
                      "recorded_at_utc": now.astimezone(UTC).isoformat(), "records": records}
            outcome_path = path_for(state, "outcomes", origin, f"-h{horizon}.json")
            if not outcome_path.exists():
                write_once(outcome_path, json_bytes(output))
                count += 1
    return count


def run(state: Path, calendar_path: Path, *, source_dir: Path | None = None,
        now: datetime | None = None) -> dict:
    from scripts.verify_live_store import verify

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    state = state.resolve()
    manifest = verify(state)
    calendar = Calendar(calendar_path)
    universe = json.loads((state / "baseline/config/nifty50_universe.json").read_text())
    symbols = set(universe["symbols"])
    if len(symbols) != 50 or set(universe["current_isin_by_symbol"]) != symbols:
        raise ValueError("Pinned basket is incomplete")
    baseline = pd.read_csv(state / "baseline/daily_prices.csv.gz", parse_dates=["date"], low_memory=False)
    if baseline.date.max().date().isoformat() != manifest["baseline_last_session"]:
        raise ValueError("Baseline date does not match manifest")
    prices, stored_days = read_sessions(state, baseline, symbols, calendar)
    last = stored_days[-1] if stored_days else baseline.date.max().date()
    local_now = now.astimezone(IST)
    through = local_now.date() if local_now.time() >= time(17) else local_now.date() - timedelta(days=1)
    due = calendar.between(last, through)
    retrieved_at = now.astimezone(UTC).isoformat()
    pending = []
    previous = prices.loc[prices.date.eq(pd.Timestamp(last))].set_index("symbol")
    for day in due:
        parsed = parse_session(day, symbols, universe["current_isin_by_symbol"], source_dir, retrieved_at)
        if parsed is None:
            # A newer report must not leapfrog a missing exchange session.
            return {"status": "waiting_for_reports", "missing_session": day.isoformat(),
                    "appended_sessions": [], "issued_forecast": None, "outcomes": 0}
        frame, provenance = parsed
        for row in frame.itertuples(index=False):
            if abs(row.raw_prev_close - float(previous.loc[row.symbol, "raw_close"])) > 0.011:
                raise ValueError(f"Previous close discontinuity for {row.symbol} on {day}; review action or missing session")
        pending.append((day, frame, provenance))
        previous = frame.set_index("symbol")
    origin = pending[-1][0] if pending else last
    target_1 = calendar.forward(origin, 1)
    target_5 = calendar.forward(origin, 5)
    forecast_path = path_for(state, "forecasts", origin)
    can_issue = not forecast_path.exists() and origin > baseline.date.max().date() and eligible_for_issue(now, target_1)
    review = None
    if pending or can_issue:
        roster = roster_check(universe)
        review = action_review(symbols, baseline.date.max().date() + timedelta(days=1), target_5)
        review["schema_version"] = 1
        review["retrieved_at_utc"] = retrieved_at
        review["roster"] = roster
        review["calendar_sha256"] = calendar.sha256
        if prior := [event for event in review["price_basis_events"]
                     if date.fromisoformat(event["ex_date"]) <= origin]:
            raise ValueError(f"Unreviewed price-basis corporate action through {origin}: {prior}")
    all_prices = pd.concat([prices] + [frame for _, frame, _ in pending], ignore_index=True)
    forecast = None
    if can_issue:
        scores = score(all_prices, state, origin, symbols)
        assert review is not None
        events = review["price_basis_events"]
        entries = []
        for scored in scores:
            for horizon, target in ((1, target_1), (5, target_5)):
                affected = [event for event in events if event["symbol"] == scored["symbol"]
                            and origin < date.fromisoformat(event["ex_date"]) <= target]
                entries.append({"symbol": scored["symbol"], "horizon": horizon,
                                "target_session": target.isoformat(),
                                "origin_close": scored["origin_close"],
                                "forecast_close": None if affected else
                                    (scored["predicted_close"] if horizon == 1 else scored["origin_close"]),
                                "policy": "frozen_one_day_lightgbm" if horizon == 1 else "unchanged_close",
                                "status": "abstained_known_corporate_action" if affected else "issued",
                                "known_events": affected})
        forecast = {"schema_version": 1, "origin_session": origin.isoformat(),
                    "issued_at_utc": retrieved_at, "issue_kind": "prospective",
                    "model_sha256": manifest["one_day_model_sha256"],
                    "model_run": manifest["one_day_model_run"],
                    "calendar_sha256": calendar.sha256,
                    "action_screen_as_of_utc": retrieved_at,
                    "action_screen_note": "Known NSE price-basis actions only; later announcements may require revision or abstention.",
                    "forecasts": entries}
    appended = []
    for day, frame, provenance in pending:
        path = path_for(state, "sessions", day, ".csv.gz")
        write_once(path, gzip.compress(frame.to_csv(index=False).encode(), mtime=0))
        provenance["rows_sha256"] = sha256(path)
        provenance["roster_sha256"] = review["roster"]["sha256"]
        provenance["calendar_sha256"] = calendar.sha256
        write_once(path_for(state, "sessions", day), json_bytes(provenance))
        appended.append(day.isoformat())
    if review is not None:
        review_path = path_for(state, "reviews", origin)
        if not review_path.exists():
            write_once(review_path, json_bytes(review))
    if forecast is not None:
        write_once(forecast_path, json_bytes(forecast))
    outcomes = build_outcomes(state, all_prices, stored_days + [day for day, _, _ in pending], calendar, now)
    return {"status": "updated" if appended or forecast or outcomes else "current",
            "appended_sessions": appended, "latest_session": origin.isoformat(),
            "issued_forecast": origin.isoformat() if forecast else None,
            "outcomes": outcomes}
