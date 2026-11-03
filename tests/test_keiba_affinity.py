"""Synthetic regression tests for Ver.21 affinity (no network access)."""
import unittest
import pandas as pd
from keiba_affinity import analyze_race_affinity, to_csv_bytes


class AffinityTests(unittest.TestCase):
    def setUp(self):
        rows = []
        past = []
        # 12 target races, with one exposed and one unexposed same popularity band.
        for i in range(12):
            day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=14 * i)
            rid = f"T{i:03d}"
            for marker, finish in (("A", 1), ("B", 5)):
                horse = f"{marker}{i}"
                rows.append(dict(race_id=rid, horse_id=horse, race_date=day,
                                 finish=finish, popularity=4))
                if marker == "A":
                    past.append(dict(race_id=f"P{i:03d}", horse_id=horse,
                                     race_date=day - pd.Timedelta(days=30),
                                     race_name="共通前走", finish=2, popularity=5))
                if marker == "B":
                    past.append(dict(race_id=f"F{i:03d}", horse_id=horse,
                                     race_date=day + pd.Timedelta(days=30),
                                     race_name="未来のレース", finish=1, popularity=1))
        self.targets = pd.DataFrame(rows)
        self.history = pd.DataFrame(past)

    def test_candidates_and_matching(self):
        result = analyze_race_affinity(self.targets, self.history)
        self.assertEqual(result["race_name"].tolist(), ["共通前走"])
        row = result.iloc[0]
        self.assertEqual(row["n"], 12)
        self.assertEqual(row["comparison_n"], 12)
        self.assertAlmostEqual(row["place_rate"], 1.0)
        self.assertAlmostEqual(row["adjusted_place_difference"], 1.0)
        self.assertEqual(row["interpretation"], "好相性候補")

    def test_future_excluded(self):
        result = analyze_race_affinity(self.targets, self.history)
        self.assertNotIn("未来のレース", result["race_name"].tolist())

    def test_same_day_excluded(self):
        same_day = dict(race_id="SAME", horse_id="A0",
                        race_date=self.targets.iloc[0]["race_date"],
                        race_name="同日未確定", finish=2, popularity=1)
        history = pd.concat([self.history, pd.DataFrame([same_day])], ignore_index=True)
        result = analyze_race_affinity(self.targets, history)
        self.assertNotIn("同日未確定", result["race_name"].tolist())

    def test_duplicate_rejected(self):
        targets = pd.concat([self.targets, self.targets.iloc[[0]]])
        with self.assertRaises(ValueError):
            analyze_race_affinity(targets, self.history)

    def test_missing_date_rejected(self):
        targets = self.targets.copy()
        targets.loc[0, "race_date"] = None
        with self.assertRaises(ValueError):
            analyze_race_affinity(targets, self.history)

    def test_small_samples_not_asserted(self):
        result = analyze_race_affinity(self.targets.head(4), self.history)
        self.assertTrue(result.iloc[0]["interpretation"].startswith("判定保留"))

    def test_csv_encoding(self):
        data = analyze_race_affinity(self.targets, self.history)
        self.assertTrue(to_csv_bytes(data).startswith(bytes([0xEF, 0xBB, 0xBF])))


if __name__ == "__main__":
    unittest.main()
