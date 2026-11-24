"""地方の格付けの点（事前登録 2026-11-25 の追記どおり）。"""
import math
import unittest

from keiba_nar_class import nar_class_level as f


class NarClassTest(unittest.TestCase):
    def test_single_classes(self):
        for text, want in (('A1', 5.0), ('A2', 4.5), ('B1', 4.0), ('B2', 3.5), ('B3', 3.0), ('C1', 2.5), ('C2', 2.0), ('C3', 1.5)):
            self.assertEqual(f(f'特別({text})'), want, text)

    def test_spans_are_averaged(self):
        self.assertEqual(f('とちぎ和牛賞特別(B2B3)'), 3.25)
        self.assertEqual(f('小春空特別(C1C2)'), 2.25)

    def test_group_suffix_is_ignored(self):
        self.assertEqual(f('C2三四'), 2.0)
        self.assertEqual(f('C2五六'), 2.0)

    def test_graded(self):
        self.assertEqual(f('浦和記念(3上) Jpn2'), 9.0)
        self.assertEqual(f('JpnI'), 10.0)
        self.assertEqual(f('埼玉栄冠(重賞)'), 5.5)
        self.assertEqual(f('ゴールドカップ S2'), 6.0)

    def test_missing(self):
        for text in ('2歳二', '3歳未格付選抜馬', 'よく分からない', '', None):
            self.assertTrue(math.isnan(f(text)), text)


if __name__ == '__main__':
    unittest.main()
