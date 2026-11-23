"""地方の1レース取得（研究用）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import keiba_nar as nar

LIST = ('<ul><li><a href="../race/shutuba.html?race_id=202645112511"><span>11R</span> <span>浦和記念(JpnII)</span> ダ2000m</a></li>'
        '<li><a href="../race/shutuba.html?race_id=202645112510"><span>10R</span> 特別</a></li></ul>')
ENTRY = ('<div class="RaceName">浦和記念</div><table>'
         '<tr><td>1</td><td>1</td><td><a href="https://db.netkeiba.com/horse/2021101111" title="馬ア">馬ア</a></td></tr>'
         '<tr><td>2</td><td>2</td><td><a href="https://db.netkeiba.com/horse/2021102222" title="馬イ">馬イ</a></td></tr></table>')


class NarTests(unittest.TestCase):
    def test_find_race_by_name(self):
        f = nar.find_race(LIST, '浦和記念')
        self.assertEqual(list(f['レースID']), ['202645112511'])
        self.assertTrue(nar.find_race(LIST, 'ない').empty)

    def test_fallback_entry_parse(self):
        info, e = nar.parse_entries(ENTRY, '202645112511')
        self.assertEqual(list(e['馬ID']), ['2021101111', '2021102222'])
        self.assertEqual(list(e['馬番']), [1, 2])

    def test_run_saves_raw_entries_and_histories(self):
        pages = {nar.NAR_LIST_URL + '20261125': LIST, nar.NAR_ENTRY_URL + '202645112511': ENTRY}
        with tempfile.TemporaryDirectory() as root, \
             patch('keiba_core.get_results', lambda hid, name: pd.DataFrame({'日付': ['2026/10/01'], '馬ID': [hid]})):
            nar.run('20261125', '浦和記念', Path(root), fetch_text=lambda url, encoding=None: pages[url])
            status = json.loads((Path(root) / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['状態'], '完了')
            self.assertTrue((Path(root) / 'entry_202645112511.html').exists())
            self.assertEqual(len([p for p in (Path(root) / 'horses').glob('horse_*.csv') if p.name != 'horse_log.csv']), 2)


if __name__ == '__main__':
    unittest.main()
