import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nar_local_html as nh  # noqa: E402


class TierNoteTests(unittest.TestCase):
    def test_unknown_venue(self):
        self.assertIn('この競馬場では未確認', nh.tier_note('川崎', {}))

    def test_confirmed_and_unconfirmed(self):
        t = {'期間': '9/1〜11/24', '確認済み': True, '下限': 0.02, 'A': {'レース数': 10, '的中率': 0.5},
             '全レース': {'レース数': 40, '的中率': 0.3}}
        s = nh.tier_note('大井', {'大井': t})
        self.assertIn('過去の大井（9/1〜11/24）での5頭ボックス的中：A 50%（10R）、全レース 30%', s)
        self.assertIn('当たる確率ではありません', s)
        t['確認済み'] = False
        self.assertIn('区分は未確認', nh.tier_note('大井', {'大井': t}))


class BuildTests(unittest.TestCase):
    def test_build_with_v1s(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pd.DataFrame({'レースID': ['r1'] * 6, '発走': '12:00', 'レース': '1R 見本 12:00 ダ1400m 6頭', '順位': range(1, 7),
                          '馬番': range(1, 7), '馬名': list('ABCDEF'), '騎手': 'x', '地方点': [100, 80, 60, 40, 20, 0],
                          '方式': 'nar-local-v1', 'nar-v1S順位': [2, 1, 3, 4, 6, 5]}).to_csv(d / '地方参考順位_2026-11-26.csv', index=False)
            pd.DataFrame({'レースID': ['r1'], '発走': '12:00', 'レース': '1R 見本', '頭数': 6, 'ボックス向き順': 1,
                          '信頼度': 'A'}).to_csv(d / '信頼度区分_2026-11-26.csv', index=False)
            (d / 'run_log.json').write_text('{"AUC": {"nar-local-v1": 0.7}, "判定": "参考"}', encoding='utf-8')
            html = nh.build(d, '浦和', '2026-11-26', tiers={}).read_text(encoding='utf-8')
            self.assertIn('参考：南関東4場版（検証中）', html)
            self.assertIn('1・2・3・4・5', html)
            self.assertIn('この競馬場では未確認', html)


if __name__ == '__main__':
    unittest.main()
