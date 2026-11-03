"""Portable archive roundtrip and tampering safeguards."""
import unittest
from io import BytesIO
import zipfile
import pandas as pd

from keiba_affinity_store import pack_checkpoint, load_checkpoint, audit_checkpoint


class ArchiveTests(unittest.TestCase):
    def test_roundtrip_preserves_race_and_horse_ids(self):
        rows = pd.DataFrame([{"レースID":"0202505041111", "馬ID":"0000012345",
                              "馬名":"試験馬", "日付":"2025-10-01"}])
        saved = pack_checkpoint({"targets":rows})
        restored = load_checkpoint(saved)["targets"]
        self.assertEqual(restored.loc[0,"馬ID"],"0000012345")
        self.assertEqual(restored.loc[0,"レースID"],"0202505041111")

    def test_multiple_tables(self):
        archive = pack_checkpoint({"target_log":pd.DataFrame([{"status":"成功"}]),
                                   "horse_log":pd.DataFrame([{"horse_id":"0000000001"}])})
        self.assertEqual(set(load_checkpoint(archive)), {"target_log","horse_log"})

    def test_audit_detects_missing_horse_results(self):
        target = pd.DataFrame([{"レースID": "202505041111", "馬ID": "2020000001",
                                "日付": "2025-11-02", "レース名": "テスト",
                                "着順_数値": 1, "人気": 2}])
        manifest = pd.DataFrame([{"race_id": "202505041111", "status": "成功", "entrants": 1}])
        horse_log = pd.DataFrame([{"horse_id": "2020000001", "status": "成功"}])
        valid = {"targets": target, "target_log": manifest,
                 "horse_log": horse_log, "horse_raw": pd.DataFrame([{"馬ID": "2020000001"}])}
        self.assertEqual(audit_checkpoint(valid), [])
        missing = dict(valid)
        missing["horse_raw"] = pd.DataFrame([{"馬ID": "9999999999"}])
        self.assertTrue(audit_checkpoint(missing))

    def test_audit_detects_wrong_target_count(self):
        target = pd.DataFrame([{"レースID": "202505041111", "馬ID": "2020000001",
                                "日付": "2025-11-02", "レース名": "テスト",
                                "着順_数値": 1, "人気": 2}])
        manifest = pd.DataFrame([{"race_id": "202505041111", "status": "成功", "entrants": 2}])
        self.assertTrue(audit_checkpoint({"targets": target, "target_log": manifest}))

    def test_partial_checkpoint_with_multiple_unfetched_years(self):
        acquired = pd.DataFrame([{
            "レースID": "202505041111", "馬ID": "2020000001",
            "日付": "2025-11-02", "レース名": "テスト",
            "着順_数値": 1, "人気": 2,
        }])
        years = pd.DataFrame([
            {"year": 2023, "race_id": "", "status": "失敗", "entrants": 0},
            {"year": 2024, "race_id": "", "status": "失敗", "entrants": 0},
            {"year": 2025, "race_id": "202505041111", "status": "成功", "entrants": 1},
        ])
        restored = load_checkpoint(pack_checkpoint({
            "targets": acquired, "target_log": years,
        }))
        self.assertEqual(audit_checkpoint(restored), [])
        self.assertEqual(len(restored["target_log"]), 3)

    def test_reject_saved_affinity_with_failed_acquisition(self):
        target = pd.DataFrame([{"レースID": "202505041111", "馬ID": "2020000001",
                                "日付": "2025-11-02", "レース名": "テスト",
                                "着順_数値": 1, "人気": 2}])
        manifest = pd.DataFrame([{"year": 2025, "race_id": "202505041111",
                                  "status": "失敗", "entrants": 0}])
        horse_log = pd.DataFrame([{"horse_id": "2020000001", "status": "成功"}])
        tables = {"targets": target, "target_log": manifest,
                  "horse_log": horse_log, "horse_raw": pd.DataFrame([{"馬ID": "2020000001"}]),
                  "affinity": pd.DataFrame([{"race_name": "誤った候補"}])}
        restored = load_checkpoint(pack_checkpoint(tables))
        self.assertTrue(audit_checkpoint(restored))

    def test_unknown_table_rejected(self):
        with self.assertRaises(ValueError):
            pack_checkpoint({"danger":pd.DataFrame([{"x":1}])})

    def test_tampered_manifest_rejected(self):
        out = BytesIO()
        with zipfile.ZipFile(out,"w") as z:
            z.writestr("manifest.json",'{"format_version":1,"tables":["targets"]}')
            z.writestr("unexpected.txt","hello")
        with self.assertRaises(ValueError):
            load_checkpoint(out.getvalue())

    def test_bad_archive_rejected(self):
        with self.assertRaises(zipfile.BadZipFile):
            load_checkpoint(b"not a zip")


if __name__=="__main__":
    unittest.main()
