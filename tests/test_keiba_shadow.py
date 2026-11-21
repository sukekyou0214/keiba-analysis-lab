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
            with patch('keiba_shadow.ARTIFACT', self.artifact(root)), \
                 patch('keiba_shadow.ARTIFACT_V2', Path(root) / 'none.json'), \
                 patch('keiba_shadow.ARTIFACT_V3', Path(root) / 'none.json'), patch('keiba_shadow.ARTIFACT_V5', Path(root) / 'none.json'):
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
            with patch('keiba_shadow.ARTIFACT', artifact), patch('keiba_shadow.ARTIFACT_V2', Path(root) / 'none.json'), \
                 patch('keiba_shadow.ARTIFACT_V3', Path(root) / 'none.json'), patch('keiba_shadow.ARTIFACT_V5', Path(root) / 'none.json'):
                saved = sh.save_weekend_records(Path(root) / 'work', info, entries, {'M': table}, {'M': table},
                                                pd.DataFrame())
            self.assertIn('shadow-v1', saved)
            files = list((Path(root) / 'work' / 'records' / 'predictions').glob('shadow-*/202608050305.csv'))
            self.assertEqual(len(files), 1)
            pred = pd.read_csv(files[0])
            self.assertTrue(pred['順位'].isna().all())
            self.assertTrue(pred['判定'].str.contains('判定不可').all())


class RestartRecoveryTests(unittest.TestCase):
    def test_upcoming_weekend_keeps_this_weekend_on_sunday(self):
        from datetime import date
        from keiba_weekend_ui import upcoming_weekend
        self.assertEqual(upcoming_weekend(date(2026, 11, 20)), (date(2026, 11, 21), date(2026, 11, 22)))
        self.assertEqual(upcoming_weekend(date(2026, 11, 21)), (date(2026, 11, 21), date(2026, 11, 22)))
        self.assertEqual(upcoming_weekend(date(2026, 11, 22)), (date(2026, 11, 21), date(2026, 11, 22)))
        self.assertEqual(upcoming_weekend(date(2026, 11, 23)), (date(2026, 11, 28), date(2026, 11, 29)))

    def repo(self, root):
        from keiba_registry import LocalStore, save_prediction
        store = LocalStore(Path(root) / 'repo')
        ranking = pd.DataFrame({'レースID': '202608050510', '馬ID': ['2022100001', '2022100002', '2022100003'],
                                '馬番': [1, 2, 3], '馬名': ['ア', 'イ', 'ウ'], '順位': [1, 2, 3], '点数': [3.0, 2.0, 1.0],
                                '判定': '試験'})
        save_prediction(store, '202608050510', ranking, target_time='2026-11-14T15:40:00+09:00', source='試験',
                        model_version='common-M@test', feature_version='frozen', data_version='data-v1',
                        created_at='2026-11-13T21:00:00+09:00', code='test')
        save_prediction(store, '202608050611', ranking.assign(レースID='202608050611'),
                        target_time='2026-11-21T15:40:00+09:00', source='試験', model_version='common-M@test',
                        feature_version='frozen', data_version='data-v1', created_at='2026-11-20T21:00:00+09:00',
                        code='test')
        return Path(root) / 'repo' / 'predictions'

    def test_restore_selects_dates_and_does_not_overwrite(self):
        import keiba_shadow as sh
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            self.assertEqual(len(sh.repo_records_for(['20261114'], repo)), 1)
            self.assertEqual(sh.restore_from_repo(Path(root) / 'work', ['20261114'], repo), ['202608050510'])
            self.assertEqual(sh.restore_from_repo(Path(root) / 'work', ['20261114'], repo), ['202608050510'])
            saved = list((Path(root) / 'work' / 'records' / 'predictions').rglob('*.csv'))
            self.assertEqual([p.name for p in saved], ['202608050510.csv'])

    def test_compare_after_restart_uses_repo_records(self):
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            work = Path(root) / 'weekend' / '20261114' / 'work'
            out = Path(root) / 'weekend' / '20261114' / 'out'
            with patch('keiba_shadow.REPO_RECORDS', repo), \
                 patch.object(wb, 'fetch_text', lambda url, encoding=None: result_html()):
                wb.collect_and_compare(work, out)
            per = pd.read_csv(out / '20261114_結果照合_レース別.csv', dtype={'レースID': str})
            self.assertEqual(list(per['レースID']), ['202608050510'])
            self.assertTrue(bool(per['事前保存'].iloc[0]))
            self.assertEqual(int(per['上位3勝ち馬'].iloc[0]), 1)
            self.assertIn('完了', json.loads((work / 'status.json').read_text(encoding='utf-8'))['照合'])

    def test_archive_comparisons(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('archive_records', Path(__file__).resolve().parents[1] / 'scripts' / 'archive_records.py')
        ar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ar)
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'out'
            src.mkdir()
            (src / 'x_結果照合_レース別.csv').write_text('a\n1\n', encoding='utf-8')
            (src / 'x_全頭展開別順位.csv').write_text('raw\n', encoding='utf-8')
            with patch.object(ar, 'ROOT', Path(root) / 'records'):
                self.assertEqual(ar.comparisons_from_weekend_zip(src, 'w1'), 1)
                self.assertEqual(ar.comparisons_from_weekend_zip(src, 'w1'), 0)
            self.assertEqual([p.name for p in (Path(root) / 'records' / 'comparisons' / 'w1').iterdir()],
                             ['x_結果照合_レース別.csv'])


class V2ShadowTests(unittest.TestCase):
    def test_rank_average_fills_missing_common_and_breaks_ties(self):
        import keiba_model_v1 as v1
        r = pd.DataFrame({'馬ID': ['a', 'b', 'c', 'd'], '順位': [1, 2, 3, 4], '点数': [4.0, 3.0, 2.0, 1.0]})
        out = v1.rank_average(r, np.array([3, 1, np.nan, 2]))
        # 平均: a=2, b=1.5, c=3（共通なし→v1で補う）, d=3 → b,a,c,d（c と d は点数で c が先）
        self.assertEqual(list(out.sort_values('順位')['馬ID']), ['b', 'a', 'c', 'd'])

    def test_v2_artifact_saves_rank_average_shadow(self):
        import keiba_model_v1 as v1
        with tempfile.TemporaryDirectory() as root:
            p = v1.Params(features=('着順', '芝実績なし'))
            p.priors = {'top3': 0.2, 'median': {'着順': 0.5, '芝実績なし': 0.0}, 'mean': {'着順': 0.5, '芝実績なし': 0.0},
                        'std': {'着順': 0.3, '芝実績なし': 1.0}}
            v2 = Path(root) / 'v2.json'
            v1.save_artifact(v2, p, np.array([1.0, -0.5]), {'model_version': 'v2', '種類': 'rank_average'})
            batch = wbt.WeekendBatchTests('test_end_to_end_outputs_and_rules')
            batch.setUp()
            self.addCleanup(batch.policy.stop)
            with patch('keiba_shadow.ARTIFACT', ShadowTests.artifact(None, root)), patch('keiba_shadow.ARTIFACT_V2', v2), \
                 patch('keiba_shadow.ARTIFACT_V3', Path(root) / 'none.json'), patch('keiba_shadow.ARTIFACT_V5', Path(root) / 'none.json'), \
                 patch('keiba_shadow.common_without_pace', side_effect=lambda info, active, raw: {}) as common:
                batch.run_batch(root)
            records = Path(root) / 'work' / 'records' / 'predictions'
            self.assertTrue(any(p.name.startswith('shadow-v2-') for p in records.iterdir()))
            self.assertTrue(common.called)


class V3DebutTests(unittest.TestCase):
    def test_jockey_points_match_and_shrink(self):
        import keiba_jockey as kj
        self.assertEqual(kj.match('▲和田陽', ['和田陽希', '和田竜二']), '和田陽希')
        self.assertEqual(kj.match('Ｃデムーロ', ['C.デムーロ', 'M.デムーロ']), 'C.デムーロ')
        self.assertIsNone(kj.match('原', ['原優介', '菅原明良']))
        raw = pd.DataFrame({'馬ID': ['h1'] * 4 + ['h2'] * 4, '日付': ['2026/10/01', '2026/10/08', '2026/10/15', '2025/01/01'] * 2,
                            '騎手': ['甲山太郎'] * 4 + ['乙川次郎'] * 4, 'レースID': [f'r{i}' for i in range(8)],
                            '着 順': ['1', '2', '3', '1', '9', '8', '取', '1'], '距離': ['芝1600', 'ダ1200', '芝2000', '芝1600'] * 2})
        rides = kj.rides(raw)
        self.assertEqual(len(rides), 7)  # 取消は数えない
        points, overall = kj.jockey_points(rides, '2026-11-01')  # 365日より前の騎乗は数えない
        self.assertAlmostEqual(overall, 3 / 5)
        self.assertGreater(points['甲山太郎'], overall)
        self.assertLess(points['乙川次郎'], overall)
        field = pd.DataFrame({'馬ID': ['x', 'y', 'z'], '騎手': ['乙川', '甲山', '不明']})
        r = kj.debut_ranking(field, rides, '2026-11-01', 'R1')
        self.assertEqual(list(r.sort_values('順位')['馬ID']), ['y', 'z', 'x'])
        self.assertIn('対応できず', r.set_index('馬ID').loc['z', '判定'])

    def test_v3_ranks_debut_race_with_jockey_points(self):
        import keiba_model_v1 as v1
        import keiba_shadow as sh
        with tempfile.TemporaryDirectory() as root:
            p = v1.Params(features=('着順',))
            p.priors = {'top3': 0.2, 'median': {'着順': 0.5}, 'mean': {'着順': 0.5}, 'std': {'着順': 0.3}}
            v3 = Path(root) / 'v3.json'
            v1.save_artifact(v3, p, np.array([1.0]), {'model_version': 'v3', '種類': 'rank_average', '新馬戦': 'jockey-v1'})
            info = {'レースID': '202608050305', '開催日': '2026-11-15', '発走': '12:00', 'レース名': '2歳新馬', '取得元': '試験'}
            entries = pd.DataFrame({'馬ID': ['2024100001', '2024100002'], '馬名': ['ア', 'イ'], '馬番': [1, 2],
                                    '騎手': ['乙川', '甲山'], '状態': ['出走予定', '出走予定']})
            pool = pd.DataFrame({'馬ID': ['h1', 'h2'], '日付': ['2026/10/01', '2026/10/01'], '騎手': ['甲山太郎', '乙川次郎'],
                                 'レースID': ['a', 'b'], '着 順': ['1', '10'], '距離': ['芝1600', '芝1600']})
            table = pd.DataFrame({'参考順位': [pd.NA, pd.NA], '総合参考点': [np.nan, np.nan]},
                                 index=pd.Index(entries['馬ID'], name='馬ID'))
            with patch('keiba_shadow.ARTIFACT', Path(root) / 'none.json'), \
                 patch('keiba_shadow.ARTIFACT_V2', Path(root) / 'none.json'), patch('keiba_shadow.ARTIFACT_V3', v3), \
                 patch('keiba_shadow.ARTIFACT_V5', Path(root) / 'none.json'):
                saved = sh.save_weekend_records(Path(root) / 'work', info, entries, {'M': table}, {'M': table},
                                                pd.DataFrame(), pd.DataFrame(), pool)
            self.assertIn('shadow-v3', saved)
            f = list((Path(root) / 'work' / 'records' / 'predictions').glob('shadow-v3-*/202608050305.csv'))[0]
            pred = pd.read_csv(f, dtype={'馬ID': str}).set_index('馬ID')
            self.assertEqual(int(pred.loc['2024100002', '順位']), 1)


class ArchiveConflictTests(unittest.TestCase):
    def test_conflicting_record_is_kept_alongside(self):
        import importlib.util, zipfile
        spec = importlib.util.spec_from_file_location('archive_records', Path(__file__).resolve().parents[1] / 'scripts' / 'archive_records.py')
        ar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ar)
        with tempfile.TemporaryDirectory() as root:
            def make(name, body):
                path = Path(root) / name
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr('records/predictions/m1/R1.json', body)
                    z.writestr('records/predictions/m1/R1.csv', body + 'csv')
                return path
            with patch.object(ar, 'ROOT', Path(root) / 'records'):
                self.assertEqual(ar.from_weekend_zip(make('a.zip', '{"a":1}')), 2)
                self.assertEqual(ar.from_weekend_zip(make('b.zip', '{"a":1}')), 0)  # 同じ中身は何もしない
                self.assertEqual(ar.from_weekend_zip(make('c.zip', '{"a":2}')), 2)  # 違う中身は別名で並べる
            files = sorted(p.name for p in (Path(root) / 'records' / 'predictions' / 'm1').iterdir())
            self.assertEqual(len(files), 4)
            self.assertEqual(Path(root, 'records', 'predictions', 'm1', 'R1.json').read_text(), '{"a":1}')
