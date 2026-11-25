import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v8_test as v8  # noqa: E402


class V8FeatureTest(unittest.TestCase):
    def test_weight_change(self):
        self.assertEqual(v8.weight_change('470 (+10)'), 1.0)
        self.assertEqual(v8.weight_change('518(-4)'), 0.4)
        self.assertEqual(v8.weight_change('計不'), 0.0)
        self.assertEqual(v8.weight_change(None), 0.0)

    def test_a_features(self):
        prior = pd.DataFrame({'日付': pd.to_datetime(['2026-01-01', '2026-03-01']), '騎手': ['甲', '乙'],
                              '距離': [1600.0, 2000.0], '場': ['中山', '東京']})
        a = v8.a_features(prior, pd.Timestamp('2026-05-20'), '東京', 2400.0, '丙')
        self.assertEqual(a, {'乗り替わり': 1.0, '休み明け': 1.0, '距離替わり': 1.0, 'コース替わり': 0.0})
        b = v8.a_features(prior, pd.Timestamp('2026-03-15'), '京都', 2000.0, '乙')
        self.assertEqual(b, {'乗り替わり': 0.0, '休み明け': 0.0, '距離替わり': 0.0, 'コース替わり': 1.0})
        self.assertEqual(v8.a_features(None, pd.Timestamp('2026-03-15'), '京都', 2000.0, '乙'),
                         {c: 0.0 for c in v8.A_SET})

    def test_day_bias(self):
        f = pd.DataFrame({'実出走': [True] * 5, '馬番': ['1', '2', '3', '4', '5'], '着順': [1, 2, 3, 4, 5],
                          '通過': ['1-1', '2-2', '3-3', '4-4', '5-5']})
        inner, lead = v8.day_bias([f])
        self.assertAlmostEqual(inner, (1 + 0.75 + 0.5) / 3 - 0.5)
        self.assertAlmostEqual(lead, 0.5 - 0.25)
        self.assertEqual(v8.day_bias([]), (0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
