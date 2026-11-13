"""全馬共通条件：全馬を同じ窓・同じ式で順位付けし、保留は芝の有効成績がない馬だけ。"""
import unittest
from types import SimpleNamespace

import pandas as pd

from keiba_common_ranking import LEVELS, SHRINK, common_rank, run_level

CONFIG = SimpleNamespace(race_date='2026-11-15', venue='京都', distance=2200, surface='芝')


def runs(hid, rows):
    out = []
    for k, (finish, name, grade, venue, dist, pace) in enumerate(rows):
        out.append({'馬ID': hid, '馬名': hid, 'レースID': f'2026{int(hid[-2:]):02d}0101{k:02d}', '日付': f'2026-10-{28 - k:02d}',
                    'コース': '芝', '競馬場': venue, '距離_m': dist, '着順_数値': finish, '頭数': 16, 'レース名': name,
                    'レース格': grade, 'netkeibaペース': pace})
    return out


class CommonRankingTests(unittest.TestCase):
    def frame(self):
        data = (runs('2020000001', [(1, '秋華賞', 'G1', '京都', 2000, 'ミドル寄り')] * 6)
                + runs('2020000002', [(8, '府中牝馬S', 'G2', '東京', 1800, 'ハイ寄り')] * 6)
                + runs('2020000003', [(1, '3歳1勝クラス', 'その他', '中山', 1600, '')] * 2)
                + [{'馬ID': '2020000004', '馬名': 'ダートのみ', 'レースID': '202601010199', '日付': '2026-10-01', 'コース': 'ダ',
                    '競馬場': '京都', '距離_m': 1800, '着順_数値': 1, '頭数': 12, 'レース名': '1勝クラス', 'レース格': 'その他',
                    'netkeibaペース': ''}]
                + runs('2020000005', [(2, '未来の戦', 'G1', '京都', 2200, '')]))
        d = pd.DataFrame(data)
        d.loc[d['馬ID'].eq('2020000005'), '日付'] = '2026-11-20'  # 対象日以降は使わない
        d['日付'] = pd.to_datetime(d['日付'])
        return d

    def test_everyone_with_turf_runs_is_ranked_and_only_no_turf_is_held(self):
        entries = pd.DataFrame({'馬ID': [f'202000000{i}' for i in range(1, 7)], '馬名': list('ABCDEF')})
        r = common_rank(entries, self.frame(), CONFIG, 'M').set_index('馬ID')
        self.assertEqual(int(r['参考順位'].notna().sum()), 3)
        self.assertEqual(r.loc['2020000001', '参考順位'], 1)
        self.assertEqual(r.loc['2020000004', '判定'], '芝の有効成績なし・保留')
        self.assertEqual(r.loc['2020000005', '判定'], '芝の有効成績なし・保留')
        self.assertEqual(r.loc['2020000006', '判定'], '戦績未取得')
        self.assertEqual(r.loc['2020000003', '判定'], '共通条件・走数少（2走）')

    def test_small_samples_are_pulled_to_the_field_value(self):
        entries = pd.DataFrame({'馬ID': ['2020000001', '2020000002', '2020000003'], '馬名': list('ABC')})
        r = common_rank(entries, self.frame(), CONFIG, 'M').set_index('馬ID')
        # 2走連勝でも、6走連勝のG1馬より着順点・格とも低く出る（走数の少なさを割り引く）。
        self.assertLess(r.loc['2020000003', '着順点'], r.loc['2020000001', '着順点'])
        self.assertLess(r.loc['2020000003', '格'], r.loc['2020000001', '格'])
        field_finish = (6 * 100 + 6 * (1 - 7 / 15) * 100 + 2 * 100) / 14
        expected = (2 * 100 + SHRINK * field_finish) / (2 + SHRINK)
        self.assertAlmostEqual(r.loc['2020000003', '着順点'], round(expected, 1), places=1)

    def test_class_levels_read_only_from_explicit_labels(self):
        self.assertEqual(run_level('秋華賞', 'G1'), 'G1')
        self.assertEqual(run_level('三年坂S(3勝クラス)', 'その他'), '3勝')
        self.assertEqual(run_level('アンドロメダS(L)', 'その他'), 'OP/L')
        self.assertIsNone(run_level('C2一', 'その他'))
        self.assertEqual(LEVELS['G1'], 7)


class MainRankingModeTests(unittest.TestCase):
    def test_main_ranking_common_mode(self):
        from keiba_main_ranking import main_ranking
        d = CommonRankingTests().frame()
        context = {'race_id': '202608050411', 'race_date': '2026-11-15', 'venue': '京都', 'surface': '芝',
                   'distance': 2200, 'name': '試験', 'source': '試験', 'grade': 'G1'}
        horses = {'2020000001': 'A', '2020000002': 'B', '2020000003': 'C'}
        r = main_ranking(d, horses, context, mode='common')
        self.assertEqual(int(r['参考順位'].notna().sum()), 3)
        self.assertTrue(r['比較対象'].eq('全馬共通（芝の近10走）').all())
        graded = main_ranking(d, horses, context, graded_only=True)
        self.assertTrue(graded['比較対象'].eq('G1〜G3').all())


if __name__ == '__main__':
    unittest.main()
