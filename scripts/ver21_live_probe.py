"""One-year, two-request live diagnostic; no retries or anti-bot bypass.

Run only when explicitly selected by CI for a bounded smoke test.
This does not certify 10 years of complete history.
"""
import sys
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
    print("PASS: verified one historical race, not full backfill")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, requests.RequestException) as error:
        print("LIVE PROBE FAILED:", repr(error), file=sys.stderr)
        sys.exit(1)
