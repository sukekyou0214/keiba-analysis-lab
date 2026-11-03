"""Offline tests for historical horse-result batches."""
import unittest
import pandas as pd
from keiba_affinity_pipeline import collect_batch


class HistoryBatchTests(unittest.TestCase):
    def setUp(self):
        self.horses = pd.DataFrame([
            {"馬ID": "2020000001", "馬名": "甲"},
            {"馬ID": "2020000002", "馬名": "乙"},
            {"馬ID": "2020000003", "馬名": "丙"},
        ])

    def test_bounded_and_successful(self):
        calls = []
        def fetch(hid, name):
            calls.append(hid)
            return pd.DataFrame([{"馬ID": hid, "馬名": name}])
        df, log = collect_batch(self.horses, fetch, limit=2, sleeper=lambda _: None)
        self.assertEqual(len(df), 2)
        self.assertEqual(len(log), 2)
        self.assertEqual(len(calls), 2)

    def test_skips_completed(self):
        df, log = collect_batch(
            self.horses, lambda hid, name: pd.DataFrame([{"馬ID": hid}]),
            completed={"2020000001"}, limit=1, sleeper=lambda _: None,
        )
        self.assertEqual(log.iloc[0]["horse_id"], "2020000002")

    def test_rejects_wrong_identity(self):
        df, log = collect_batch(
            self.horses,
            lambda hid, name: pd.DataFrame([{"馬ID": "9999999999"}]),
            limit=1, sleeper=lambda _: None,
        )
        self.assertTrue(df.empty)
        self.assertEqual(log.iloc[0]["status"], "失敗")

    def test_failure_recorded(self):
        def fail(hid, name):
            raise ValueError("Test failure")
        df, log = collect_batch(self.horses, fail, limit=1, sleeper=lambda _: None)
        self.assertTrue(df.empty)
        self.assertIn("Test failure", log.iloc[0]["reason"])

    def test_rejects_unbounded(self):
        with self.assertRaises(ValueError):
            collect_batch(self.horses, lambda h, n: None, limit=150)


if __name__ == "__main__":
    unittest.main()
