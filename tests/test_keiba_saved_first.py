"""保存データ優先：バックアップ復元後は追加要求なし、保存後に走った馬だけ新しい走を追加。"""
import unittest

import pandas as pd

from keiba_main_backup import load_main_backup, pack_main_backup
from keiba_main_conditions import main_context
from keiba_main_integration import (fetch_missing_histories, horses_without_history, merge_new_runs,
                                    parse_latest_runs, stale_horses)

RID = '202608050411'
HORSES = {'2022000001': '保存馬甲', '2022000002': '保存馬乙'}


def saved_history():
    rows = []
    for hid in HORSES:
        for i, (rid, day) in enumerate([('202608010101', '2026-10-04'), ('202605010101', '2026-05-03')]):
            rows.append({'馬ID': hid, '馬名': HORSES[hid], 'レースID': rid, '日付': day, '着順_数値': i + 1,
                         '頭数': 16, '上がり順位': 2, 'netkeibaペース': 'ミドル寄り'})
    return pd.DataFrame(rows)


def past_page(dates):
    rows = ''.join(
        f'<tr class="HorseList" id="tr_{i}"><td class="HorseInfo"><a href="https://db.netkeiba.com/horse/{hid}">{HORSES[hid]}</a></td>'
        + ''.join(f'<td class="Past"><div class="Data01"><span>{d} 京都</span></div></td>' for d in day_list)
        + '</tr>' for i, (hid, day_list) in enumerate(dates.items(), 1))
    predicted = ('<table class="PredictRap_Table"><tr class="HorseList"><td><a href="https://db.netkeiba.com/horse/2022000001">x</a></td>'
                 '<td class="Past">2027.01.01</td></tr></table>')
    return f'<table class="Shutuba_Table">{rows}</table>{predicted}'


class SavedFirstTests(unittest.TestCase):
    def restored(self):
        blob = pack_main_backup(RID, HORSES, {'2022000001': 1, '2022000002': 2}, saved_history(),
                                context=main_context(RID, {}))
        manifest, frames = load_main_backup(blob, RID)
        from keiba_core import prepare
        return manifest['horses'], prepare(frames['data'])

    def test_restored_backup_needs_no_history_request(self):
        horses, data = self.restored()
        missing = horses_without_history(horses, data)
        self.assertEqual(missing, {})
        calls = []
        fetch_missing_histories(missing, lambda *a: calls.append(a), sleeper=lambda _: None)
        self.assertEqual(calls, [])

    def test_no_new_run_means_no_refetch(self):
        horses, data = self.restored()
        latest = parse_latest_runs(past_page({'2022000001': ['2026.10.04', '2026.05.03'],
                                              '2022000002': ['2026.10.04']}))
        self.assertEqual(stale_horses(horses, data, latest), {})

    def test_only_horses_with_a_later_run_are_refetched(self):
        horses, data = self.restored()
        latest = parse_latest_runs(past_page({'2022000001': ['2026.11.01', '2026.10.04'],
                                              '2022000002': ['2026.10.04']}))
        # 予想ラップ表の複製行（未来日付）は読まない。
        self.assertEqual(latest['2022000001'], pd.Timestamp('2026-11-01'))
        self.assertEqual(stale_horses(horses, data, latest), {'2022000001': '保存馬甲'})

    def test_merge_keeps_enriched_rows_and_adds_only_new_runs(self):
        _, data = self.restored()
        fresh = pd.DataFrame([
            {'馬ID': '2022000001', '馬名': '保存馬甲', 'レースID': '202608010101', '日付': '2026-10-04', '着順_数値': 1},
            {'馬ID': '2022000001', '馬名': '保存馬甲', 'レースID': '202605040811', '日付': '2026-11-01', '着順_数値': 3}])
        merged = merge_new_runs(data, fresh)
        self.assertEqual(len(merged), len(data) + 1)
        kept = merged.loc[merged['レースID'].eq('202608010101') & merged['馬ID'].eq('2022000001')]
        self.assertEqual(kept['netkeibaペース'].tolist(), ['ミドル寄り'])


class RankingLabelTests(unittest.TestCase):
    def test_unfetched_horses_are_held_with_reason(self):
        from keiba_main_ranking import main_ranking
        horses = {**HORSES, '2022000003': '未取得馬'}
        data = saved_history()
        data['コース'] = '芝'
        context = main_context(RID, {})
        for graded in (True, False):
            r = main_ranking(data, horses, context, graded_only=graded, limited={'2022000003'})
            row = r.loc[r['馬ID'].eq('2022000003')].iloc[0]
            self.assertEqual(row['判定'], '戦績未取得（取得先の制限）')
            self.assertTrue(pd.isna(row['参考順位']))
        r = main_ranking(data, horses, context, graded_only=True)
        self.assertEqual(r.loc[r['馬ID'].eq('2022000003'), '判定'].iloc[0], '戦績未取得（取得待ち）')
        # 戦績はあるが重賞実績がない馬は従来どおり。
        self.assertTrue(r.loc[r['馬ID'].eq('2022000001'), '判定'].iloc[0] == '対象の重賞戦績なし・保留')


if __name__ == '__main__':
    unittest.main()
