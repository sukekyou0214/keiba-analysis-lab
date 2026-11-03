"""Ver.21: historical race-affinity candidate analysis.

Inputs are normalized DataFrames; this module never scrapes or performs I/O.
target_results: one row per horse in a historical TARGET race, with
  race_id, horse_id, race_date, finish, popularity, and optional venue/distance/age.
history_results: historical runs for those horses, with
  race_id, horse_id, race_date, race_name, finish, popularity.
Only runs dated STRICTLY before each target start are allowed. The caller must
supply historical target entrants, including those without matched prior runs.
"""
from __future__ import annotations

import re
import pandas as pd

VERSION = "21.0.0-affinity-alpha"
BASE_REQUIRED = {"race_id", "horse_id", "race_date", "finish", "popularity"}
HISTORY_REQUIRED = BASE_REQUIRED | {"race_name"}
OUTPUT_COLUMNS = [
    "race_name", "exposure", "n", "wins", "places", "win_rate",
    "place_rate", "comparison_n", "comparison_place_rate",
    "adjusted_place_difference", "missing_popularity_rate",
    "missing_history_rate", "confidence", "interpretation",
    "analysis_version",
]


def _validate(df: pd.DataFrame, required: set[str], name: str) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"{name} must be a pandas DataFrame")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name}: missing columns {sorted(missing)}")
    out = df.copy()
    out["race_id"] = out["race_id"].astype("string").str.strip()
    out["horse_id"] = out["horse_id"].astype("string").str.strip()
    out["race_date"] = pd.to_datetime(out["race_date"], errors="coerce")
    out["finish"] = pd.to_numeric(out["finish"], errors="coerce")
    out["popularity"] = pd.to_numeric(out["popularity"], errors="coerce")
    if out["race_date"].isna().any():
        raise ValueError(f"{name}: invalid or missing race_date; cannot guarantee time isolation")
    if out[["race_id", "horse_id"]].isna().any().any() or (
        out["race_id"].eq("") | out["horse_id"].eq("")
    ).any():
        raise ValueError(f"{name}: missing race_id/horse_id")
    if out.duplicated(["race_id", "horse_id"]).any():
        raise ValueError(f"{name}: duplicate race_id/horse_id")
    return out


def _band(p):
    if pd.isna(p) or p < 1 or p != int(p):
        return "unknown"
    return "1-3" if p <= 3 else "4-6" if p <= 6 else "7-9" if p <= 9 else "10+"


def analyze_race_affinity(
    target_results: pd.DataFrame,
    history_results: pd.DataFrame,
    minimum_n: int = 10,
    minimum_comparison_n: int = 10,
    max_years: int = 10,
) -> pd.DataFrame:
    """Find candidate historical race associations, never causal assertions.

    Reference group: entrants in the SAME target race(s) and popularity band
    without the named prior-race exposure; reweighted to exposed distribution.
    Missing popularity and absent history are not treated as known negatives.
    """
    if minimum_n < 1 or minimum_comparison_n < 1 or max_years < 1:
        raise ValueError("sample thresholds and max_years must be positive")
    t = _validate(target_results, BASE_REQUIRED, "target_results")
    h = _validate(history_results, HISTORY_REQUIRED, "history_results")
    t = t.loc[t["finish"].ge(1)].copy()
    t = t.loc[t["race_date"] >= t["race_date"].max() - pd.DateOffset(years=max_years)]
    if t.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    t["band"] = t["popularity"].map(_band)
    t["placed"] = t["finish"].le(3).astype(int)
    t["won"] = t["finish"].eq(1).astype(int)
    h["race_name"] = h["race_name"].astype("string").str.strip()
    h = h.loc[h["race_name"].notna() & h["race_name"].ne("") & h["finish"].ge(1)]
    # An entry in a future race can never become a historical feature.
    # A same-day result is also excluded because race order is unknown.
    pairs = t[["race_id", "horse_id", "race_date"]].merge(
        h[["horse_id", "race_id", "race_date", "race_name", "finish", "popularity"]],
        on="horse_id", suffixes=("_target", "_history"), how="left",
    )
    pairs = pairs.loc[
        pairs["race_date_history"].notna()
        & pairs["race_date_history"].lt(pairs["race_date_target"])
        & pairs["race_id_history"].ne(pairs["race_id_target"])
    ]
    # Each runner contributes once per historical race name, regardless of repeats.
    exposure = pairs[["race_id_target", "horse_id", "race_name"]].drop_duplicates()
    if exposure.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    all_targets = t.rename(columns={"race_id": "race_id_target"})
    outputs = []
    for name, subset in exposure.groupby("race_name", sort=True):
        exposed_keys = pd.MultiIndex.from_frame(subset[["race_id_target", "horse_id"]])
        indices = pd.MultiIndex.from_frame(all_targets[["race_id_target", "horse_id"]])
        tagged = all_targets.copy()
        tagged["exposed"] = indices.isin(exposed_keys)
        observed = tagged.loc[tagged["exposed"]]
        n = len(observed)
        # These rates are unconditional DESCRIPTIVE stats, not adjusted effects.
        adjusted, comparison_n, comparison_places = [], 0, 0
        matched_exposed = 0
        for (race_id, band), group in tagged.groupby(["race_id_target", "band"], dropna=False):
            if band == "unknown":
                continue
            a = group.loc[group["exposed"]]
            b = group.loc[~group["exposed"]]
            if a.empty or b.empty:
                continue
            matched_exposed += len(a)
            comparison_n += len(b)
            comparison_places += int(b["placed"].sum())
            adjusted.extend([float(a["placed"].mean() - b["placed"].mean())] * len(a))
        # Distinguish incomplete coverage from a verified absence of a prior run.
        # History data completeness cannot be inferred from rows alone.
        missing_pop = float(observed["band"].eq("unknown").mean()) if n else float("nan")
        if n >= minimum_n and comparison_n >= minimum_comparison_n and matched_exposed >= minimum_n and missing_pop <= .2:
            conf = "暫定（要外部検証）"
        elif n >= 3:
            conf = "参考（小標本・比較不足）"
        else:
            conf = "判定保留"
        diff = sum(adjusted) / len(adjusted) if adjusted else float("nan")
        interpretation = "比較不可"
        if pd.notna(diff):
            interpretation = "好相性候補" if diff > 0 else "悪相性候補" if diff < 0 else "差なし"
        if conf != "暫定（要外部検証）":
            interpretation = "判定保留（" + interpretation + "）"
        outputs.append({
            "race_name": str(name), "exposure": "過去出走経験",
            "n": n, "wins": int(observed["won"].sum()),
            "places": int(observed["placed"].sum()),
            "win_rate": float(observed["won"].mean()),
            "place_rate": float(observed["placed"].mean()),
            "comparison_n": comparison_n,
            "comparison_place_rate": comparison_places / comparison_n if comparison_n else float("nan"),
            "adjusted_place_difference": diff,
            "missing_popularity_rate": missing_pop,
            # Unknown without a source-wide acquisition manifest, NOT zero.
            "missing_history_rate": float("nan"),
            "confidence": conf, "interpretation": interpretation,
            "analysis_version": VERSION,
        })
    return pd.DataFrame(outputs, columns=OUTPUT_COLUMNS).sort_values(
        ["n", "race_name"], ascending=[False, True], ignore_index=True
    )


def to_csv_bytes(result: pd.DataFrame) -> bytes:
    """UTF-8 BOM for reliable Japanese CSV opening on iPhone/Excel."""
    return result.to_csv(index=False).encode("utf-8-sig")
