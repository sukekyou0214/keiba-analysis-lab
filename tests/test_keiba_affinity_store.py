"""Portable archive roundtrip and tampering safeguards."""
import unittest
from io import BytesIO
import zipfile
import pandas as pd

from keiba_affinity_store import pack_checkpoint, load_checkpoint


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
