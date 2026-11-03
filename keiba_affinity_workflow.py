"""Strict, offline end-to-end orchestration for Ver.21 race-affinity research.

Inputs are already collected / converted results; no requests or UI calls here.
Return a result only after BOTH acquisition manifests and every row validate.
"""
from __future__ import annotations

import pandas as pd

from keiba_affinity import analyze_race_affinity
from keiba_affinity_adapter import normalize_legacy_results
from keiba_affinity_pipeline import complete_horse_manifest
from keiba_affinity_store import audit_checkpoint


def analyze_verified_bundle(targets: pd.DataFrame, target_log: pd.DataFrame,
                            histories: pd.DataFrame, horse_log: pd.DataFrame):
    """Compute candidates using historical, strictly pre-target exposures only.

    The history parameter is a converted DataFrame with the usual Japanese
    columns. All target fields and individual horse acquisitions must be
    present and auditable. Results are exploratory, not predictive proof.
    """
    bundle = {
        "targets": targets, "target_log": target_log,
        "horse_raw": histories, "horse_log": horse_log,
    }
    issues = audit_checkpoint(bundle)
    if issues:
        raise ValueError("Checkpoint quality gate: " + "; ".join(issues))
    if not target_log["status"].eq("成功").all():
        raise ValueError("At least one historical target year was not collected")
    if target_log["race_id"].astype("string").isna().any():
        raise ValueError("Target manifest contains missing race IDs")
    if set(target_log["race_id"].astype(str)) != set(targets["レースID"].astype(str)):
        raise ValueError("Historical target race manifest does not match results")
    if not complete_horse_manifest(targets, horse_log, histories):
        raise ValueError("Not all horse histories were collected and checked")
    t = normalize_legacy_results(targets)
    h = normalize_legacy_results(histories)
    # Some overseas races have no 12-digit JRA/netkeiba race ID.
    # Never invent identifiers or accidentally collapse multiple blank IDs.
    # Only verified-ID runs can be used for race-name affinity comparisons.
    verifiable = h["race_id"].str.fullmatch(r"\d{12}", na=False)
    unverified_rows = int((~verifiable).sum())
    h = h.loc[verifiable].copy()
    if h.empty:
        raise ValueError("No horse history rows with verifiable race IDs")
    if t["race_date"].isna().any() or h["race_date"].isna().any():
        raise ValueError("Cannot guarantee time isolation with undated rows")
    if t["finish"].isna().any() or t["finish"].lt(1).any():
        raise ValueError("Target outcomes are incomplete or non-numeric")
    if t.duplicated(["race_id", "horse_id"]).any() or h.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Duplicated horse/race histories")
    # Horse histories may include target outcomes, but these must not replace
    # the independently verified target rows.
    h = h.loc[~h["race_id"].isin(set(t["race_id"]))].copy()
    # Non-target races with missing race IDs or dates would make coverage and
    # identity unverifiable. Refuse the whole bundle instead of silently drop.
    if (h["race_id"].isna().any()
            or not h["race_id"].str.fullmatch(r"\d{12}", na=False).all()
            or not h["horse_id"].str.fullmatch(r"\d{10}", na=False).all()):
        raise ValueError("Invalid historical IDs")
    if (t["race_id"].isna().any()
            or not t["race_id"].str.fullmatch(r"\d{12}", na=False).all()
            or not t["horse_id"].str.fullmatch(r"\d{10}", na=False).all()):
        raise ValueError("Invalid target IDs")
    if not set(h["horse_id"]).issubset(set(t["horse_id"])):
        raise ValueError("Unexpected horses in historical data")
    # analyze_race_affinity applies an additional strict pre-target date filter
    # per race (same-day results are never used as historical predictors).
    output = analyze_race_affinity(t, h)
    return output, pd.DataFrame([{
        "target_races": int(t["race_id"].nunique()),
        "target_entrants": int(len(t)),
        "horse_histories_collected": int(h["horse_id"].nunique()),
        "historical_rows": int(len(h)),
        "excluded_unverifiable_history_rows": unverified_rows,
        "history_id_coverage": "partial" if unverified_rows else "no missing race IDs",
        "future_and_same_day_policy": "excluded by date in engine",
        "target_manifest": "verified",
        "horse_manifest": "verified",
        "historical_source_completeness": "provider-dependent / not independently verified",
    }])
