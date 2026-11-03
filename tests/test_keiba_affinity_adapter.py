"""Contract tests for legacy CSV bridge and data-quality reporting."""
import unittest
import pandas as pd

from keiba_affinity_adapter import build_affinity_inputs, analyze_legacy_csv


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.rid = "202405041111"
        self.raw = pd.DataFrame([
            {"レースID": self.rid, "馬ID": "2020100001", "日付": "2024-10-01",
             "レース名": "対象", "着順_数値": 1, "人気": 4},
            {"レースID": self.rid, "馬ID": "2020100002", "日付": "2024-10-01",
             "レース名": "対象", "着順_数値": 4, "人気": 4},
            {"レースID": "202304041111", "馬ID": "2020100001", "日付": "2023-09-01",
             "レース名": "前哨戦", "着順_数値": 2, "人気": 2},
        ])

    def test_target_and_history(self):
        t, h, q = build_affinity_inputs(self.raw, [self.rid])
        self.assertEqual(len(t), 2)
        self.assertEqual(len(h), 3)
        self.assertEqual(q.iloc[0]["target_field_complete"], "未検証（公式頭数との照合が必要）")

    def test_missing_popularity_not_fabricated(self):
        raw = self.raw.drop(columns=["人気"])
        t, h, q = build_affinity_inputs(raw, [self.rid])
        self.assertTrue(t["popularity"].isna().all())
        self.assertEqual(q.iloc[0]["target_missing_popularity"], 2)

    def test_missing_target_rejected(self):
        with self.assertRaises(ValueError):
            build_affinity_inputs(self.raw, ["202505041111"])

    def test_invalid_identifier_rejected(self):
        with self.assertRaises(ValueError):
            build_affinity_inputs(self.raw, ["invalid"])

    def test_duplicate_rejected(self):
        raw = pd.concat([self.raw, self.raw.iloc[[0]]], ignore_index=True)
        with self.assertRaises(ValueError):
            build_affinity_inputs(raw, [self.rid])

    def test_incomplete_identity_audited(self):
        raw = self.raw.copy()
        raw.loc[2, "馬ID"] = ""
        t, h, q = build_affinity_inputs(raw, [self.rid])
        self.assertEqual(len(h), 2)
        self.assertEqual(q.iloc[0]["invalid_identity_date_rows"], 1)

    def test_end_to_end_candidate(self):
        result, q = analyze_legacy_csv(self.raw, [self.rid])
        self.assertEqual(result["race_name"].tolist(), ["前哨戦"])
        self.assertEqual(result.iloc[0]["n"], 1)
        self.assertTrue(result.iloc[0]["interpretation"].startswith("判定保留"))


if __name__ == "__main__":
    unittest.main()
