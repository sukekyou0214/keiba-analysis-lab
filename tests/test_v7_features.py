import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v7_test as v7  # noqa: E402


class V7FeatureTests(unittest.TestCase):
    def test_first_corner(self):
        self.assertAlmostEqual(v7.first_corner('3-3-2', 11), 0.2)
        self.assertTrue(np.isnan(v7.first_corner('', 10)))

    def test_features(self):
        prior = pd.DataFrame({'場': ['東京', '中山', '東京'], '先行': [0.0, 0.5, 1.0], '3着内': [1.0, 0.0, 0.0]})
        f = v7.v7_features(prior, '東京', 1, 9)
        self.assertAlmostEqual(f['先行度'], 0.5)
        # 東京 2走で1回3着内、全体 1/3 へ縮小2：(1 + 2/3) / 4
        self.assertAlmostEqual(f['同場3着内率'], (1 + 2 / 3) / 4)
        self.assertAlmostEqual(f['内枠点'], 1.0)
        none = v7.v7_features(None, '東京', 9, 9)
        self.assertTrue(np.isnan(none['先行度']))
        self.assertAlmostEqual(none['内枠点'], 0.0)


if __name__ == '__main__':
    unittest.main()
