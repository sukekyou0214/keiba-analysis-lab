"""結果前の予想保存（主予測・影の予測）と結果後の照合。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import keiba_weekend_batch as wb
import test_keiba_weekend_batch as wbt  # 実行対象を重複させないためクラスは名前空間に置かない


def result_html():
    from test_keiba_backtest import result_page
    return result_page('試験S', '3歳以上 ３勝クラス', [('2', '2022100001', '試験馬ア', '1:35.6', '1', '2', '34.0', '2-2'),
                                                    ('1', '2022100002', '試験馬イ', '1:35.5', '', '1', '33.5', '5-5'),
                                                    ('3', '2022100003', '試験馬ウ', '1:35.7', '1', '3', '34.1', '1-1')])


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.batch = wbt.WeekendBatchTests('test_end_to_end_outputs_and_rules')
        self.batch.setUp()
        self.addCleanup(self.batch.policy.stop)

    def artifact(self, root):
        import keiba_model_v1 as v1
        p = v1.Params(features=('着順', '芝実績なし'))
        p.priors = {'top3': 0.2, 'median': {'着順': 0.5, '芝実績なし': 0.0}, 'mean': {'着順': 0.5, '芝実績なし': 0.0},
                    'std': {'着順': 0.3, '芝実績なし': 1.0}}
        path = Path(root) / 'v1.json'
        v1.save_artifact(path, p, np.array([1.0, -0.5]))
        return path

    def test_records_saved_once_revised_on_scratch_and_compared(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('keiba_shadow.ARTIFACT', self.artifact(root)):
                self.batch.run_batch(root)
                records = Path(root) / 'work' / 'records' / 'predictions'
                models = sorted(p.name for p in records.iterdir())
                self.assertEqual(len(models), 3)
                self.assertTrue(any(m.startswith('shadow-v1-') for m in models))
                files = sorted(p.name for p in records.rglob('202608050510*.csv'))
                self.batch.run_batch(root)  # 同じ構成なら保存し直さない
                self.assertEqual(sorted(p.name for p in records.rglob('202608050510*.csv')), files)
                hist = Path(root) / 'work' / 'entries_history' / '202608050510'
                self.assertEqual(len([p for p in hist.glob('*.csv') if p.name != 'log.csv']), 1)
            fake_now = '2099-01-01'
            with patch.object(wb, 'fetch_text', lambda url, encoding=None: result_html()), \
                 patch('keiba_weekend_batch.datetime') as dt:
                dt.now.return_value.strftime.return_value = fake_now
                wb.collect_and_compare(Path(root) / 'work', Path(root) / 'out')
            per = list((Path(root) / 'out').glob('*_結果照合_レース別.csv'))
            self.assertEqual(len(per), 1)
            table = pd.read_csv(per[0], dtype={'レースID': str})
            self.assertIn('202608050510', set(table['レースID']))
            self.assertEqual(table['モデル版'].nunique(), 3)


if __name__ == '__main__':
    unittest.main()


class RevisionTests(unittest.TestCase):
    def test_scratch_creates_revision_and_compare_uses_last_pre_race(self):
        from keiba_registry import LocalStore, save_result
        import keiba_shadow as sh
        info = {'レースID': '202608050510', '開催日': '2026-11-14', '発走': '15:00', '取得元': 'x'}
        entries = pd.DataFrame({'馬ID': ['1000000001', '1000000002'], '馬名': ['A', 'B'], '馬番': [1, 2],
                                '状態': ['出走予定', '出走予定']})
        rank = pd.DataFrame({'馬ID': ['1000000001', '1000000002'], '順位': [1, 2], '点数': [2.0, 1.0], '判定': ['', '']})
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(Path(root))
            with patch('keiba_shadow._now', return_value='2026-11-14T10:00:00+09:00'):
                self.assertIsNotNone(sh._save(store, '202608050510', 'm', rank.assign(レースID='202608050510'), info, entries, 'd', 'f'))
                self.assertIsNone(sh._save(store, '202608050510', 'm', rank.assign(レースID='202608050510'), info, entries, 'd', 'f'))
            scratched = entries.assign(状態=['出走予定', '取消・除外'])
            meta = sh._save(store, '202608050510', 'm', rank.assign(レースID='202608050510', 順位=[2, 1]), info, scratched, 'd', 'f')
            self.assertIsNotNone(meta)
            self.assertTrue((Path(root) / 'predictions/m/202608050510_r1.csv').exists())
            result = pd.DataFrame({'レースID': ['202608050510'] * 2, '馬ID': ['1000000001', '1000000002'], '馬番': [1, 2],
                                   '馬名': ['A', 'B'], '着順原文': ['1', '取'], '状態': ['完走', '取消'], '着順': [1, None]})
            save_result(store, '202608050510', result, confirmed_at='2026-11-14T15:05:00+09:00', source='x')
            from keiba_registry import compare
            merged = compare(store, 'm')
            # 改訂（取消の反映）は今の時刻で作られ、対象日時より後なので事前保存ではない → 発走前の初版で照合する
            self.assertEqual(merged.set_index('馬ID').loc['1000000001', '順位'], 1)
            self.assertTrue(merged['事前保存'].all())


class DebutShadowTests(unittest.TestCase):
    def test_debut_race_saved_as_held_even_with_empty_history(self):
        import keiba_shadow as sh
        with tempfile.TemporaryDirectory() as root:
            artifact = ShadowTests.artifact(None, root)
            info = {'レースID': '202608050305', '開催日': '2026-11-15', '発走': '12:00', 'レース名': '2歳新馬',
                    '取得元': '試験'}
            entries = pd.DataFrame({'馬ID': ['2024100001', '2024100002'], '馬名': ['ア', 'イ'], '馬番': [1, 2],
                                    '状態': ['出走予定', '出走予定']})
            table = pd.DataFrame({'参考順位': [pd.NA, pd.NA], '総合参考点': [np.nan, np.nan]},
                                 index=pd.Index(entries['馬ID'], name='馬ID'))
            with patch('keiba_shadow.ARTIFACT', artifact):
                saved = sh.save_weekend_records(Path(root) / 'work', info, entries, {'M': table}, {'M': table},
                                                pd.DataFrame())
            self.assertIn('shadow-v1', saved)
            files = list((Path(root) / 'work' / 'records' / 'predictions').glob('shadow-*/202608050305.csv'))
            self.assertEqual(len(files), 1)
            pred = pd.read_csv(files[0])
            self.assertTrue(pred['順位'].isna().all())
            self.assertTrue(pred['判定'].str.contains('判定不可').all())
