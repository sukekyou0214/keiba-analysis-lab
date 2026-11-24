"""結果ページの払戻表の読み取り（三連複・三連単ボックスの回収率を確かめるため）。"""
import unittest

import keiba_backtest as bt

PAGE = '''<table class="Payout_Detail_Table"><tbody>
<tr class="Tan"><th>単勝</th><td class="Result"><div><span>5</span></div></td><td class="Payout"><span>350円</span></td></tr>
<tr class="Wide"><th>ワイド</th><td class="Result"><ul><li><span>1</span></li><li><span>5</span></li></ul><ul><li><span>5</span></li><li><span>9</span></li></ul></td>
<td class="Payout"><span>300円<br>1,020円</span></td></tr>
<tr class="Fuku3"><th>3連複</th><td class="Result"><ul><li><span>1</span></li><li><span>5</span></li><li><span>9</span></li></ul></td><td class="Payout"><span>3,450円</span></td></tr>
<tr class="Tan3"><th>3連単</th><td class="Result"><ul><li><span>5</span></li><li><span>1</span></li><li><span>9</span></li></ul></td><td class="Payout"><span>15,670円</span></td></tr>
</tbody></table>'''


class PayoutTest(unittest.TestCase):
    def test_reads_combos_and_amounts(self):
        rows = {(r['券種'], r['組番']): r['払戻'] for r in bt.parse_payouts(PAGE)}
        self.assertEqual(rows[('三連単', '5-1-9')], 15670)
        self.assertEqual(rows[('三連複', '1-5-9')], 3450)
        self.assertEqual(rows[('ワイド', '5-9')], 1020)
        self.assertEqual(rows[('単勝', '5')], 350)

    def test_missing_table_is_empty(self):
        self.assertEqual(bt.parse_payouts('<html></html>'), [])


if __name__ == '__main__':
    unittest.main()
