"""One-year, two-request live diagnostic; no retries or anti-bot bypass.

Run only when explicitly selected by CI for a bounded smoke test.
This does not certify 10 years of complete history.
"""
import sys
import os
import requests
from keiba_affinity_collect import (
    UA, discover_historical_entries, parse_historical_result,
)

SPECIAL = "https://race.netkeiba.com/special/index.html?id=0109"


def read(url):
    response = requests.get(url, headers=UA, timeout=15)
    print("HTTP status:", response.status_code, "bytes:", len(response.content),
          "url host:", requests.utils.urlparse(response.url).hostname)
    response.raise_for_status()
    if requests.utils.urlparse(response.url).hostname != "race.netkeiba.com":
        raise ValueError("Unexpected redirect host")
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def main():
    if os.environ.get("KEIBA_LIVE_E2E") == "1":
        from keiba_affinity_collect import fetch_target_history
        from keiba_affinity_pipeline import collect_batch
        from keiba_affinity_store import pack_checkpoint, load_checkpoint
        from keiba_affinity_workflow import analyze_verified_bundle
        from keiba_core import get_results, convert_auto
        target, target_log = fetch_target_history(
            SPECIAL, 2025, 2025, delay_seconds=1.0
        )
        if len(target) != 14 or not target_log["status"].eq("成功").all():
            raise ValueError("2025 target field unavailable")
        raw, log = collect_batch(
            target, get_results, limit=20, delay=1.0
        )
        print("HORSE MANIFEST:", log.to_dict(orient="records"), flush=True)
        if len(log) != 14 or not log["status"].eq("成功").all():
            raise ValueError("Horse history collection incomplete")
        converted = convert_auto(raw)
        output, quality = analyze_verified_bundle(
            target, target_log, converted, log
        )
        checkpoint = pack_checkpoint({
            "targets": target, "target_log": target_log,
            "horse_raw": converted, "horse_log": log, "affinity": output,
        })
        restored = load_checkpoint(checkpoint)
        retested, restored_quality = analyze_verified_bundle(
            restored["targets"], restored["target_log"],
            restored["horse_raw"], restored["horse_log"]
        )
        if not output.equals(retested):
            raise ValueError("Restored output differs from original affinity")
        print("PASS: 2025 all 14 horses, history rows:", len(converted),
              "affinity candidates:", len(output),
              "saved bytes:", len(checkpoint), flush=True)
        print("QUALITY:", quality.to_dict(orient="records"), flush=True)
        return
    if os.environ.get("KEIBA_LIVE_FULL") == "1":
        from keiba_affinity_collect import fetch_target_history
        results, log = fetch_target_history(
            SPECIAL, 2016, 2025, delay_seconds=1.0, max_races=10
        )
        for _, row in log.iterrows():
            print("YEAR:", row["year"], row["status"], "starters:",
                  row["entrants"], "note:", row["reason"], flush=True)
        if len(log) != 10 or not log["status"].eq("成功").all():
            raise ValueError("Historical 10-year acquisition incomplete")
        counts = results.groupby(results["レースID"].str[:4]).size().to_dict()
        print("Verified starters by year:", counts, flush=True)
        if set(counts) != {str(y) for y in range(2016, 2026)}:
            raise ValueError("Missing target year")
        if len(results) != 144:
            raise ValueError(f"Unexpected sum of starters: {len(results)}")
        print("PASS: ten verified years, 144 actual starters", flush=True)
        return
    special = read(SPECIAL)
    entry = discover_historical_entries(special, 2025, 2025).get(2025)
    if not entry:
        raise ValueError("2025 target race is absent from historical listing")
    print("2025 listing:", entry)
    rid = entry["race_id"]
    table = read("https://race.netkeiba.com/race/result.html?race_id=" + rid)
    results = parse_historical_result(table, rid)
    print("2025 observed starters:", len(results),
          "nonstarters:", len(results.attrs.get("nonstarters", [])))
    if entry["expected_entrants"] != len(results):
        raise ValueError("Count mismatch")
    if entry["expected_date"] != results["日付"].iloc[0]:
        raise ValueError("Date mismatch")
    # Verify one horse-history fetch, not a bulk scrape.
    from keiba_core import get_results, convert_auto
    horse_id = str(results.iloc[0]["馬ID"])
    horse_name = str(results.iloc[0]["馬名"])
    history_raw = get_results(horse_id, horse_name)
    history = convert_auto(history_raw)
    if history.empty or not history["馬ID"].astype(str).eq(horse_id).all():
        raise ValueError("Horse history identity check failed")
    if not history["レースID"].astype(str).eq(rid).any():
        raise ValueError("Target-race history row missing")
    print("PASS: one horse history fetched, rows:", len(history),
          "target race recorded:", rid, flush=True)
    print("PASS: verified one historical race, not full backfill")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, requests.RequestException) as error:
        print("LIVE PROBE FAILED:", repr(error), file=sys.stderr)
        sys.exit(1)
