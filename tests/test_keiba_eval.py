"""評価指標：繰り上げなし・全頭保留は失敗・中止は出走扱い・同着・ブートストラップ。"""
import unittest

import pandas as pd

from keiba_eval import bootstrap_diff, period_of, race_metrics, summarize


def field(rows):
    return pd.DataFrame([{'馬ID': h, '状態': s, '着順': f, '実出走': s in ('完走', '中止', '失格', '降着')} for h, s, f in rows])


F = field([('a', '完走', 1), ('b', '完走', 2), ('c', '取消', None), ('d', '中止', None), ('e', '完走', 3)])


class EvalTests(unittest.TestCase):
    def test_scratched_in_top3_is_not_replaced(self):
        pred = pd.DataFrame({'馬ID': ['c', 'b', 'd', 'a', 'e'], '順位': [1, 2, 3, 4, 5]})
        m = race_metrics(pred, F)
        self.assertFalse(m['上位3勝ち馬'])
        self.assertTrue(m['上位5勝ち馬'])
        self.assertEqual(m['頭数'], 4)  # 取消は実出走の分母から外す
        self.assertEqual(m['首位数'], 0)  # 首位が取消なら首位成績の分母に入れない

    def test_all_held_counts_as_miss_and_dead_heat(self):
        held = pd.DataFrame({'馬ID': list('abcde'), '順位': [pd.NA] * 5})
        m = race_metrics(held, F)
        self.assertFalse(m['上位3勝ち馬'])
        self.assertEqual(m['順位付与数'], 0)
        heat = field([('a', '完走', 1), ('b', '完走', 1), ('c', '完走', 3)])
        m2 = race_metrics(pd.DataFrame({'馬ID': ['b', 'c', 'a'], '順位': [1, 2, 3]}), heat)
        self.assertEqual(m2['首位勝利'], 1)
        rows = pd.DataFrame([{'レースID': 'r1', **m}, {'レースID': 'r2', **m2}])
        s = summarize(rows)
        self.assertEqual((s['レース数'], s['上位3勝ち馬']), (2, 1))

    def test_bootstrap_and_periods(self):
        a = pd.DataFrame({'レースID': ['1', '2', '3', '4'], '上位3勝ち馬': [1, 1, 0, 1]})
        b = pd.DataFrame({'レースID': ['1', '2', '3', '4'], '上位3勝ち馬': [0, 1, 0, 0]})
        r = bootstrap_diff(a, b)
        self.assertEqual((r['レース数'], r['差']), (4, 0.5))
        self.assertLessEqual(r['下限'], r['差'])
        self.assertEqual(bootstrap_diff(a, b), r)  # 種を固定して再現可能
        self.assertEqual((period_of('2026-09-20'), period_of('2026-10-25'), period_of('2026-11-14')), ('inner', 'outer', 'used'))


if __name__ == '__main__':
    unittest.main()
