"""Offline tests for bounded historical result collection."""
import unittest
from unittest.mock import patch

from keiba_affinity_collect import (
    validate_special_url, discover_historical_ids,
    parse_historical_result, fetch_target_history,
)


def special_html():
    return ('<h2>過去のレース結果</h2><table>'
            '<tr><td>2025/11/02</td><td><a href="/race/202505041111/">天皇賞</a></td><td>東京</td><td>芝2000</td><td>8</td></tr>'
            '<tr><td>2024/10/27</td><td><a href="/race/202405041111/">天皇賞</a></td><td>東京</td><td>芝2000</td><td>8</td></tr>'
            '</table>')


def result_html():
    def tr(i):
        cells = [str(i), "1", str(i), f'<a href="/horse/20200000{i:02d}/">馬{i}</a>',
                 "牡4", "57", "騎手", "1:59.0", "差", str(i), "5.0"]
        return '<tr class="HorseList">' + "".join(f'<td>{c}</td>' for c in cells) + '</tr>'
    return ('<html><h1 class="RaceName">試験重賞</h1>'
            '<p>2025年11月2日</p><table class="RaceTable01">'
            + "".join(tr(i) for i in range(1, 9)) + '</table></html>')


class CollectorTests(unittest.TestCase):
    def test_only_allowed_special_url(self):
        self.assertEqual(validate_special_url(
            "https://race.netkeiba.com/special/index.html?id=0109"),
            "https://race.netkeiba.com/special/index.html?id=0109")
        for url in ("http://race.netkeiba.com/special/index.html?id=0109",
                    "https://evil.example/special/index.html?id=0109",
                    "https://race.netkeiba.com/race/result.html?race_id=202505041111"):
            with self.assertRaises(ValueError):
                validate_special_url(url)

    def test_discovery(self):
        self.assertEqual(discover_historical_ids(special_html(), 2024, 2025),
                         {2024: "202405041111", 2025: "202505041111"})

    def test_parse_result(self):
        df = parse_historical_result(result_html(), "202505041111")
        self.assertEqual(len(df), 8)
        self.assertEqual(df.iloc[0]["馬ID"], "2020000001")
        self.assertEqual(df.iloc[0]["人気"], 1)
        self.assertEqual(df.iloc[0]["日付"], "2025-11-02")

    def test_year_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            parse_historical_result(result_html(), "202405041111")

    def test_expected_entrant_count_mismatch(self):
        class FakeResponse:
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            def __init__(self, text):
                self.text = text
            def raise_for_status(self):
                pass

        class FakeSession:
            def get(self, url, **kwargs):
                html = special_html().replace("<td>8</td>", "<td>9</td>") if "special/index" in url else result_html()
                return FakeResponse(html)

        with patch("keiba_affinity_collect.time.sleep"):
            df, manifest = fetch_target_history(
                "https://race.netkeiba.com/special/index.html?id=0109",
                2025, 2025, session=FakeSession(), delay_seconds=0.5)
        self.assertTrue(df.empty)
        self.assertEqual(manifest.iloc[0]["status"], "失敗")
        self.assertIn("mismatch", manifest.iloc[0]["reason"])

    def test_request_budget_rejected(self):
        with self.assertRaises(ValueError):
            fetch_target_history("https://race.netkeiba.com/special/index.html?id=0109",
                                 2010, 2025)

    def test_manifest_preserves_missing_year(self):
        class FakeResponse:
            text = ""
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            def raise_for_status(self):
                pass

        class FakeSession:
            def get(self, url, **kwargs):
                response = FakeResponse()
                response.text = special_html() if "special/index" in url else result_html()
                return response

        with patch("keiba_affinity_collect.time.sleep"):
            df, manifest = fetch_target_history(
                "https://race.netkeiba.com/special/index.html?id=0109",
                2023, 2025, session=FakeSession(), delay_seconds=0.5)
        self.assertEqual(len(df), 8)
        self.assertEqual(manifest["status"].tolist(), ["失敗", "失敗", "成功"])
        self.assertEqual(manifest["entrants"].tolist(), [0, 0, 8])


if __name__ == "__main__":
    unittest.main()
