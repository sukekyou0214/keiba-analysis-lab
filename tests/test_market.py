import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import market_test as mt  # noqa: E402


def race(rid, odds, pop, lab_rank, win_idx, nums=None):
    n = len(odds)
    return pd.DataFrame({'レースID': rid, '馬ID': [f'{rid}-{i}' for i in range(n)],
                         '馬番': nums or [str(i + 1) for i in range(n)], '勝ち': [i == win_idx for i in range(n)],
                         '人気': pop, 'オッズ': odds, '点': [10.0 - r for r in lab_rank], 'ラボ順位': lab_rank,
                         '点z': mt.standardize([10.0 - r for r in lab_rank]).to_numpy(), '払戻あり': True})


class OddsTest(unittest.TestCase):
    def test_odds_value(self):
        self.assertEqual(mt.odds_value('7.8'), 7.8)
        self.assertEqual(mt.odds_value('1,234.5'), 1234.5)
        for bad in ('', '---', '取消', None, '0'):
            self.assertTrue(np.isnan(mt.odds_value(bad)))

    def test_payout_table(self):
        info = {'払戻': [{'券種': '単勝', '組番': '4', '払戻': 11100}, {'券種': '複勝', '組番': '4', '払戻': 530},
                         {'券種': '複勝', '組番': '5', '払戻': 110}, {'券種': '馬連', '組番': '4-5', '払戻': 2250}]}
        t = mt.payout_table(info)
        self.assertEqual(t, {('単勝', '4'): 11100.0, ('複勝', '4'): 530.0, ('複勝', '5'): 110.0})
        self.assertIsNone(mt.payout_table({}))

    def test_standardize_fills_missing_with_min(self):
        z = mt.standardize([3.0, np.nan, 1.0])
        self.assertAlmostEqual(z.iloc[1], z.iloc[2])
        self.assertAlmostEqual(z.mean(), 0.0)
        self.assertTrue((mt.standardize([2.0, 2.0]) == 0).all())


class DiscordTest(unittest.TestCase):
    def test_discord_picks_lab_top_and_unpopular(self):
        g = race('r', [2, 5, 9, 20, 40], [1, 2, 3, 4, 5], [4, 1, 5, 2, 3], 0)
        self.assertEqual(list(mt.discord(g, 2, 4)['馬番']), ['4'])      # ラボ2位・4番人気
        self.assertEqual(sorted(mt.discord(g, 3, 4)['馬番']), ['4', '5'])
        self.assertTrue(mt.discord(g, 1, 4).empty)                       # ラボ1位は2番人気

    def test_choose_tie_breaks_on_count(self):
        t = {c: {'回収率': 0.5, '対象の馬': 1} for c in mt.CANDIDATES}
        t[('複勝', 2, 6)] = {'回収率': 1.2, '対象の馬': 10}
        t[('単勝', 3, 8)] = {'回収率': 1.2, '対象の馬': 30}
        self.assertEqual(mt.choose(t), ('単勝', 3, 8))


class RecoveryTest(unittest.TestCase):
    def test_race_bets_and_recovery(self):
        tab = pd.concat([race('a', [2, 5, 9, 20], [1, 2, 3, 4], [3, 2, 4, 1], 3),
                         race('b', [2, 5, 9, 20], [1, 2, 3, 4], [1, 2, 3, 4], 0)])
        infos = {'a': {'払戻': [{'券種': '単勝', '組番': '4', '払戻': 2000}, {'券種': '複勝', '組番': '4', '払戻': 500}]},
                 'b': {'払戻': [{'券種': '単勝', '組番': '1', '払戻': 200}]}}
        rb = mt.race_bets(tab, infos, ('単勝', 1, 4))
        self.assertEqual(list(rb['賭け金']), [100.0, 0.0])
        self.assertEqual(list(rb['払戻']), [2000.0, 0.0])
        self.assertAlmostEqual(mt.recovery(rb), 20.0)
        fav = mt.race_bets(tab, infos, ('1番人気', None, None))
        self.assertAlmostEqual(mt.recovery(fav), 1.0)                     # 200円÷200円
        lab1 = mt.race_bets(tab, infos, ('ラボ1位', None, None))
        self.assertAlmostEqual(mt.recovery(lab1), (2000 + 200) / 200)
        r = mt.boot_recovery(rb)
        self.assertEqual(r['対象の馬'], 1)
        self.assertLessEqual(r['下限'], r['回収率'])

    def test_no_payout_race_skipped(self):
        tab = race('a', [2, 5], [1, 2], [1, 2], 0)
        self.assertTrue(mt.race_bets(tab, {'a': {}}, ('単勝', 1, 1)).empty)


class ClogitTest(unittest.TestCase):
    def test_fit_recovers_market_and_lab(self):
        rng = np.random.default_rng(1)
        races = []
        for i in range(400):
            n = 10
            logodds = rng.normal(2.5, 0.8, n)
            lab = rng.normal(0, 1, n)
            u = -1.0 * logodds + 0.5 * lab
            p = np.exp(u - u.max()); p /= p.sum()
            w = rng.choice(n, p=p)
            g = race(str(i), list(np.exp(logodds)), list(range(1, n + 1)), list(range(1, n + 1)), w)
            g['点z'] = lab
            races.append(g)
        b1, b2 = mt.fit_clogit(races, False), mt.fit_clogit(races, True)
        self.assertLess(b1[0], 0)
        self.assertGreater(b2[1], 0.2)
        ev = mt.m1_eval(races, b1, b2)
        self.assertGreater(ev['差'], 0)

    def test_m1_races_drops_missing_odds(self):
        a = race('a', [2, np.nan], [1, 2], [1, 2], 0)
        b = race('b', [2, 3], [1, 2], [1, 2], 0)
        ok, dropped = mt.m1_races(pd.concat([a, b]))
        self.assertEqual(len(ok), 1)
        self.assertEqual(dropped['オッズなし'], 1)


if __name__ == '__main__':
    unittest.main()
