"""Archive the NSE corporate-action search for every frozen constituent."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .data_pipeline import ROOT


def fetch_symbol(symbol: str, first_date: str, last_date: str) -> tuple[str, dict]:
    query = urllib.parse.urlencode({
        "index": "equities", "symbol": symbol,
        "from_date": datetime.fromisoformat(first_date).strftime("%d-%m-%Y"),
        "to_date": datetime.fromisoformat(last_date).strftime("%d-%m-%Y"),
    })
    url = f"https://www.nseindia.com/api/corporates-corporateActions?{query}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                records = json.load(response)
            if not isinstance(records, list):
                raise ValueError("Unexpected NSE corporate-action response")
            return symbol, {"url": url, "records": records}
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=ROOT / "config/nifty50_universe.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config/data_pipeline.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data/raw/nse_all_corporate_actions_snapshot.json")
    args = parser.parse_args()
    symbols = json.loads(args.universe.read_text(encoding="utf-8"))["symbols"]
    config = json.loads(args.config.read_text(encoding="utf-8"))
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch_symbol, symbol, config["source_first_date"],
                        config["source_last_date"]): symbol for symbol in symbols
        }
        for future in as_completed(futures):
            symbol, source = future.result()
            results[symbol] = source
    snapshot = {
        "retrieved_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": ("NSE corporate-actions API full window "
                   f"{config['source_first_date']} to {config['source_last_date']}"),
        "symbols": dict(sorted(results.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(results), "output": str(args.output)}))


if __name__ == "__main__":
    main()
