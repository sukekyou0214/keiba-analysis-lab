"""出走表のグレード表記から比較対象の既定値を決める。表記がなければ推測しない。"""
import unittest

from keiba_main_conditions import checked_context
from keiba_race_metadata import parse_metadata

URL = 'https://race.netkeiba.com/race/shutuba.html?race_id=202608050210'


def page(icons, data2):
    spans = ''.join(f'<span class="Icon_GradeType {x}"></span>' for x in icons)
    return (f'<h1 class="RaceName">試験S{spans}</h1>'
            '<div class="RaceData01">15:05発走 / 芝1600m 右 外</div>'
            f'<div class="RaceData02"><span>5回</span><span>京都</span><span>{data2}</span></div>')


class RaceGradeTests(unittest.TestCase):
    def test_graded_icon(self):
        self.assertEqual(parse_metadata(page(['Icon_GradeType1'], 'オープン'), URL)['grade'], 'G1')
        self.assertEqual(parse_metadata(page(['Icon_GradeType3'], 'オープン'), URL)['grade'], 'G3')

    def test_condition_race(self):
        html = page(['Icon_GradeType16 Icon_GradePos01', 'Icon_GradeType13 Icon_GradePos01'], '３勝クラス')
        self.assertEqual(parse_metadata(html, URL)['grade'], '条件戦')

    def test_open_without_icon_is_not_guessed(self):
        self.assertNotIn('grade', parse_metadata(page([], 'オープン'), URL))

    def test_context_keeps_only_known_grades(self):
        base = {'race_date': '2026-11-08', 'venue': '京都', 'surface': '芝', 'distance': 1600}
        self.assertEqual(checked_context('202608050210', {**base, 'grade': '条件戦'})['grade'], '条件戦')
        self.assertIsNone(checked_context('202608050210', {**base, 'grade': '重賞かも'})['grade'])
        self.assertIsNone(checked_context('202608050210', base)['grade'])


if __name__ == '__main__':
    unittest.main()
