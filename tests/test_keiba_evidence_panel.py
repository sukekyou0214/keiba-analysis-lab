"""詳細分析のデータの裏付け：分子/分母・日付・欠損を表示し、点数にしない。"""
import unittest

import pandas as pd

from keiba_evidence_panel import evidence_table


class EvidenceTests(unittest.TestCase):
    def test_fractions_dates_and_missing(self):
        data = pd.DataFrame([
            {'馬ID': '1', '日付': '2026-10-01', '競馬場': '京都', 'コース': '芝', '距離_m': 2200, '頭数': 12, '着順_数値': 1,
             '着差': -0.1, 'レース名': '3歳以上2勝クラス', 'レース格': 'その他', '通過順': '2-2', '馬場状態': '良'},
            {'馬ID': '1', '日付': '2026-09-01', '競馬場': '東京', 'コース': '芝', '距離_m': 1600, '頭数': 12, '着順_数値': None,
             '着差': None, 'レース名': '3歳以上2勝クラス', 'レース格': 'その他', '通過順': '', '馬場状態': '良'},
            {'馬ID': '1', '日付': '2026-12-01', '競馬場': '京都', 'コース': '芝', '距離_m': 2200, '頭数': 12, '着順_数値': 1,
             '着差': -0.1, 'レース名': '未来', 'レース格': 'その他', '通過順': '', '馬場状態': '良'}])
        t = evidence_table(data, {'1': 'A', '2': 'B'}, {'1': 3}, {'race_date': '2026-11-15', 'distance': 2200, 'venue': '京都'})
        a, b = t.iloc[0], t.iloc[1]
        self.assertEqual(a['馬'], '3 A')
        self.assertEqual(a['芝有効走数（直近5）'], 1)  # 着順が数値でない走（取消等）は数えない、未来の走も使わない
        self.assertEqual(a['距離±200m（3着内/走）'], '1/1')
        self.assertEqual(a['最終出走'], '2026/10/01')
        self.assertIn('芝の比較可能な走なし', b['欠損'])


if __name__ == '__main__':
    unittest.main()
