"""予想記録と結果記録：別管理・上書き拒否・改ざん検出・事前保存の判定。"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import keiba_registry  # noqa: F401  (keiba_release may reload it during the suite)

NAMES = ('LocalStore', 'RecordExists', 'compare', 'frame_hash', 'load_prediction', 'save_prediction', 'save_result')


def refresh_names():
    import sys
    module = sys.modules['keiba_registry']
    globals().update({name: getattr(module, name) for name in NAMES})


refresh_names()


def ranking():
    return pd.DataFrame({'レースID': ['202608050411'] * 3, '馬ID': ['2020000001', '2020000002', '2020000003'],
                         '馬番': [1, 2, 3], '馬名': list('ABC'), '順位': [1, 2, pd.NA], '点数': [70.0, 65.0, pd.NA],
                         '判定': ['', '', '保留']})


class RegistryTests(unittest.TestCase):
    def setUp(self):
        refresh_names()

    def save(self, store, created='2026-11-15T10:00:00+09:00'):
        return save_prediction(store, '202608050411', ranking(), target_time='2026-11-15T15:40:00+09:00',
                               source='test', model_version='m1', feature_version='f1', data_version='d1',
                               input_frame=ranking(), created_at=created, code='abc')

    def test_prediction_is_written_once_and_verified(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            meta = self.save(store)
            self.assertEqual(meta['コード版'], 'abc')
            with self.assertRaises(RecordExists):
                self.save(store)
            meta2, table = load_prediction(store, 'm1', '202608050411')
            self.assertEqual(table['馬ID'].tolist(), ['2020000001', '2020000002', '2020000003'])
            self.assertTrue(pd.isna(table.loc[2, '順位']))  # 保留は最下位にしない
            path = Path(root) / 'predictions/m1/202608050411.csv'
            path.write_bytes(path.read_bytes().replace(b'70.0', b'99.0'))
            with self.assertRaises(ValueError):
                load_prediction(store, 'm1', '202608050411')

    def test_results_are_separate_and_compare_flags_pre_race(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            self.save(store)
            result = pd.DataFrame({'レースID': ['202608050411'] * 3, '馬ID': ['2020000002', '2020000001', '2020000003'],
                                   '馬番': [2, 1, 3], '馬名': list('BAC'), '着順原文': ['1', '2', '除'],
                                   '状態': ['完走', '完走', '除外'], '着順': [1, 2, pd.NA]})
            save_result(store, '202608050411', result, confirmed_at='2026-11-15T15:45:00+09:00', source='test')
            merged = compare(store, 'm1')
            self.assertTrue(merged['事前保存'].all())
            self.assertEqual(merged.set_index('馬ID').loc['2020000001', '着順'], 2)  # 馬IDで結合（馬番ではない）
            with self.assertRaises(RecordExists):
                save_result(store, '202608050411', result, confirmed_at='x', source='test')

    def test_duplicate_horse_rejected_and_hash_stable(self):
        with tempfile.TemporaryDirectory() as root:
            bad = pd.concat([ranking(), ranking().iloc[:1]])
            with self.assertRaises(ValueError):
                save_prediction(LocalStore(root), 'r', bad, target_time='t', source='s', model_version='m',
                                feature_version='f', data_version='d')
        self.assertEqual(frame_hash(ranking()), frame_hash(ranking()))


if __name__ == '__main__':
    unittest.main()
