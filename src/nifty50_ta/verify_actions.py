"""Verify the configured event dates and split/bonus ratios against NSE records.

Writes a local JSON snapshot of NSE's responses, then updates verification
statuses and URLs in the versioned event ledger. The price pipeline itself
remains offline and always reads the saved ledger.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .data_pipeline import ROOT, load_actions


BONUS = re.compile(r"bonus\s+(\d+)\s*:\s*(\d+)", re.IGNORECASE)
SPLIT = re.compile(
    r"(?:split|splt).*?(?:from|frm)\s+rs?e?\s*([\d.]+).*?to\s+rs?e?\s*([\d.]+)",
    re.IGNORECASE,
)


def factor_from_subject(subject: str) -> float | None:
    bonus = BONUS.search(subject)
    if bonus:
        new, held = map(int, bonus.groups())
        return held / (held + new)
    split = SPLIT.search(subject)
    if split:
        old_face, new_face = map(float, split.groups())
        return new_face / old_face
    return None


def fetch_event(symbol: str, ex_date: pd.Timestamp) -> dict:
    start = (ex_date - timedelta(days=2)).strftime("%d-%m-%Y")
    end = (ex_date + timedelta(days=2)).strftime("%d-%m-%Y")
    query = urllib.parse.urlencode({
        "index": "equities", "symbol": symbol, "from_date": start, "to_date": end,
    })
    url = f"https://www.nseindia.com/api/corporates-corporateActions?{query}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
    })
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                records = json.load(response)
            if not isinstance(records, list):
                raise ValueError("Unexpected NSE response format")
            return {"url": url, "records": records}
        except Exception as exc:  # network or rate-limit failure
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"NSE fetch failed for {symbol} {ex_date.date()}: {last_error}")


def verify(ledger_path: Path, snapshot_path: Path, offline: bool = False) -> dict:
    actions = load_actions(ledger_path)
    keys = [(row.symbol, row.ex_date) for row in actions.itertuples(index=False)]
    if offline:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        fetched = {
            (event["symbol"], pd.Timestamp(event["ex_date"])): event
            for event in snapshot["events"]
        }
        if set(fetched) != set(keys):
            raise ValueError("NSE snapshot does not match the action ledger")
    else:
        fetched = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_event, symbol, date): (symbol, date) for symbol, date in keys}
            for future in as_completed(futures):
                symbol, date = futures[future]
                fetched[(symbol, date)] = future.result()
        snapshot = {
            "retrieved_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "NSE corporate-actions API",
            "events": [
                {"symbol": symbol, "ex_date": date.date().isoformat(), **fetched[(symbol, date)]}
                for symbol, date in keys
            ],
        }
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    statuses = []
    urls = []
    failures = []
    for action in actions.itertuples(index=False):
        response = fetched[(action.symbol, action.ex_date)]
        records = [
            record for record in response["records"]
            if record.get("symbol") == action.symbol and record.get("series") == "EQ"
            and pd.to_datetime(record.get("exDate"), format="%d-%b-%Y", errors="coerce") == action.ex_date
        ]
        if action.action_type in {"Demerger", "Rights"}:
            terms = (("demerger", "scheme", "arrangement") if action.action_type == "Demerger"
                     else ("rights",))
            matched = [r for r in records if any(term in str(r.get("subject", "")).lower()
                       for term in terms)]
            status = "nse_event_date_verified" if matched else "needs_review"
        else:
            factors = [factor_from_subject(str(r.get("subject", ""))) for r in records]
            factors = [factor for factor in factors if factor is not None]
            product = math.prod(factors)
            status = "nse_ratio_verified" if factors and math.isclose(
                product, action.split_bonus_factor, rel_tol=1e-9, abs_tol=1e-12
            ) else "needs_review"
        statuses.append(status)
        urls.append(response["url"])
        if status == "needs_review":
            failures.append({
                "action_id": action.action_id, "expected_factor": action.split_bonus_factor,
                "nse_subjects": [r.get("subject") for r in records],
            })
    actions["verification_status"] = statuses
    actions["source_url"] = urls
    actions["ex_date"] = actions.ex_date.dt.strftime("%Y-%m-%d")
    actions.to_csv(ledger_path, index=False, float_format="%.16g", na_rep="")
    return {"status_counts": pd.Series(statuses).value_counts().to_dict(), "needs_review": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=ROOT / "config/corporate_actions.csv")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/raw/nse_corporate_actions_snapshot.json")
    parser.add_argument("--offline", action="store_true", help="recheck the saved NSE snapshot without a network request")
    args = parser.parse_args()
    print(json.dumps(verify(args.ledger, args.snapshot, offline=args.offline), indent=2))


if __name__ == "__main__":
    main()
