"""展開予測：最初のコーナーを位置の目安にする・根拠不足は保留・しきい値選択の再現性。"""
import unittest

import numpy as np
import pandas as pd

import keiba_pace_v1 as pv
from keiba_model_data import normalize_history


def hist(hid, passings, field='12'):
    return normalize_history(pd.DataFrame([{'日付': f'2026/{10 - i:02d}/01', '開催': '4京都1', '距離': '芝2000', '着 順': '3',
                                            '頭 数': field, '通過': p, 'レース名': '3歳以上2勝クラス', 'レース格': 'その他',
                                            '馬ID': hid, 'レースID': f'2026{hid[-2:]}{i:06d}'} for i, p in enumerate(passings)]))


class PaceTests(unittest.TestCase):
    def test_first_corner_not_fourth(self):
        h = hist('2020000001', ['1-1-8-10', '2-2-9-12', '1-1-1-1'])
        pos = pv.horse_position(h, '2026-11-15', pv.PaceParams())
        self.assertAlmostEqual(pos['位置目安'], (1 / 12 + 2 / 12 + 1 / 12) / 3)
        self.assertEqual(pos['位置根拠走数'], 3)

    def test_pressure_label_and_hold(self):
        front = {f'20200000{i:02d}': hist(f'20200000{i:02d}', ['1-1-1', '2-2-2', '1-1-1']) for i in range(1, 5)}
        back = {f'20200000{i:02d}': hist(f'20200000{i:02d}', ['10-10', '11-11', '12-12']) for i in range(5, 9)}
        field = pd.DataFrame({'馬ID': list(front) + list(back)})
        res, positions = pv.predict_pace(field, {**front, **back}, '2026-11-15', pv.PaceParams())
        self.assertEqual(res['独自判定'], 'H')
        self.assertEqual(len(positions), 8)
        unknown = pd.DataFrame({'馬ID': [f'20209999{i:02d}' for i in range(8)]})
        res2, _ = pv.predict_pace(unknown, {}, '2026-11-15', pv.PaceParams())
        self.assertIsNone(res2['独自判定'])
        self.assertIn('判定保留', res2['根拠'])

    def test_threshold_choice_is_deterministic(self):
        pressures = np.array([0.0, 0.5, 2.0, 2.5, 4.0, 5.0])
        published = np.array(['S', 'S', 'M', 'M', 'H', 'H'])
        a = pv.choose_thresholds(pressures, published)
        self.assertEqual(a, pv.choose_thresholds(pressures, published))
        self.assertEqual(a[0], 1.0)


if __name__ == '__main__':
    unittest.main()
