"""Bridge legacy Japanese race-results CSVs to Ver.21 affinity data.

This module performs NO network access. Input is a collection of horse race
results already acquired using the existing application's permitted pipeline.
For valid comparison, the input must cover ALL target-race entrants, not just
the horses that happen to run in the upcoming race.
"""
from __future__ import annotations

import re
import unicodedata
import pandas as pd

from keiba_affinity import analyze_race_affinity

ALIASES = {
    "race_id": ("レースID", "race_id"),
    "horse_id": ("馬ID", "horse_id"),
    "race_date": ("日付", "race_date"),
    "race_name": ("レース名", "race_name"),
    "finish": ("着順_数値", "着 順", "着順", "finish"),
    "popularity": ("人気", "人 気", "popularity"),
}
TARGET_IDS_PATTERN = re.compile(r"^\d{12}$")


def normalize_name(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).strip()


def normalize_legacy_results(source: pd.DataFrame) -> pd.DataFrame:
    """Preserve source rows; do not fabricate missing popularity or results."""
    if not isinstance(source, pd.DataFrame):
        raise TypeError("source must be a DataFrame")
    frame = source.copy()
    frame.columns = [str(c).strip() for c in frame.columns]
    columns = {}
    for standard, alternatives in ALIASES.items():
        present = [col for col in alternatives if col in frame]
        if present:
            columns[standard] = present[0]
    required = {"race_id", "horse_id", "race_date", "race_name", "finish"}
    missing = required - set(columns)
    if missing:
        raise ValueError(f"Missing required historical columns: {sorted(missing)}")
    normalized = pd.DataFrame({k: frame[v] for k, v in columns.items()})
    if "popularity" not in normalized:
        normalized["popularity"] = pd.NA
    normalized["race_id"] = normalized["race_id"].astype("string").str.strip().str.replace(
        r"\.0$", "", regex=True
    )
    normalized["horse_id"] = normalized["horse_id"].astype("string").str.strip().str.replace(
        r"\.0$", "", regex=True
    )
    normalized["race_name"] = normalized["race_name"].map(normalize_name)
    normalized["race_date"] = pd.to_datetime(normalized["race_date"], errors="coerce")
    normalized["finish"] = pd.to_numeric(normalized["finish"], errors="coerce")
    normalized["popularity"] = pd.to_numeric(normalized["popularity"], errors="coerce")
    return normalized


def build_affinity_inputs(
    raw: pd.DataFrame,
    historical_target_ids: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return target entrants, history, and quality audit for CSV-based analysis.

    Raises if any target result is missing identity, date, or finish. This
    does NOT certify target-field completeness; that requires independent
    race-result entrant counts or a verified acquisition manifest.
    """
    target_ids = list(dict.fromkeys(str(x).strip() for x in historical_target_ids))
    if not target_ids or any(not TARGET_IDS_PATTERN.fullmatch(x) for x in target_ids):
        raise ValueError("Provide one or more 12-digit historical target race IDs")
    history = normalize_legacy_results(raw)
    bad_ids = history["race_id"].isna() | ~history["race_id"].str.fullmatch(r"\d{12}", na=False)
    bad_horses = history["horse_id"].isna() | ~history["horse_id"].str.fullmatch(r"\d{10}", na=False)
    invalid_dates = history["race_date"].isna()
    identity_valid = ~(bad_ids | bad_horses | invalid_dates)
    history = history.loc[identity_valid].copy()
    if history.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Duplicate horse/race rows; merge sources before analysis")
    targets = history.loc[history["race_id"].isin(target_ids)].copy()
    target_issues = (
        targets.empty or targets["finish"].isna().any()
        or targets["finish"].lt(1).any()
    )
    if target_issues or set(target_ids) - set(targets["race_id"]):
        raise ValueError("Missing or invalid target-race result rows; no analysis performed")
    quality = pd.DataFrame([{
        "input_rows": len(raw),
        "valid_identity_date_rows": len(history),
        "invalid_identity_date_rows": int((~identity_valid).sum()),
        "target_races_requested": len(target_ids),
        "target_races_found": int(targets["race_id"].nunique()),
        "target_entrant_rows": len(targets),
        "target_missing_popularity": int(targets["popularity"].isna().sum()),
        "target_field_complete": "未検証（公式頭数との照合が必要）",
        "history_complete": "未検証（全馬の戦績取得状況が必要）",
    }])
    return targets, history, quality


def analyze_legacy_csv(raw: pd.DataFrame, historical_target_ids: list[str]):
    target, history, quality = build_affinity_inputs(raw, historical_target_ids)
    return analyze_race_affinity(target, history), quality
