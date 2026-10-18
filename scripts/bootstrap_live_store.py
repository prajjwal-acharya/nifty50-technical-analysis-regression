"""Create an immutable, checksum-verified starting point for live data."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from nifty50_ta.model_common import ROOT, sha256
from nifty50_ta.one_day_model import FEATURES


BASELINE_FILES = {
    "data/processed/daily_prices.csv.gz": "baseline/daily_prices.csv.gz",
    "data/processed/features.csv.gz": "baseline/features.csv.gz",
    "data/processed/manifest.json": "baseline/processed_manifest.json",
    "data/processed/release_audit.json": "baseline/release_audit.json",
    "data/processed/quality_report.json": "baseline/quality_report.json",
    "data/processed/stock_dictionary.csv": "baseline/stock_dictionary.csv",
    "docs/data-release-2026-10-15.json": "baseline/release_lock.json",
    "config/data_pipeline.json": "baseline/config/data_pipeline.json",
    "config/corporate_actions.csv": "baseline/config/corporate_actions.csv",
    "config/nifty50_universe.json": "baseline/config/nifty50_universe.json",
    "config/outlier_reviews.csv": "baseline/config/outlier_reviews.csv",
}
MODEL_FILES = {
    "one_day_lightgbm.txt": "models/one_day/one_day_lightgbm.txt",
    "manifest.json": "models/one_day/manifest.json",
    "test_2026_predictions.csv.gz": "models/one_day/test_2026_predictions.csv.gz",
}


def _check_release(root: Path) -> dict:
    lock_path = root / "docs/data-release-2026-10-15.json"
    lock = json.loads(lock_path.read_text())
    audit = json.loads((root / "data/processed/release_audit.json").read_text())
    if lock["audit_status"] != "passed" or audit["status"] != "passed":
        raise ValueError("The historical data release has not passed its audit.")
    for name in ("daily_prices.csv.gz", "features.csv.gz", "quality_report.json",
                 "stock_dictionary.csv"):
        path = root / "data/processed" / name
        if sha256(path) != lock["output_sha256"][name]:
            raise ValueError(f"Historical release checksum mismatch: {name}")
    if sha256(root / "data/processed/manifest.json") != audit["manifest_sha256"]:
        raise ValueError("Processed manifest does not match the release audit.")
    for name in ("data_pipeline.json", "corporate_actions.csv", "nifty50_universe.json",
                 "outlier_reviews.csv"):
        if sha256(root / "config" / name) != lock["versioned_config_sha256"][name]:
            raise ValueError(f"Historical configuration checksum mismatch: {name}")
    count, latest, latest_symbols = 0, "", set()
    with gzip.open(root / "data/processed/daily_prices.csv.gz", "rt", newline="") as source:
        for row in csv.DictReader(source):
            count += 1
            day = row["date"]
            if day > latest:
                latest, latest_symbols = day, {row["symbol"]}
            elif day == latest:
                latest_symbols.add(row["symbol"])
    if (count != lock["counts"]["price_rows"] or latest != lock["source_window"]["last_date"]
            or len(latest_symbols) != lock["counts"]["stocks"]):
        raise ValueError("Historical price coverage does not match the release lock.")
    return lock


def _check_model(root: Path, model_run: Path, lock_path: Path) -> dict:
    manifest = json.loads((model_run / "manifest.json").read_text())
    if manifest["release_lock_sha256"] != sha256(lock_path):
        raise ValueError("Model was not trained against this release lock.")
    if manifest["feature_order"] != FEATURES:
        raise ValueError("Model feature order does not match the current one-session contract.")
    if sha256(model_run / manifest["model_file"]) != manifest["model_sha256"]:
        raise ValueError("Saved one-session model checksum mismatch.")
    if manifest["model_file"] != "one_day_lightgbm.txt":
        raise ValueError("Unexpected saved one-session model filename.")
    if not (model_run / "test_2026_predictions.csv.gz").is_file():
        raise ValueError("Historical one-session predictions needed for parity checks are missing.")
    return manifest


def bootstrap(state_dir: Path, model_run: Path, root: Path = ROOT) -> dict:
    """Write a complete new store, or fail without changing an existing one."""
    root, state_dir = root.resolve(), state_dir.resolve()
    model_run = (root / model_run).resolve() if not model_run.is_absolute() else model_run.resolve()
    if state_dir.exists():
        raise FileExistsError(f"Refusing to replace existing live store: {state_dir}")
    if not state_dir.parent.is_dir():
        raise FileNotFoundError(f"Live-store parent does not exist: {state_dir.parent}")
    lock_path = root / "docs/data-release-2026-10-15.json"
    lock = _check_release(root)
    model = _check_model(root, model_run, lock_path)
    with tempfile.TemporaryDirectory(prefix="live-store-bootstrap-", dir=state_dir.parent) as staging_name:
        staging = Path(staging_name)
        file_hashes = {}
        for source_name, destination_name in BASELINE_FILES.items():
            destination = staging / destination_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / source_name, destination)
            file_hashes[destination_name] = sha256(destination)
        for source_name, destination_name in MODEL_FILES.items():
            destination = staging / destination_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(model_run / source_name, destination)
            file_hashes[destination_name] = sha256(destination)
        if file_hashes["models/one_day/one_day_lightgbm.txt"] != model["model_sha256"]:
            raise ValueError("Copied model checksum mismatch.")
        if file_hashes["baseline/daily_prices.csv.gz"] != lock["output_sha256"]["daily_prices.csv.gz"]:
            raise ValueError("Copied historical price checksum mismatch.")
        if file_hashes["baseline/features.csv.gz"] != lock["output_sha256"]["features.csv.gz"]:
            raise ValueError("Copied historical feature checksum mismatch.")
        manifest = {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "baseline_last_session": lock["source_window"]["last_date"],
            "basket_as_of": lock["universe_as_of_date"],
            "baseline_price_rows": lock["counts"]["price_rows"],
            "baseline_stocks": lock["counts"]["stocks"],
            "one_day_model_run": model_run.name,
            "one_day_model_sha256": model["model_sha256"],
            "files_sha256": file_hashes,
        }
        (staging / "store_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (staging / "README.md").write_text(
            "# Nifty 50 private live state\n\n"
            "This private repository holds the audited historical starting point and the "
            "pinned next-session model. Code and public decisions remain in the "
            "nifty50-technical-analysis-regression repository.\n\n"
            "The historical release ends on 2026-10-13. Files under `baseline/` "
            "and `models/` are immutable. `store_manifest.json` records their hashes. "
            "Future ingestion adds one dated session and its source provenance under "
            "`sessions/YYYY/MM/`, then immutable forecasts under "
            "`forecasts/YYYY/MM/`. Actual outcomes are stored separately under "
            "`outcomes/YYYY/MM/`. Never revise an issued forecast to incorporate "
            "later market data.\n\n"
            "The bootstrap intentionally excludes the original workbook and the "
            "full NSE daily report archives. Public deployment must not copy this "
            "repository into GitHub Pages.\n"
        )
        for directory in ("sessions", "forecasts", "outcomes"):
            (staging / directory).mkdir()
            (staging / directory / ".gitkeep").touch()
        staging.rename(state_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True,
                        help="New directory for the private live-state repository")
    parser.add_argument("--model-run", type=Path, required=True,
                        help="Verified reports/generated/one_day run to pin")
    args = parser.parse_args()
    manifest = bootstrap(args.state_dir, args.model_run)
    print(f"Created verified live store: {args.state_dir.resolve()}")
    print(f"Historical close through {manifest['baseline_last_session']}; "
          f"model SHA-256 {manifest['one_day_model_sha256']}")


if __name__ == "__main__":
    main()
