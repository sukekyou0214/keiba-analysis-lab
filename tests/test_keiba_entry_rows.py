"""実際の出走表で確認した構造：本体表のあとに予想ラップ表（有料枠）の複製行・空行が続く。"""
import unittest
from unittest.mock import Mock, patch

from keiba_v22_collect import parse_entries


def main_row(row_id, number, horse_id, name, jockey):
    return (f'<tr class="HorseList" id="tr_{row_id}"><td class="Waku1 Txt_C">1</td>'
            f'<td class="Umaban1 Txt_C">{number}</td>'
            f'<td class="HorseInfo"><span class="HorseName"><a href="https://db.netkeiba.com/horse/{horse_id}" title="{name}">{name}</a></span></td>'
            f'<td class="Jockey"><a href="#">{jockey}</a></td><td class="Popular"><span></span></td></tr>')


def page(count, title='清水S'):
    body = ''.join(main_row(10 + i, i, f'20221000{i:02d}', f'試験馬{chr(0x30A2 + i * 2)}', '騎手')
                   for i in range(1, count + 1))
    predict = ('<table class="Shutuba_Table RaceTable01 PredictRap_Table NoCheckData isFreemium">'
               '<tr class="HorseList"><td class="Num Waku1 Sticky">1</td>'
               '<td class="Horse_Info Horse_Link"><a href="https://db.netkeiba.com/horse/2022100001">試験馬イ</a></td>'
               '<td class="Txt_R">36.1</td><td class="Txt_R">35.0</td></tr>'
               '<tr class="HorseList"><td></td><td></td><td></td><td></td></tr></table>')
    return (f'<h1 class="RaceName">{title}</h1>'
            f'<table class="Shutuba_Table RaceTable01 ShutubaTable">{body}</table>{predict}'
            '<a href="https://db.netkeiba.com/horse/2099999999">関連記事の馬</a>')


class EntryRowTests(unittest.TestCase):
    def test_predicted_lap_duplicate_and_template_rows_are_ignored(self):
        d = parse_entries(page(10), expected_name='清水S')
        self.assertEqual(len(d), 10)
        self.assertFalse(d['馬ID'].duplicated().any())
        self.assertNotIn('2099999999', set(d['馬ID']))
        self.assertEqual(d.iloc[0]['馬ID'], '2022100001')
        self.assertTrue(d['想定人気'].isna().all())

    def test_missing_horse_link_in_main_table_still_stops(self):
        html = page(6).replace('href="https://db.netkeiba.com/horse/2022100003"', 'href="#"')
        with self.assertRaises(ValueError):
            parse_entries(html, expected_name='清水S')

    def test_real_duplicate_in_main_table_still_stops(self):
        html = page(6).replace('2022100004', '2022100003')
        with self.assertRaises(ValueError):
            parse_entries(html, expected_name='清水S')

    def test_core_entry_extraction_matches_parser(self):
        from keiba_core import get_horses
        html = page(10)
        get_horses.clear()
        self.addCleanup(get_horses.clear)
        with patch('keiba_core.requests.get', return_value=Mock(text=html, apparent_encoding='utf-8')):
            horses, numbers = get_horses('202608050210')
        d = parse_entries(html, expected_name='清水S')
        self.assertEqual(set(horses), set(d['馬ID']))
        self.assertEqual(numbers['2022100001'], 1)
        self.assertEqual(sorted(numbers.values()), list(range(1, 11)))


if __name__ == '__main__':
    unittest.main()
