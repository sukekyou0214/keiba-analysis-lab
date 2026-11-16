"""主画面の「参考：騎手順位（新馬戦のみ）」。"""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import keiba_debut_reference as dr
from keiba_weekend_batch import HistoryCache


class DebutReferenceTests(unittest.TestCase):
    def work(self, root, name):
        work = Path(root) / 'work'
        (work / 'entries').mkdir(parents=True)
        info = {'レースID': 'R1', 'レース名': name, '開催日': '2026-11-21', 'コース': '芝'}
        (work / 'entries' / 'R1.json').write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
        pd.DataFrame({'レースID': 'R1', '馬ID': ['a', 'b', 'c'], '馬名': ['ア', 'イ', 'ウ'], '馬番': [1, 2, 3],
                      '騎手': ['乙川', '▲甲山', '甲山'], '状態': ['出走予定', '出走予定', '取消']}
                     ).to_csv(work / 'entries' / 'R1.csv', index=False)
        # 別レースの出走馬の戦績にある騎乗も数える（同じ週末の全出走馬）
        pd.DataFrame({'レースID': 'R2', '馬ID': ['h1'], '馬名': ['エ'], '馬番': [1], '騎手': ['甲山'], '状態': ['出走予定']}
                     ).to_csv(work / 'entries' / 'R2.csv', index=False)
        cache = HistoryCache(work / 'horses')
        pd.DataFrame({'馬ID': ['h1', 'h1'], '日付': ['2026/10/01', '2026/10/08'], '騎手': ['甲山太郎', '乙川次郎'],
                      'レースID': ['x1', 'x2'], '着 順': ['1', '12'], '距離': ['芝1600', '芝1800']}
                     ).to_csv(cache.path('h1'), index=False)
        return work

    def test_debut_race_table_ranks_by_jockey_and_skips_scratched(self):
        with tempfile.TemporaryDirectory() as root:
            table = dr.reference_table(self.work(root, '2歳新馬'), 'R1')
            self.assertEqual(list(table['馬名']), ['イ', 'ア'])
            self.assertEqual(list(table['順位']), [1, 2])
            self.assertTrue(table['騎手点（3着内率）'].str.endswith('%').all())

    def test_not_shown_for_other_races(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(dr.reference_table(self.work(root, '3歳以上1勝クラス'), 'R1'))

    def test_validation_text_states_it_is_not_probability(self):
        self.assertIn('勝つ確率ではありません', dr.VALIDATION)
        self.assertIn('47.8%', dr.VALIDATION)
