"""週末一括分析：通信を差し替えて、取得→参考順位→信頼度→出力までを通しで確認する。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import keiba_weekend_batch as wb

DATE = '20261114'
LIST = ('<ul>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050510">10R 試験S 15:00 芝1600m 4頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba_debut.html?race_id=202608050505&rf=race_list">5R 2歳新馬 芝1800m 2頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050511">11R 読めないS 15:40 芝2000m 9頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba_debut.html?race_id=202608050506">6R 2歳新馬 芝1600m 12頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050509">9R 試験特別 ダ1800m 9頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050508">8R 障害S 障3000m 9頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050504">4R 3歳以上障害未勝利 芝2750m 9頭</a></li>'
        '<li class="RaceList_DataItem"><a href="../race/shutuba.html?race_id=202608050502">2R 2歳未勝利 芝1600m 16頭</a></li></ul>')


def entry_page(name, data2, horses, cancel=()):
    rows = ''.join(
        f'<tr class="HorseList{" Cancel" if hid in cancel else ""}" id="tr_{i}"><td class="Umaban{i} Txt_C">{i}</td>'
        f'<td class="HorseInfo"><span class="HorseName"><a href="https://db.netkeiba.com/horse/{hid}" title="{hname}">{hname}</a></span></td>'
        f'<td class="Jockey"><a>騎手{i}</a></td></tr>' for i, (hid, hname) in enumerate(horses, 1))
    return (f'<h1 class="RaceName">{name}</h1><div class="RaceData01">15:00発走 / 芝1600m 右 外</div>'
            f'<div class="RaceData02"><span>5回</span><span>京都</span><span>{data2}</span></div>'
            ''
            f'<table class="Shutuba_Table">{rows}</table>')


MAIN = [(f'202210000{i}', f'試験馬{"アイウエ"[i - 1]}') for i in range(1, 5)]
DEBUT = [('2024100001', '新馬ア'), ('2024100002', '新馬イ')]


def raw_history(hid, name, finishes):
    rows = []
    for k, finish in enumerate(finishes):
        rows.append({'日付': f'2026/{10 - k:02d}/01', '開催': '5京都1', '距離': '芝1600', '馬 場': '良', '着 順': str(finish),
                     '上り': '34.0', '通過': '3-3', '頭 数': '12', 'レース名': '試験戦', '馬名': name, '馬ID': hid,
                     'レースID': f'20260805{k:02d}{int(hid[-1]):02d}', 'レース格': 'その他', '格付け根拠': ''})
    rows.append({'日付': '2026/11/20', '開催': '5京都1', '距離': '芝1600', '馬 場': '良', '着 順': '1', '上り': '34.0',
                 '通過': '1-1', '頭 数': '12', 'レース名': '未来の戦', '馬名': name, '馬ID': hid, 'レースID': '202699999999', 'レース格': 'その他', '格付け根拠': ''})
    return pd.DataFrame(rows)


class WeekendBatchTests(unittest.TestCase):
    def setUp(self):
        from keiba_fetch_policy import FetchPolicy
        self.policy = patch('keiba_fetch_policy.POLICY', FetchPolicy(sleeper=lambda _: None))
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.requests = []

    def fake_text(self, url, encoding=None):
        self.requests.append(url)
        if 'race_list_sub' in url:
            return LIST
        if url.endswith('202608050510'):
            return entry_page('試験S', '３勝クラス', MAIN, cancel={'2022100004'})
        if 'shutuba_debut.html' in url and url.endswith('202608050505'):
            # 新馬戦のページ構造が違っても、一覧の表記で条件を補う（見出し・コース欄なし）。
            return entry_page('2歳新馬', '2歳 新馬', DEBUT).replace('<h1 class="RaceName">2歳新馬</h1>', '').replace(
                '<div class="RaceData01">15:00発走 / 芝1600m 右 外</div>', '').replace('<div class="RaceData02"><span>5回</span><span>京都</span><span>2歳 新馬</span></div>', '')
        if url.endswith('202608050511'):
            return '<html><body>メンテナンス中</body></html>'
        if url.endswith('202608050502'):
            # 確定前は一部の馬だけ公開される（一覧16頭に対して3頭）。
            return entry_page('2歳未勝利', '2歳 未勝利', MAIN[:3])
        if url.endswith('202608050506'):
            return '<h1 class="RaceName">2歳新馬</h1><p>新馬戦想定はスーパープレミアムコース登録でご覧になれます。</p>'
        raise AssertionError(url)

    def fake_results(self, hid, name):
        self.requests.append(hid)
        if hid == '2022100002' and getattr(self, 'odd_table', False):
            return pd.DataFrame([{'日付': '2026/10/01', 'レース名': '海外', '馬名': name, '馬ID': hid}])  # 列が足りない表
        if hid == '2022100003':
            return raw_history(hid, name, [2])  # 有効1走のみ → 保留
        return raw_history(hid, name, [1, 2, 3, 4, 5] if hid.endswith('1') else [5, 4, 6, 3, 8])

    def fake_pace(self, rid):
        self.requests.append(rid)
        if rid.endswith('0201'):
            raise ValueError('ラップタイム欄が見つかりません')
        return 'ハイ寄り' if rid.endswith('01') else 'ミドル寄り'

    def run_batch(self, root):
        with patch.object(wb, 'fetch_text', self.fake_text), \
             patch('keiba_core.get_results', self.fake_results), \
             patch('keiba_core.get_netkeiba_pace', self.fake_pace):
            return wb.run([DATE], Path(root) / 'work', Path(root) / 'out')

    def test_end_to_end_outputs_and_rules(self):
        with tempfile.TemporaryDirectory() as root:
            status = self.run_batch(root)
            self.assertEqual(status, '完了')
            out = Path(root) / 'out'
            races = pd.read_csv(out / f'{DATE}_全芝レース一覧.csv', dtype={'レースID': str})
            # ダート・障害は出走表を開かずに除外。
            self.assertEqual(sorted(races['レースID']), ['202608050502', '202608050505', '202608050506', '202608050510', '202608050511'])
            self.assertIn('出走表は未発表', races.set_index('レースID').loc['202608050506', '注意点'])
            broken = races.set_index('レースID').loc['202608050511']
            self.assertIn('出走表を読めず保留', broken['注意点'])
            debut_row = races.set_index('レースID').loc['202608050505']
            self.assertEqual((debut_row['コース'], debut_row['競馬場']), ('芝1800m', '京都'))
            self.assertFalse(any(rid in u for u in self.requests for rid in ('202608050509', '202608050508', '202608050504')))
            self.assertIn('一部のみ公開', races.set_index('レースID').loc['202608050502', '注意点'])
            # 新馬戦の出走馬は戦績を要求しない。
            self.assertFalse(any(u in ('2024100001', '2024100002') for u in self.requests))
            # 取消馬の戦績も要求しない。
            self.assertNotIn('2022100004', self.requests)
            horses = pd.read_csv(out / f'{DATE}_全頭展開別順位.csv', dtype={'レースID': str})
            main = horses.loc[horses['レースID'].eq('202608050510')].set_index('馬名')
            self.assertEqual(main.loc['試験馬ア', 'M順位'], 1)
            self.assertTrue(pd.isna(main.loc['試験馬ウ', 'M順位']))
            self.assertIn('根拠不足', main.loc['試験馬ウ', 'M判定'])
            self.assertEqual(main.loc['試験馬エ', '状態'], '取消・除外')
            row = races.set_index('レースID').loc['202608050510']
            self.assertEqual(row['比較対象'], '全クラス')
            self.assertEqual(row['頭数'], 3)
            self.assertIn('取消・除外あり', row['注意点'])
            # 対象日以降の戦績（未来の戦）とそのペースは使わない。
            self.assertNotIn('202699999999', self.requests)
            debut = races.set_index('レースID').loc['202608050505']
            self.assertTrue(str(debut['信頼度']).startswith('全展開での総合評価は保留'))
            pace = pd.read_csv(Path(root) / 'work' / 'pace.csv', dtype=str)
            self.assertIn('取得不可', set(pace['状態']))
            meta = json.loads((out / f'{DATE}_条件と取得状況.json').read_text(encoding='utf-8'))
            self.assertIn('的中率', meta['信頼度'])
            self.assertTrue((out / f'{DATE}_芝レース分析.html').exists())
            self.assertTrue((out / f'{DATE}_ラボ取得データ.zip').exists())

    def test_one_odd_history_table_does_not_stop_the_run(self):
        self.odd_table = True
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_batch(root), '完了')
            horses = pd.read_csv(Path(root) / 'out' / f'{DATE}_全頭展開別順位.csv', dtype={'レースID': str})
            main = horses.loc[horses['レースID'].eq('202608050510')].set_index('馬名')
            self.assertEqual(main.loc['試験馬イ', '戦績取得'], '取得不可')
            self.assertEqual(main.loc['試験馬ア', 'M順位'], 1)

    def test_rerun_requests_nothing_already_saved(self):
        with tempfile.TemporaryDirectory() as root:
            self.run_batch(root)
            first = len(self.requests)
            self.assertEqual(self.run_batch(root), '完了')
            self.assertEqual(len(self.requests), first)

    def test_stale_entries_are_refetched_but_histories_reused(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            self.run_batch(root)
            old = __import__('time').time() - 7 * 3600
            for path in (Path(root) / 'work').rglob('*'):
                if path.name.startswith('race_list') or (path.parent.name == 'entries' and path.suffix != '.error'):
                    os.utime(path, (old, old))
            self.requests.clear()
            self.run_batch(root)
            self.assertEqual(sum('race_list_sub' in u for u in self.requests), 1)
            self.assertEqual(sum('shutuba' in u for u in self.requests), 2)
            self.assertFalse(any(u.startswith('2022') for u in self.requests))  # 戦績は再要求しない

    def test_gate_stop_saves_progress_and_resumes(self):
        from keiba_fetch_policy import BudgetExhausted
        calls = {'n': 0}
        original = self.fake_results
        def flaky(hid, name):
            calls['n'] += 1
            if calls['n'] == 2:
                raise BudgetExhausted(1)
            return original(hid, name)
        with tempfile.TemporaryDirectory() as root:
            with patch.object(self, 'fake_results', flaky):
                status = self.run_batch(root)
            self.assertTrue(status.startswith('停止'))
            log = pd.read_csv(Path(root) / 'work' / 'horses' / 'horse_log.csv', dtype=str)
            self.assertEqual(int(log['状態'].eq('成功').sum()), 1)
            self.assertEqual(self.run_batch(root), '完了')
            log = pd.read_csv(Path(root) / 'work' / 'horses' / 'horse_log.csv', dtype=str)
            self.assertEqual(int(log['状態'].eq('成功').sum()), 3)


class OpenInMainTests(unittest.TestCase):
    def setUp(self):
        self.batch = WeekendBatchTests('test_end_to_end_outputs_and_rules')
        self.batch.setUp()
        self.addCleanup(self.batch.policy.stop)

    def test_weekend_races_list_and_bundle_without_requests(self):
        with tempfile.TemporaryDirectory() as root:
            self.batch.run_batch(root)
            before = len(self.batch.requests)
            races = wb.weekend_races(root, today='2026-11-14')
            self.assertIn('202608050510', set(races['レースID']))
            self.assertTrue(wb.weekend_races(root, today='2026-11-16').empty)  # 終わった週は出さない
            row = races.set_index('レースID').loc['202608050510']
            self.assertEqual(wb.race_label({**row.to_dict(), 'レースID': '202608050510'}), '11/14(土) 京都10R 試験S 芝1600m・3頭')
            info, horses, numbers, data, metadata, missing = wb.race_bundle(row['作業フォルダ'], '202608050510')
            self.assertEqual(set(horses), {'2022100001', '2022100002', '2022100003'})  # 取消馬は除外
            self.assertEqual(numbers['2022100001'], 1)
            self.assertIn('ハイ寄り', set(data['netkeibaペース'].dropna()))
            self.assertEqual(missing, [])
            self.assertEqual(metadata['取得元'].iloc[0], wb.ENTRY_URL + '202608050510')
            self.assertEqual(len(self.batch.requests), before)

    def test_merge_keeps_saved_enrichment(self):
        existing = pd.DataFrame([{'馬ID': '2020000001', 'レースID': '202601010101', '日付': '2026-10-01', '上がり順位': 2.0,
                                  'netkeibaペース': ''}])
        fresh = pd.DataFrame([{'馬ID': '2020000001', 'レースID': '202601010101', '日付': '2026-10-01', 'netkeibaペース': 'スロー寄り'},
                              {'馬ID': '2020000002', 'レースID': '202601010102', '日付': '2026-09-01', 'netkeibaペース': 'ハイ寄り'}])
        merged = wb.merge_into_saved(existing, fresh)
        self.assertEqual(len(merged), 2)
        first = merged.loc[merged['馬ID'].eq('2020000001')].iloc[0]
        self.assertEqual((first['上がり順位'], first['netkeibaペース']), (2.0, 'スロー寄り'))


class PickerTextTests(unittest.TestCase):
    def test_empty_picker_explains_how_to_refetch(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as root:
            script = ("import keiba_weekend_ui as ui\nfrom pathlib import Path\nui.ROOT=Path(%r)\n"
                      "ui.render_weekend_picker(lambda *a, **k: '選択しない')" % root)
            app = AppTest.from_string(script).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('まだ取得していません' in c.value for c in app.caption))


class ConfidenceRuleTests(unittest.TestCase):
    def test_score_and_selection_rule(self):
        conf = {'戦績確認率': 1.0, '順位表示率(最少)': 0.9, '平均項目充足率': 0.8, '近5走平均有効数': 4.0}
        score = conf['戦績確認率'] * 25 + conf['順位表示率(最少)'] * 30 + conf['平均項目充足率'] * 30 + conf['近5走平均有効数'] / 5 * 15
        self.assertAlmostEqual(score, 88.0)


if __name__ == '__main__':
    unittest.main()
