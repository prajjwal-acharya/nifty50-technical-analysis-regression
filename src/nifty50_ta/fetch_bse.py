"""Archive NSE's daily BSE EQ rows for the dates in the supplied market calendar.

The exchange publishes daily bhavcopies under two historical layouts. This
collector keeps the exact BSE row, source URL, and downloaded file hash for
each date. Completed years are checkpoints, so network failures can be retried.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

from .data_pipeline import ROOT, read_raw_prices


FIELDS = (
    "date", "symbol", "series", "open", "high", "low", "prev_close",
    "close", "volume", "source_url", "source_sha256", "source_format",
    "source_raw_row_json",
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/csv,application/zip,*/*",
}


def candidate_urls(day: date) -> list[tuple[str, str]]:
    root = "https://nsearchives.nseindia.com"
    month = day.strftime("%b").upper()
    old = f"{root}/content/historical/EQUITIES/{day:%Y}/{month}/cm{day:%d}{month}{day:%Y}bhav.csv.zip"
    security = f"{root}/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv"
    return ([ (old, "legacy_zip"), (security, "security_csv") ]
            if day.year <= 2019 else
            [ (security, "security_csv"), (old, "legacy_zip") ])


def parse_bse(body: bytes, source_format: str, day: date) -> tuple[dict, dict]:
    if source_format.endswith("zip"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError("Unexpected NSE ZIP layout")
            content = archive.read(members[0]).decode("utf-8-sig")
    else:
        content = body.decode("utf-8-sig")
    records = csv.DictReader(io.StringIO(content))
    found = []
    for raw in records:
        normalized = {key.strip(): value.strip() for key, value in raw.items() if key is not None}
        if normalized.get("SYMBOL") == "BSE" and normalized.get("SERIES") == "EQ":
            found.append((raw, normalized))
    if len(found) != 1:
        raise ValueError(f"Expected one BSE EQ row for {day}; found {len(found)}")
    raw, row = found[0]
    actual_date = pd.to_datetime(row.get("DATE1", row.get("TIMESTAMP")), dayfirst=True).date()
    if actual_date != day:
        raise ValueError(f"NSE archive date mismatch: requested {day}, received {actual_date}")
    if source_format == "security_csv":
        values = (row["OPEN_PRICE"], row["HIGH_PRICE"], row["LOW_PRICE"],
                  row["PREV_CLOSE"], row["CLOSE_PRICE"], row["TTL_TRD_QNTY"])
    else:
        values = (row["OPEN"], row["HIGH"], row["LOW"], row["PREVCLOSE"],
                  row["CLOSE"], row["TOTTRDQTY"])
    return dict(zip(("open", "high", "low", "prev_close", "close", "volume"), values)), raw


def fetch_day(day: date) -> dict:
    failures = []
    for url, source_format in candidate_urls(day):
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=18) as response:
                    body = response.read()
                prices, raw = parse_bse(body, source_format, day)
                return {
                    "date": day.isoformat(), "symbol": "BSE", "series": "EQ",
                    **prices, "source_url": url,
                    "source_sha256": hashlib.sha256(body).hexdigest(),
                    "source_format": source_format,
                    "source_raw_row_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                }
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    failures.append(f"{source_format}:404")
                    break
                failures.append(f"{source_format}:{exc.code}")
            except Exception as exc:
                failures.append(f"{source_format}:{type(exc).__name__}:{str(exc)[:60]}")
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Could not obtain BSE EQ for {day}: {'; '.join(failures)}")


def save_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["date"]))


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len({row["date"] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate BSE dates in {path}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/50Nifty50.xlsx")
    parser.add_argument("--output", type=Path, default=ROOT / "data/raw/bse_nse_prices.csv")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    prices, _ = read_raw_prices(args.raw, {"JIOFIN": 10, "MAXHEALTH": 10})
    calendar = sorted({timestamp.date() for timestamp in prices.date if timestamp >= pd.Timestamp("2017-02-03")})
    checkpoint_dir = args.output.parent / "bse_nse_years"
    all_rows = []
    for year in sorted({day.year for day in calendar}):
        days = [day for day in calendar if day.year == year]
        checkpoint = checkpoint_dir / f"bse_{year}.csv"
        saved = load_rows(checkpoint) if checkpoint.exists() else []
        saved_dates = {row["date"] for row in saved}
        pending = [day for day in days if day.isoformat() not in saved_dates]
        rows = list(saved)
        errors = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fetch_day, day): day for day in pending}
            for completed, future in enumerate(as_completed(futures), 1):
                try:
                    rows.append(future.result())
                except Exception as exc:
                    errors.append(str(exc))
                if completed % 50 == 0:
                    save_rows(rows, checkpoint)
                    print(f"{year}: {len(rows)}/{len(days)} rows archived", flush=True)
        save_rows(rows, checkpoint)
        if errors:
            raise RuntimeError(f"{year}: {len(errors)} download failures; retry this command. First: {errors[0]}")
        if {row["date"] for row in rows} != {day.isoformat() for day in days}:
            raise ValueError(f"Incomplete BSE calendar for {year}")
        all_rows.extend(rows)
        print(f"{year}: complete ({len(rows)} days)", flush=True)
    save_rows(all_rows, args.output)
    print(f"Saved {len(all_rows)} official NSE BSE rows to {args.output}", flush=True)


if __name__ == "__main__":
    main()
