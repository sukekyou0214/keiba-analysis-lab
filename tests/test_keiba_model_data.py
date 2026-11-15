"""戦績の正規化：状態・芝ダ障・クラス・窓・未来遮断。"""
import unittest

import pandas as pd

from keiba_model_data import before, classify_class, comparable, finish_status, first_corner, normalize_history, ran


def raw(rows):
    return pd.DataFrame([{'日付': d, '開催': v, '距離': dist, '着 順': f, '頭 数': n, '着差': m, '上り': '34.0', '通過': p,
                          'レース名': name, 'レース格': g, '馬名': 'A', '馬ID': '2020000001', 'レースID': rid, '馬 場': '良'}
                         for d, v, dist, f, n, m, p, name, g, rid in rows])


ROWS = [('2026/11/20', '5京都4', '芝2000', '1', '12', '-0.2', '2-2', '未来S(3勝クラス)', 'その他', '202608050410'),
        ('2026/10/01', '4東京1', '芝1600', '取', '16', '', '', '3歳以上2勝クラス', 'その他', '202605040101'),
        ('2026/09/01', '4中山1', 'ダ1800', '3', '16', '0.5', '5-5-4-3', '3歳以上2勝クラス', 'その他', '202606040101'),
        ('2026/08/01', '2新潟1', '芝1800', '中', '18', '', '', '関屋記念(GIII)', 'G3', '202604020101'),
        ('2026/07/01', '名古屋', 'ダ1400', '1', '10', '-0.5', '1-1', 'C1', 'その他', ''),
        ('2026/06/01', '3東京1', '芝1600', '2', '16', '0.1', '3-3', '府中S(L)', 'その他', '202605030101'),
        ('2026/05/01', '2京都1', '障3000', '1', '12', '-1.0', '', '3歳以上障害未勝利', 'その他', '202608020101'),
        ('2026/04/01', '2京都1', '芝2000', '1(降)', '12', '', '', '3歳以上1勝クラス', 'その他', '202608020102')]


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.h = normalize_history(raw(ROWS))

    def test_status_and_surface(self):
        status = dict(zip(self.h['レースID'], self.h['状態']))
        self.assertEqual(status['202605040101'], '取消')
        self.assertEqual(status['202604020101'], '中止')
        self.assertEqual(status['202608020102'], '降着')
        surfaces = dict(zip(self.h['レースID'], self.h['コース']))
        self.assertEqual((surfaces['202606040101'], surfaces['202608020101']), ('ダ', '障'))

    def test_class_from_explicit_labels_and_unknown_kept(self):
        cls = dict(zip(self.h['レース名'], self.h['クラス']))
        self.assertEqual(cls['関屋記念(GIII)'], 'G3')
        self.assertEqual(cls['府中S(L)'], 'L')
        self.assertEqual(cls['3歳以上2勝クラス'], '2勝')
        self.assertEqual(cls['C1'], '不明')  # 地方は不明のまま
        self.assertEqual(classify_class('2歳未勝利', '(混合)[指定] 馬齢'), '未勝利')
        self.assertEqual(classify_class('アンドロメダS', '3歳以上 オープン (国際)'), 'OP')
        self.assertIsNone(classify_class('特別', ''))

    def test_cutoff_and_windows(self):
        past = before(self.h, '2026-11-15')
        self.assertNotIn('202608050410', set(past['レースID']))  # 対象日以降は使わない
        turf_ran = ran(past.loc[past['コース'].eq('芝')])
        self.assertNotIn('取消', set(turf_ran['状態']))  # 取消・除外は窓に数えない
        self.assertIn('中止', set(turf_ran['状態']))  # 中止は出走として数える
        self.assertEqual(set(comparable(past)['状態']), {'完走', '降着'})  # 着順比較は完走・降着のみ

    def test_raw_text_kept_and_margins(self):
        row = self.h.loc[self.h['レースID'].eq('202605040101')].iloc[0]
        self.assertEqual(row['着順_原文'], '取')
        self.assertTrue(pd.isna(row['着差']))
        self.assertEqual(finish_status('１'), ('完走', 1))
        self.assertEqual(first_corner('5-5-4-3'), 5)
        self.assertIsNone(first_corner(''))

    def test_empty_and_duplicate_rows(self):
        self.assertTrue(normalize_history(pd.DataFrame()).empty)
        doubled = normalize_history(pd.concat([raw(ROWS), raw(ROWS)]))
        self.assertEqual(len(doubled), len(self.h))


if __name__ == '__main__':
    unittest.main()
