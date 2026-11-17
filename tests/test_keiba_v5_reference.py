"""主画面の「参考：v5順位（検証中）」。"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import keiba_v5_reference as ref


class V5ReferenceTests(unittest.TestCase):
    def save(self, root, folder, name, ranks):
        path = Path(root) / 'records' / 'predictions' / folder / f'{name}.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({'レースID': 'R1', '馬ID': ['a', 'b', 'c'], '馬番': [1, 2, 3], '馬名': ['ア', 'イ', 'ウ'],
                      '順位': ranks, '点数': [1.0, 2.0, 3.0], '判定': '基準モデル'}).to_csv(path, index=False)

    def test_reads_latest_revision_in_rank_order(self):
        with tempfile.TemporaryDirectory() as root:
            self.save(root, 'shadow-v5-x', 'R1', [3, 2, 1])
            self.save(root, 'shadow-v5-x', 'R1_r1', [1, 3, 2])
            self.save(root, 'shadow-v3-y', 'R1', [2, 1, 3])
            table = ref.reference_table(root, 'R1')
            self.assertEqual(list(table['馬名']), ['ア', 'ウ', 'イ'])

    def test_none_without_record_or_ranks(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(ref.reference_table(root, 'R1'))
            self.save(root, 'shadow-v5-x', 'R1', [pd.NA, pd.NA, pd.NA])
            self.assertIsNone(ref.reference_table(root, 'R1'))

    def test_validation_text(self):
        self.assertIn('勝つ確率ではありません', ref.VALIDATION)
        self.assertIn('56.2%', ref.VALIDATION)
