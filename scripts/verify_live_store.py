"""Verify the immutable starting assets of a private live-data store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nifty50_ta.model_common import sha256
from nifty50_ta.one_day_model import FEATURES


def verify(state_dir: Path) -> dict:
    state_dir = state_dir.resolve()
    manifest = json.loads((state_dir / "store_manifest.json").read_text())
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported live-store schema version.")
    hashes = manifest["files_sha256"]
    for relative_name, expected in hashes.items():
        path = (state_dir / relative_name).resolve()
        if not path.is_relative_to(state_dir) or sha256(path) != expected:
            raise ValueError(f"Live-store asset checksum mismatch: {relative_name}")
    lock = json.loads((state_dir / "baseline/release_lock.json").read_text())
    audit = json.loads((state_dir / "baseline/release_audit.json").read_text())
    model = json.loads((state_dir / "models/one_day/manifest.json").read_text())
    if lock["audit_status"] != "passed" or audit["status"] != "passed":
        raise ValueError("Historical release audit did not pass.")
    if (manifest["baseline_last_session"] != lock["source_window"]["last_date"]
            or manifest["baseline_price_rows"] != lock["counts"]["price_rows"]
            or manifest["baseline_stocks"] != lock["counts"]["stocks"]):
        raise ValueError("Live-store release metadata mismatch.")
    if model["release_lock_sha256"] != hashes["baseline/release_lock.json"]:
        raise ValueError("Pinned model references another release lock.")
    if (model["feature_order"] != FEATURES or model["model_sha256"] !=
            manifest["one_day_model_sha256"]):
        raise ValueError("Pinned model contract mismatch.")
    if hashes["models/one_day/one_day_lightgbm.txt"] != model["model_sha256"]:
        raise ValueError("Pinned model bytes do not match its manifest.")
    if hashes["baseline/processed_manifest.json"] != audit["manifest_sha256"]:
        raise ValueError("Historical processed manifest does not match its audit.")
    for name in ("daily_prices.csv.gz", "features.csv.gz", "quality_report.json",
                 "stock_dictionary.csv"):
        if hashes[f"baseline/{name}"] != lock["output_sha256"][name]:
            raise ValueError(f"Historical output does not match the release lock: {name}")
    for name in ("data_pipeline.json", "corporate_actions.csv", "nifty50_universe.json",
                 "outlier_reviews.csv"):
        if hashes[f"baseline/config/{name}"] != lock["versioned_config_sha256"][name]:
            raise ValueError(f"Historical config does not match the release lock: {name}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = verify(args.state_dir)
    print(f"Live store verified through {manifest['baseline_last_session']} "
          f"with {manifest['baseline_stocks']} stocks and model "
          f"{manifest['one_day_model_sha256'][:12]}.")


if __name__ == "__main__":
    main()
