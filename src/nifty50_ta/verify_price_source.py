"""Spot-check the supplied workbook's OHLCV against original NSE bhavcopies."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

from .data_pipeline import ROOT, read_raw_prices, sha256
from .fetch_bse import HEADERS, candidate_urls


CHECK_DATES = (
    "2016-10-13", "2017-02-03", "2017-09-28", "2018-03-22",
    "2019-02-13", "2020-02-01", "2021-10-13", "2022-03-21",
    "2022-09-13", "2023-11-12", "2024-03-02", "2025-05-23",
    "2025-06-16", "2026-02-01", "2026-10-13",
)
HISTORICAL_SYMBOLS = {
    "ETERNAL": "ZOMATO",
    "SHRIRAMFIN": "SRTRANSFIN",
    "TATACONSUM": "TATAGLOBAL",
    "TMPV": "TATAMOTORS",
}


def fetch_archive(day: pd.Timestamp) -> tuple[str, str, dict[str, dict]]:
    failures = []
    for url, source_format in candidate_urls(day.date()):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read()
            if source_format.endswith("zip"):
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                    if len(members) != 1:
                        raise ValueError("Unexpected NSE ZIP layout")
                    content = archive.read(members[0]).decode("utf-8-sig")
            else:
                content = body.decode("utf-8-sig")
            records = {}
            for raw in csv.DictReader(io.StringIO(content)):
                row = {key.strip(): value.strip() for key, value in raw.items() if key is not None}
                if row.get("SERIES") == "EQ":
                    records[row["SYMBOL"]] = row
            if not records:
                raise ValueError("No EQ records in archive")
            return url, hashlib.sha256(body).hexdigest(), records
        except urllib.error.HTTPError as exc:
            failures.append(f"{source_format}:{exc.code}")
            if exc.code != 404:
                continue
        except Exception as exc:
            failures.append(f"{source_format}:{type(exc).__name__}")
    raise RuntimeError(f"No NSE archive for {day.date()}: {failures}")


def audit(raw_path: Path, output_path: Path) -> dict:
    prices, _ = read_raw_prices(raw_path, {"JIOFIN": 10, "MAXHEALTH": 10})
    dates = pd.to_datetime(CHECK_DATES)
    if not set(dates).issubset(set(prices.date)):
        raise ValueError("Spot-check date missing from source workbook")
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = dict(zip(CHECK_DATES, pool.map(fetch_archive, dates)))
    matched = 0
    matched_via_historical_symbol = 0
    skipped = []
    mismatches = []
    sources = []
    for date_text in CHECK_DATES:
        url, digest, official = archives[date_text]
        sources.append({"date": date_text, "url": url, "sha256": digest})
        subset = prices.loc[prices.date.eq(pd.Timestamp(date_text)) & prices.series.eq("EQ")]
        for row in subset.itertuples(index=False):
            nse = official.get(row.symbol)
            if nse is None and row.symbol in HISTORICAL_SYMBOLS:
                nse = official.get(HISTORICAL_SYMBOLS[row.symbol])
                if nse is not None:
                    matched_via_historical_symbol += 1
            if nse is None:
                skipped.append({"date": date_text, "symbol": row.symbol})
                continue
            fields = ({
                "raw_open": "OPEN_PRICE", "raw_high": "HIGH_PRICE",
                "raw_low": "LOW_PRICE", "raw_prev_close": "PREV_CLOSE",
                "raw_close": "CLOSE_PRICE", "volume": "TTL_TRD_QNTY",
            } if "OPEN_PRICE" in nse else {
                "raw_open": "OPEN", "raw_high": "HIGH", "raw_low": "LOW",
                "raw_prev_close": "PREVCLOSE", "raw_close": "CLOSE",
                "volume": "TOTTRDQTY",
            })
            for local_field, nse_field in fields.items():
                a = float(getattr(row, local_field))
                b = float(nse[nse_field])
                if abs(a - b) > (0.011 if local_field != "volume" else 0):
                    mismatches.append({
                        "date": date_text, "symbol": row.symbol, "field": local_field,
                        "workbook": a, "nse": b,
                    })
            matched += 1
    report = {
        "workbook_sha256": sha256(raw_path),
        "checked_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dates_checked": list(CHECK_DATES),
        "matched_symbol_dates": matched,
        "matched_via_historical_symbol": matched_via_historical_symbol,
        "skipped_symbol_dates_due_to_historical_symbol_or_series": len(skipped),
        "skipped_symbol_dates": skipped,
        "field_mismatches": mismatches,
        "sources": sources,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/50Nifty50.xlsx")
    parser.add_argument("--output", type=Path, default=ROOT / "data/raw/nse_workbook_price_spotcheck.json")
    args = parser.parse_args()
    report = audit(args.raw, args.output)
    print(json.dumps({key: report[key] for key in (
        "matched_symbol_dates", "matched_via_historical_symbol",
        "skipped_symbol_dates_due_to_historical_symbol_or_series",
        "field_mismatches",
    )}, indent=2))


if __name__ == "__main__":
    main()
