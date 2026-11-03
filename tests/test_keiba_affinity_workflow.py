"""Offline end-to-end regression: source rows -> gates -> candidates -> ZIP."""
import unittest

import pandas as pd

from keiba_affinity_workflow import analyze_verified_bundle
from keiba_affinity_store import pack_checkpoint, load_checkpoint


def sample():
    rid = "202405041111"
    a, b = "2020000001", "2020000002"
    target = pd.DataFrame([
        {"レースID": rid, "馬ID": a, "馬名": "甲", "日付": "2024-10-27",
         "レース名": "対象G1", "着順_数値": 1, "人気": 4},
        {"レースID": rid, "馬ID": b, "馬名": "乙", "日付": "2024-10-27",
         "レース名": "対象G1", "着順_数値": 6, "人気": 4},
    ])
    target_log = pd.DataFrame([{"race_id": rid, "status": "成功", "entrants": 2}])
    history = pd.DataFrame([
        {"レースID": "202305041111", "馬ID": a, "馬名": "甲", "日付": "2023-05-01",
         "レース名": "前哨戦", "着順_数値": 3, "人気": 2},
        {"レースID": "202305041112", "馬ID": b, "馬名": "乙", "日付": "2023-05-01",
         "レース名": "別の戦", "着順_数値": 5, "人気": 9},
        {"レースID": "202505041111", "馬ID": b, "馬名": "乙", "日付": "2025-05-01",
         "レース名": "未来戦", "着順_数値": 1, "人気": 1},
    ])
    horse_log = pd.DataFrame([
        {"horse_id": a, "status": "成功", "rows": 1},
        {"horse_id": b, "status": "成功", "rows": 2},
    ])
    return target, target_log, history, horse_log


class WorkflowTests(unittest.TestCase):
    def test_full_pipeline(self):
        t, tl, h, hl = sample()
        output, quality = analyze_verified_bundle(t, tl, h, hl)
        self.assertIn("前哨戦", output["race_name"].tolist())
        self.assertNotIn("未来戦", output["race_name"].tolist())
        self.assertEqual(quality.loc[0, "target_entrants"], 2)
        self.assertTrue(output["interpretation"].str.startswith("判定保留").all())

    def test_roundtrip_then_analyze(self):
        t, tl, h, hl = sample()
        restored = load_checkpoint(pack_checkpoint({
            "targets": t, "target_log": tl, "horse_raw": h, "horse_log": hl,
        }))
        output, _ = analyze_verified_bundle(
            restored["targets"], restored["target_log"],
            restored["horse_raw"], restored["horse_log"],
        )
        self.assertEqual(len(output), 2)

    def test_missing_year_blocks(self):
        t, tl, h, hl = sample()
        tl.loc[0, "status"] = "失敗"
        with self.assertRaisesRegex(ValueError, "target year"):
            analyze_verified_bundle(t, tl, h, hl)

    def test_missing_horse_blocks(self):
        t, tl, h, hl = sample()
        hl = hl.iloc[:1]
        with self.assertRaisesRegex(ValueError, "Not all horse"):
            analyze_verified_bundle(t, tl, h, hl)

    def test_date_missing_blocks(self):
        t, tl, h, hl = sample()
        h.loc[0, "日付"] = None
        with self.assertRaisesRegex(ValueError, "time isolation"):
            analyze_verified_bundle(t, tl, h, hl)

    def test_duplicate_history_blocks(self):
        t, tl, h, hl = sample()
        h = pd.concat([h, h.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicat"):
            analyze_verified_bundle(t, tl, h, hl)

    def test_extra_target_year_blocks(self):
        t, tl, h, hl = sample()
        tl = pd.concat([tl, pd.DataFrame([{
            "race_id": "202305041111", "status": "成功", "entrants": 9,
        }])], ignore_index=True)
        with self.assertRaises(ValueError):
            analyze_verified_bundle(t, tl, h, hl)

    def test_future_only_exposure_does_not_leak(self):
        t, tl, h, hl = sample()
        h = h.loc[h["馬ID"].eq("2020000002")].copy()
        # Failing to collect one horse's history must block the analysis,
        # even when another horse has none in the available rows.
        with self.assertRaises(ValueError):
            analyze_verified_bundle(t, tl, h, hl)


if __name__ == "__main__":
    unittest.main()
