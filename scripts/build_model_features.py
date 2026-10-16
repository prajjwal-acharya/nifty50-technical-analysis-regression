"""Rebuild both current model feature stores from the locked processed release.

Run: python -m scripts.build_model_features
Existing stores are checked against the rebuilt values rather than overwritten.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from nifty50_ta.cross_sectional_expansion import expand_cross_sectional_features
from nifty50_ta.feature_expansion import CUTOFF, expand_features
from nifty50_ta.five_day_model import engineer_features, write_feature_store
from nifty50_ta.model_common import ROOT, sha256


def run(root: Path = ROOT) -> dict:
    release_path = root / "docs/data-release-2026-10-15.json"
    source_path = root / "data/processed/features.csv.gz"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release["audit_status"] != "passed" or (
        sha256(source_path) != release["output_sha256"][source_path.name]
    ):
        raise ValueError("Locked base feature release failed checksum or audit")
    source = pd.read_csv(source_path, parse_dates=["date"], low_memory=False)
    source = source.loc[source.date.lt(CUTOFF)].copy()

    one_day = expand_cross_sectional_features(
        expand_features(source, legacy_raw_ohlcv=True)
    )
    one_day_path = root / "data/processed/features_cross_sectional_pre2026.csv.gz"
    write_feature_store(one_day, one_day_path)

    corrected = expand_cross_sectional_features(expand_features(source))
    five_day = engineer_features(corrected)
    five_day_path = root / "data/processed/features_5d_corrected_pre2026.csv.gz"
    write_feature_store(five_day, five_day_path)
    return {
        "one_day_store": str(one_day_path.relative_to(root)),
        "one_day_sha256": sha256(one_day_path),
        "five_day_store": str(five_day_path.relative_to(root)),
        "five_day_sha256": sha256(five_day_path),
        "pre2026_rows": len(source),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
