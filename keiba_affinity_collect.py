"""Conservative, inspectable historical target-race acquisition for Ver.21.

No guessed IDs, no hidden fallback, no anti-bot bypass. Only explicitly supplied
netkeiba special-page URLs, bounded year range and sequential public pages.
Before production use, confirm the provider's current terms and access policy.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "KeibaAnalysisLab/21 (research; requests limited to user action)"}
RACE_ID = re.compile(r"/race/(\d{12})(?:/|[?#]|$)")
HORSE_ID = re.compile(r"/horse/(\d{10})(?:/|[?#]|$)")


def validate_special_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com"
            or parsed.path != "/special/index.html"
            or not re.fullmatch(r"\d{1,5}", dict(
                item.split("=", 1) for item in parsed.query.split("&")
                if "=" in item
            ).get("id", ""))):
        raise ValueError("Only https://race.netkeiba.com/special/index.html?id=... is supported")
    return url


def discover_historical_ids(html: str, first_year: int, last_year: int) -> dict[int, str]:
    if not 2000 <= first_year <= last_year <= 2100 or last_year-first_year > 15:
        raise ValueError("Invalid year range (maximum 16 calendar years)")
    soup = BeautifulSoup(html, "html.parser")
    heading = next((h for h in soup.find_all(["h2", "h3", "h4"])
                    if "過去のレース結果" in h.get_text(" ", strip=True)), None)
    table = heading.find_next("table") if heading else None
    if table is None:
        raise ValueError("Historical race-results table is missing")
    ids = {}
    for link in table.select('a[href*="/race/"]'):
        found = RACE_ID.search(link.get("href", ""))
        if not found:
            continue
        rid = found.group(1)
        year = int(rid[:4])
        if first_year <= year <= last_year:
            if year in ids and ids[year] != rid:
                raise ValueError(f"Ambiguous historical race ID for {year}")
            ids[year] = rid
    return ids


def discover_historical_entries(html: str, first_year: int, last_year: int):
    """Race IDs plus expected starter counts from the historical summary table."""
    ids = discover_historical_ids(html, first_year, last_year)
    soup = BeautifulSoup(html, "html.parser")
    heading = next((h for h in soup.find_all(["h2", "h3", "h4"])
                    if "過去のレース結果" in h.get_text(" ", strip=True)), None)
    table = heading.find_next("table") if heading else None
    counts = {}
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        # Typical special page columns: date, race, venue, distance, runners.
        if len(cells) < 5:
            continue
        for a in row.select('a[href*="/race/"]'):
            hit = RACE_ID.search(a.get("href", ""))
            if not hit:
                continue
            rid = hit.group(1)
            if rid != ids.get(int(rid[:4])):
                continue
            number = re.fullmatch(r"\s*(\d{1,2})\s*", cells[4].get_text(" ", strip=True))
            if number:
                counts[int(rid[:4])] = int(number.group(1))
    return {year: {"race_id": rid, "expected_entrants": counts.get(year)}
            for year, rid in ids.items()}


def _number(text):
    # Non-finishes and nonnumeric strings remain missing, not position 0.
    txt = str(text).replace(",", "").strip()
    return float(txt) if re.fullmatch(r"\d+(?:\.\d+)?", txt) else float("nan")


def parse_historical_result(html: str, race_id: str) -> pd.DataFrame:
    if not re.fullmatch(r"\d{12}", str(race_id)):
        raise ValueError("Invalid 12-digit race ID")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.RaceTable01")
    if table is None:
        raise ValueError(f"{race_id}: result table not found")
    rows = []
    for tr in table.select("tr.HorseList"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 11:
            raise ValueError(f"{race_id}: incomplete row: {len(cells)} cells")
        anchor = cells[3].select_one('a[href*="/horse/"]')
        match = HORSE_ID.search(anchor.get("href", "")) if anchor else None
        if not match:
            raise ValueError(f"{race_id}: horse ID unavailable")
        finish = _number(cells[0].get_text(" ", strip=True))
        popularity = _number(cells[9].get_text(" ", strip=True))
        rows.append({
            "レースID": race_id, "馬ID": match.group(1),
            "馬名": anchor.get_text(" ", strip=True),
            "日付": pd.NA,  # populated from source's dated metadata, not guessed
            "レース名": "", "着順_数値": finish, "人気": popularity,
            "単勝オッズ": _number(cells[10].get_text(" ", strip=True)),
        })
    if not 5 <= len(rows) <= 22:
        raise ValueError(f"{race_id}: implausible entrant count {len(rows)}")
    frame = pd.DataFrame(rows)
    if frame["馬ID"].duplicated().any():
        raise ValueError(f"{race_id}: duplicated horse IDs")
    if frame["着順_数値"].notna().sum() < 5:
        raise ValueError(f"{race_id}: insufficient known finish positions")
    if frame["人気"].notna().sum() < len(frame) - 1:
        raise ValueError(f"{race_id}: insufficient popularity coverage")
    # Never infer race date from the year portion of race ID.
    # Match a date declared in the page itself; abort if not available.
    raw = soup.get_text(" ", strip=True)
    date = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", raw)
    if not date:
        raise ValueError(f"{race_id}: actual race date not found")
    value = pd.Timestamp(int(date[1]), int(date[2]), int(date[3]))
    if value.year != int(race_id[:4]):
        raise ValueError(f"{race_id}: race date/year mismatch")
    frame["日付"] = value.strftime("%Y-%m-%d")
    title = soup.select_one("h1.RaceName")
    if not title or not title.get_text(strip=True):
        raise ValueError(f"{race_id}: race title not found")
    frame["レース名"] = title.get_text(" ", strip=True)
    return frame


def fetch_target_history(special_url: str, first_year: int, last_year: int,
                         *, session=None, delay_seconds: float = 1.0,
                         max_races: int = 10):
    """Return verified result rows and a year-by-year acquisition manifest.

    Missing years/results are explicit failures. No automatic per-horse history
    requests; this stage only acquires historical target race result tables.
    """
    validate_special_url(special_url)
    if not 0.5 <= delay_seconds <= 30 or not 1 <= max_races <= 10:
        raise ValueError("Invalid request budget or delay")
    if not 2000 <= first_year <= last_year <= 2100:
        raise ValueError("Invalid year range")
    if last_year - first_year + 1 > max_races:
        raise ValueError("Requested years exceed bounded race request budget")
    s = session or requests.Session()

    def fetch(url):
        response = s.get(url, headers=UA, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    entries = discover_historical_entries(fetch(special_url), first_year, last_year)
    results, manifest = [], []
    for index, year in enumerate(range(first_year, last_year + 1)):
        entry = entries.get(year)
        rid = entry["race_id"] if entry else None
        if rid is None:
            manifest.append({"year": year, "race_id": "", "status": "失敗",
                             "entrants": 0, "reason": "レースID未発見"})
            continue
        try:
            time.sleep(delay_seconds)
            frame = parse_historical_result(
                fetch(f"https://race.netkeiba.com/race/result.html?race_id={rid}"), rid
            )
            expected = entry["expected_entrants"]
            if expected is None:
                raise ValueError(f"{rid}: listed entrant count unavailable; cannot verify completeness")
            if expected != len(frame):
                raise ValueError(f"{rid}: entrant count mismatch, listed={expected} extracted={len(frame)}")
            results.append(frame)
            manifest.append({"year": year, "race_id": rid, "status": "成功",
                             "entrants": len(frame), "reason": ""})
        except (ValueError, requests.RequestException) as exc:
            manifest.append({"year": year, "race_id": rid, "status": "失敗",
                             "entrants": 0, "reason": str(exc)[:180]})
    combined = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return combined, pd.DataFrame(manifest)
