"""One-shot 2016–2025 real-data acceptance audit.

Requests are limited to 10 race-result pages and one result page per unique
horse in those fields, with a delay between horse requests. No retries,
anti-bot bypass or guessed race IDs. Writes test evidence as a private
GitHub Actions artifact, not a committed dataset.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pandas as pd

from keiba_affinity_collect import fetch_target_history
from keiba_affinity_pipeline import collect_batch
from keiba_affinity_store import pack_checkpoint, load_checkpoint
from keiba_affinity_workflow import analyze_verified_bundle
from keiba_core import convert_auto, get_results

SPECIAL = "https://race.netkeiba.com/special/index.html?id=0109"
OUT = Path("ver21-audit-output")


def main():
    OUT.mkdir(exist_ok=True)
    targets, years = fetch_target_history(
        SPECIAL, 2016, 2025, delay_seconds=1.0, max_races=10,
    )
    targets.to_csv(OUT / "targets.csv", index=False, encoding="utf-8-sig")
    years.to_csv(OUT / "year_manifest.csv", index=False, encoding="utf-8-sig")
    print("YEARS", years.to_dict(orient="records"), flush=True)
    if len(targets) != 144 or len(years) != 10 or not years["status"].eq("成功").all():
        raise ValueError("Incomplete historical target-field verification")

    ids = targets["馬ID"].astype(str).unique()
    expected = len(ids)
    print("TARGET", len(targets), "entries; unique horse pages", expected, flush=True)
    done = set()
    raw_frames, logs = [], []
    while len(done) < expected:
        raw, log = collect_batch(
            targets, get_results, completed=done,
            limit=20, delay=1.0,
        )
        if log.empty:
            raise ValueError("No progress in horse collection")
        raw_frames.append(raw)
        logs.append(log)
        done.update(log["horse_id"].astype(str))
        print("HORSES processed", len(done), "/", expected,
              "success", int(pd.concat(logs)["status"].eq("成功").sum()), flush=True)
        if len(done) < expected:
            time.sleep(1.0)
    horse_manifest = pd.concat(logs, ignore_index=True)
    horse_manifest.to_csv(OUT / "horse_manifest.csv", index=False, encoding="utf-8-sig")
    if not horse_manifest["status"].eq("成功").all():
        print("FAILED HORSES:", horse_manifest.loc[
            ~horse_manifest["status"].eq("成功")
        ].to_dict(orient="records"), flush=True)
        raise ValueError("At least one horse result fetch failed")

    histories = convert_auto(pd.concat(raw_frames, ignore_index=True))
    histories.to_csv(OUT / "horse_histories.csv", index=False, encoding="utf-8-sig")
    result, quality = analyze_verified_bundle(
        targets, years, histories, horse_manifest,
    )
    quality.to_csv(OUT / "quality.csv", index=False, encoding="utf-8-sig")
    result.to_csv(OUT / "affinity_candidates.csv", index=False, encoding="utf-8-sig")
    archive = pack_checkpoint({
        "targets": targets, "target_log": years,
        "horse_raw": histories, "horse_log": horse_manifest, "affinity": result,
    })
    (OUT / "checkpoint.zip").write_bytes(archive)
    restored = load_checkpoint(archive)
    second, second_quality = analyze_verified_bundle(
        restored["targets"], restored["target_log"],
        restored["horse_raw"], restored["horse_log"],
    )
    pd.testing.assert_frame_equal(result, second, check_dtype=False)
    summary = {
        "years": len(years), "target_entries": len(targets),
        "unique_horses": expected, "horse_pages_success": len(horse_manifest),
        "history_rows": len(histories), "candidate_count": len(result),
        "unverified_history_rows": int(quality.iloc[0]["excluded_unverifiable_history_rows"]),
        "checkpoint_bytes": len(archive),
        "note": "Exploratory associations; results not predictive validation",
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("TEN_YEAR_FULL_E2E_PASS", summary, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("TEN_YEAR_FULL_E2E_FAILED", repr(error), flush=True)
        sys.exit(1)
