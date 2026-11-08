"""CSV読込で全欠損の文字列列がfloatになっても、文字列判定を書き込めること（pandas 2/3共通）。"""
import io
import unittest

import pandas as pd


def csv_roundtrip(frame):
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    return pd.read_csv(buffer, dtype={'馬ID': str, 'レースID': str})


def history(rows):
    return pd.DataFrame([{'馬ID': str(h), '馬名': f'馬{h}', '着順_数値': 2, '日付': '2026-10-01',
                          'レースID': rid, 'netkeibaペース': '', 'ペース取得状態': '', 'ペース取得理由': ''}
                         for h, rid in rows])


class TextColumnTests(unittest.TestCase):
    def test_restored_csv_has_float_text_columns(self):
        restored = csv_roundtrip(history([(1, '202608010101')]))
        self.assertTrue(pd.api.types.is_float_dtype(restored['netkeibaペース']))

    def test_core_apply_pace_after_csv_roundtrip(self):
        from keiba_core import apply_netkeiba_pace, prepare
        restored = prepare(csv_roundtrip(history([(1, '202608010101'), (2, '202608010102')])))
        out = apply_netkeiba_pace(restored, {'202608010101': 'ハイ寄り'})
        self.assertEqual(out.loc[out['レースID'].eq('202608010101'), '推定ペース'].tolist(), ['ハイ寄り'])
        self.assertEqual(out.loc[out['レースID'].eq('202608010102'), '推定ペース'].tolist(), ['不明'])
        # 未取得のレースへペースを補完しない。
        self.assertEqual(int(out['netkeibaペース'].isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り']).sum()), 1)

    def test_enrich_pace_marks_missing_after_csv_roundtrip(self):
        from keiba_main_integration import enrich_pace
        restored = csv_roundtrip(history([(1, '202608010101'), (2, '202608010102')]))
        def loader(rid):
            if rid.endswith('1'):
                raise ValueError('ラップタイム欄が見つかりません')
            return 'ミドル寄り'
        result, error = enrich_pace(restored, loader, lambda f: None, sleeper=lambda _: None)
        self.assertIsNone(error)
        first = result.loc[result['レースID'].eq('202608010101')].iloc[0]
        self.assertEqual(first['ペース取得状態'], '取得不可')
        self.assertNotIn(first['netkeibaペース'], ['スロー寄り', 'ミドル寄り', 'ハイ寄り'])
        second = result.loc[result['レースID'].eq('202608010102')].iloc[0]
        self.assertEqual(second['netkeibaペース'], 'ミドル寄り')


if __name__ == '__main__':
    unittest.main()
