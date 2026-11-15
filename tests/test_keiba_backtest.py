"""過去レースの検証データ収集：結果ページの全出走馬・原文保持・再開・新馬戦の扱い。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import keiba_backtest as bt


def result_page(name='清水S', data2='5回 京都 2日目 サラ系３歳以上 ３勝クラス (混)(特指) 定量 3頭', rows=None):
    rows = rows or [('1', '2022100001', '馬ア', '1:35.5', '', '1', '33.3', '2-2'),
                    ('2', '2022100002', '馬イ', '1:35.6', '3/4', '2', '33.3', '3-3'),
                    ('取消', '2022100003', '馬ウ', '', '', '', '', '')]
    head = ''.join(f'<th>{h}</th>' for h in ['着 順', '枠', '馬 番', '馬名', '性齢', '斤量', '騎手', 'タイム', '着差', '人 気',
                                              '単勝 オッズ', '後3F', 'コーナー 通過順', '厩舎', '馬体重 (増減)'])
    body = ''.join(
        f'<tr class="HorseList"><td>{r[0]}</td><td>1</td><td>{i}</td><td><a href="https://db.netkeiba.com/horse/{r[1]}">{r[2]}</a></td>'
        f'<td>牝4</td><td>56.0</td><td>騎手</td><td>{r[3]}</td><td>{r[4]}</td><td>{r[5]}</td><td>2.1</td><td>{r[6]}</td>'
        f'<td>{r[7]}</td><td>栗東</td><td>474 (-8)</td></tr>' for i, r in enumerate(rows, 1))
    return (f'<h1 class="RaceName">{name}<span class="Icon_GradeType Icon_GradeType16"></span></h1>'
            '<div class="RaceData01">15:05発走 / 芝1600m 右 外 B / 天候:晴 / 馬場:良</div>'
            f'<div class="RaceData02">{data2}</div>'
            f'<table id="All_Result_Table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


LIST = ('<li class="RaceList_DataItem"><a href="../race/result.html?race_id=202608040210">10R 清水S 15:05 芝1600m 3頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/result.html?race_id=202608040205">5R 2歳新馬 芝1800m 2頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/result.html?race_id=202608040209">9R 特別 ダ1800m 9頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/result.html?race_id=202608040208">8R 3歳以上障害未勝利 芝2750m 9頭</a></li>')


class ParseTests(unittest.TestCase):
    def test_result_page_keeps_raw_text_and_all_runners(self):
        info, runners = bt.parse_result_page(result_page(), '202608040210')
        self.assertEqual((info['コース'], info['距離'], info['馬場']), ('芝', 1600, '良'))
        self.assertIn('３勝クラス', info['条件欄2'])
        self.assertEqual(info['グレード表示'], 'Icon_GradeType16')
        self.assertEqual(len(runners), 3)
        self.assertEqual(runners.iloc[2]['着順原文'], '取消')
        self.assertEqual(runners.iloc[0]['通過'], '2-2')

    def test_list_keeps_turf_flat_only(self):
        ids = bt.race_ids_for(LIST)
        self.assertEqual(list(ids['レースID']), ['202608040210', '202608040205'])


class RunTests(unittest.TestCase):
    def setUp(self):
        from keiba_fetch_policy import FetchPolicy
        p = patch('keiba_fetch_policy.POLICY', FetchPolicy(sleeper=lambda _: None))
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if 'race_list_sub' in url:
            return LIST
        if url.endswith('202608040210'):
            return result_page()
        if url.endswith('202608040205'):
            return result_page('2歳新馬', '5回 京都 2日目 サラ系２歳 新馬', [('1', '2024100001', '新馬ア', '1:50.0', '', '1', '35.0', '1-1')])
        raise AssertionError(url)

    def history(self, hid, name):
        self.calls.append(hid)
        return pd.DataFrame([{'日付': '2026/09/01', '馬名': name, '馬ID': hid}])

    def pace(self, rid):
        self.calls.append('pace:' + rid)
        return 'ミドル寄り'

    def run_once(self, root):
        return bt.run(['20261011'], Path(root) / 'w', fetch_text=self.fetch, history_loader=self.history, pace_loader=self.pace)

    def test_collects_once_and_resumes_without_new_requests(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_once(root), '完了')
            self.assertNotIn('2024100001', self.calls)  # 新馬戦のみの馬は戦績を要求しない
            self.assertEqual(sum(c.startswith('pace:') for c in self.calls), 2)
            first = len(self.calls)
            self.assertEqual(self.run_once(root), '完了')
            self.assertEqual(len(self.calls), first)
            info = json.loads((Path(root) / 'w' / 'races' / '202608040210.json').read_text(encoding='utf-8'))
            self.assertEqual(info['開催日'], '2026-10-11')

    def test_gate_stop_is_saved_and_reported(self):
        from keiba_fetch_policy import BudgetExhausted
        def history(hid, name):
            raise BudgetExhausted(1)
        with tempfile.TemporaryDirectory() as root:
            status = bt.run(['20261011'], Path(root) / 'w', fetch_text=self.fetch, history_loader=history, pace_loader=self.pace)
            self.assertTrue(status.startswith('停止'))
            self.assertEqual(bt.read_status(Path(root) / 'w')['状態'], '停止')


if __name__ == '__main__':
    unittest.main()
