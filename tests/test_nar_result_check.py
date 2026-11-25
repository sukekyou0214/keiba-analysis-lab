import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nar_result_check as rc  # noqa: E402


class ResultCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        w = Path(self.tmp.name)
        (w / 'results').mkdir()
        for rid, post, finish in (('r1', '12:20', [3, 1, 2, 4, 5, 6]), ('r2', '15:00', [4, 1, 2, 5, 6, 3])):
            (w / f'info_{rid}.json').write_text(json.dumps({'レースID': rid, '発走': post, '一覧表記': f'{rid} 見本'}), encoding='utf-8')
            pd.DataFrame({'着順原文': finish, '馬番': range(1, 7), '人気': range(1, 7)}).to_csv(w / 'results' / f'{rid}.csv', index=False)
        # r2：1着 2番・2着 3番・3着 6番 → 三連複 2-3-6、三連単 2-3-6
        (w / 'results' / 'r2_払戻.json').write_text(json.dumps([{'券種': '三連複', '組番': '2-3-6', '払戻': 2000},
                                                                {'券種': '三連単', '組番': '2-3-6', '払戻': 9000}]), encoding='utf-8')
        rows = []
        for rid in ('r1', 'r2'):
            for n in range(1, 7):
                rows.append({'レースID': rid, '発走': '', '馬番': n, '順位': n, 'C3U順位': 7 - n})
        self.pred = pd.DataFrame(rows)
        self.work = w

    def tearDown(self):
        self.tmp.cleanup()

    def test_scores_each_method_and_excludes_before_save(self):
        per = rc.check(self.pred, rc.load_results(self.work), ['順位', 'C3U順位'], saved_at='13:00')
        self.assertTrue(per.loc[per['レースID'].eq('r1'), '対象外（保存前に発走）'].all())
        r2 = per.loc[per['レースID'].eq('r2')].set_index('方式')
        self.assertFalse(r2.loc['順位', '5頭BOX的中'])  # 上位5＝1〜5、3着の 6番が入らない
        self.assertTrue(r2.loc['C3U順位', '5頭BOX的中'])  # 上位5＝6・5・4・3・2
        self.assertEqual(r2.loc['C3U順位', '三連単払戻'], 9000)
        s = rc.summarize(per).set_index('方式')
        self.assertEqual(s.loc['C3U順位', 'レース'], 1)
        self.assertAlmostEqual(s.loc['C3U順位', '三連複回収率'], 2.0)
        self.assertAlmostEqual(s.loc['C3U順位', '三連単回収率'], 1.5)


if __name__ == '__main__':
    unittest.main()
