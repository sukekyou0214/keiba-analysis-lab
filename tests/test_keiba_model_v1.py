"""基準モデル v1：未来遮断・再現性・同点・新馬/1走/休養/芝復帰/全欠損・保留の扱い・縮小。"""
import unittest

import numpy as np
import pandas as pd

import keiba_model_v1 as v1
from keiba_model_data import normalize_history

INFO = {'レースID': '202608050411', '開催日': '2026-11-15', '距離': 2000, '競馬場': '京都', 'クラス': '3勝', '馬場': '良'}


def hist(hid, rows):
    return normalize_history(pd.DataFrame([{'日付': d, '開催': v, '距離': dist, '着 順': f, '頭 数': '12', '着差': m, '上り': '34.0',
                                            '通過': '3-3', 'レース名': name, 'レース格': 'その他', '馬ID': hid, '馬名': hid,
                                            'レースID': f'2026{hid[-2:]}{i:06d}', '馬 場': '良'}
                                           for i, (d, v, dist, f, m, name) in enumerate(rows)]))


A = hist('2020000001', [('2026/10/01', '4京都1', '芝2000', '1', '-0.2', '3歳以上3勝クラス'),
                        ('2026/09/01', '4中山1', '芝1800', '2', '0.1', '3歳以上2勝クラス')])
B = hist('2020000002', [('2026/10/01', '4東京1', '芝1600', '8', '1.0', '3歳以上3勝クラス')])
ONE = hist('2020000003', [('2026/10/01', '4京都1', '芝2000', '1', '-0.1', '3歳以上2勝クラス')])
LONG = hist('2020000004', [('2025/06/01', '3京都1', '芝2000', '1', '-0.3', '3歳以上3勝クラス')])
DIRT = hist('2020000005', [('2026/10/01', '4東京1', 'ダ1600', '1', '-0.5', '3歳以上2勝クラス'),
                           ('2026/03/01', '1中山1', '芝2000', '5', '0.6', '3歳以上2勝クラス')])


class V1Tests(unittest.TestCase):
    def params(self, **kw):
        p = v1.Params(features=('着順', '着差', 'クラス差', '距離適性', '休養日数', '芝実績なし'), **kw)
        p.priors = {'top3': 0.2}
        return p

    def feats(self, history, params=None):
        field = pd.DataFrame({'馬ID': ['2020000001', '2020000002', '2020000003', '2020000004', '2020000005', '2020000009']})
        return v1.build_features(field, history, INFO, params or self.params())

    def test_future_rows_do_not_change_features(self):
        h = pd.concat([A, B, ONE, LONG, DIRT])
        future = hist('2020000001', [('2026/11/20', '5京都4', '芝2000', '18', '5.0', '3歳以上3勝クラス')])
        a = self.feats(h)
        b = self.feats(pd.concat([h, future]))
        pd.testing.assert_frame_equal(a, b)

    def test_edge_horses_do_not_break(self):
        f = self.feats(pd.concat([A, B, ONE, LONG, DIRT])).set_index('馬ID')
        self.assertEqual(f.loc['2020000003', '芝有効走数'], 1)
        self.assertGreater(f.loc['2020000004', '休養日数'], 365)
        self.assertEqual(f.loc['2020000005', '直近ダート数'], 1)  # 芝復帰：ダートは窓から外れるが記録は残る
        self.assertEqual(f.loc['2020000009', '芝実績なし'], 1.0)  # 全欠損
        self.assertEqual((f.loc['2020000003', '距離適性_分子'], f.loc['2020000003', '距離適性_分母']), (1, 1))
        # 1/1 と 10/10 を同じ確かさにしない：縮小で1/1は基準率へ寄る
        self.assertLess(f.loc['2020000003', '距離適性'], 1.0)

    def test_rank_is_reproducible_ties_by_id_and_holds_not_last(self):
        p = self.params()
        f = self.feats(pd.concat([A, B, ONE, LONG, DIRT]), p)
        v1.fit_priors(f, p)
        beta = np.zeros(len(p.features))  # 全頭同点
        r1 = v1.rank_race(f, beta, p)
        r2 = v1.rank_race(f.copy(), beta, p)
        pd.testing.assert_frame_equal(r1, r2)
        self.assertEqual(r1.sort_values('順位')['馬ID'].tolist(), sorted(r1['馬ID']))
        held = v1.rank_race(f, beta, p, debut_race=True)
        self.assertTrue(held['順位'].isna().all())
        self.assertTrue(held['判定'].str.startswith('判定不可').all())
        missing = r1.set_index('馬ID').loc['2020000009', '判定']
        self.assertTrue(missing.startswith('推定'))

    def test_logit_learns_obvious_signal(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=(300, 1))
        race = np.repeat(np.arange(30), 10)
        winner = np.zeros(300)
        for r in range(30):
            idx = np.flatnonzero(race == r)
            winner[idx[np.argmax(x[idx, 0])]] = 1
        beta = v1.fit_logit(x, race, winner, l2=0.1)
        self.assertGreater(beta[0], 1.0)


if __name__ == '__main__':
    unittest.main()
