"""共通取得ゲート：間隔・上限・拒否時の停止と待機時刻（実通信なし、時計と通信を差し替え）。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import requests

import keiba_fetch_policy  # noqa: F401  (names are refreshed in setUp)

NAMES = ('BudgetExhausted', 'CooldownActive', 'FetchPolicy', 'PolicyStop', 'RefusedResponse')


def refresh_names():
    """keiba_release may reload the module during the suite; always use the current classes."""
    import sys
    module = sys.modules['keiba_fetch_policy']
    globals().update({name: getattr(module, name) for name in NAMES})


refresh_names()

DB = 'https://db.netkeiba.com/horse/result/2022100001/'
RACE = 'https://race.netkeiba.com/race/result.html?race_id=202608050411'


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(round(seconds, 3))
        self.now += seconds


def response(status=200):
    return Mock(status_code=status, text='ok')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        refresh_names()

    def make(self, statuses=None, path=None):
        clock = Clock()
        replies = list(statuses or [])
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            reply = replies.pop(0) if replies else 200
            if isinstance(reply, Exception):
                raise reply
            return response(reply)
        policy = FetchPolicy(state_path=path, clock=clock, sleeper=clock.sleep, transport=transport)
        return policy, clock, calls

    def test_interval_is_per_host_and_shared_by_all_callers(self):
        policy, clock, calls = self.make()
        policy.get(DB); policy.get(RACE); policy.get(DB)
        self.assertEqual(clock.waits, [3.0])  # only the second db request waits
        clock.now += 1
        policy.get(RACE)  # 4 seconds since the last race request: no wait
        self.assertEqual(clock.waits, [3.0])
        clock.now += 1
        policy.get(DB)  # 2 seconds since the last db request: waits the remaining 1
        self.assertEqual(clock.waits, [3.0, 1.0])
        self.assertEqual(len(calls), 5)

    def test_other_hosts_are_not_gated(self):
        policy, clock, calls = self.make()
        policy.get('https://example.com/a'); policy.get('https://example.com/b')
        self.assertEqual(clock.waits, [])
        self.assertEqual(policy.snapshot(), [])

    def test_budget_stops_before_sending(self):
        policy, clock, calls = self.make()
        with policy.budget(2) as budget:
            policy.get(DB); policy.get(RACE)
            with self.assertRaises(BudgetExhausted):
                policy.get(DB)
        self.assertEqual((len(calls), budget.used), (2, 2))
        policy.get(DB)  # outside the operation there is no budget

    def test_two_refusals_close_the_host_until_the_stored_time(self):
        policy, clock, calls = self.make([400, 429])
        with self.assertRaises(RefusedResponse):
            policy.get(DB)
        with self.assertRaises(CooldownActive) as stop:
            policy.get(DB)
        self.assertEqual(stop.exception.until, clock.now + 30 * 60)
        with self.assertRaises(CooldownActive):
            policy.get(DB)  # no request is sent while closed
        self.assertEqual(len(calls), 2)
        policy.get(RACE)  # other host stays usable
        self.assertEqual(policy.cooldown_until('db.netkeiba.com'), stop.exception.until)
        self.assertIsInstance(stop.exception, requests.RequestException)

    def test_cooldown_doubles_then_caps_and_resets_after_success(self):
        policy, clock, calls = self.make([403, 503] * 6 + [200, 400, 400])
        lengths = []
        for _ in range(6):
            with self.assertRaises(RefusedResponse):
                policy.get(DB)
            with self.assertRaises(CooldownActive) as stop:
                policy.get(DB)
            lengths.append(stop.exception.until - clock.now)
            clock.now = stop.exception.until + 1
        self.assertEqual([x / 60 for x in lengths], [30, 60, 120, 240, 360, 360])
        policy.get(DB)  # success resets the level
        with self.assertRaises(RefusedResponse):
            policy.get(DB)
        with self.assertRaises(CooldownActive) as stop:
            policy.get(DB)
        self.assertEqual(stop.exception.until - clock.now, 30 * 60)

    def test_transport_errors_count_as_refusals_and_success_clears_streak(self):
        policy, clock, calls = self.make([requests.ConnectionError('reset'), 200, requests.Timeout('slow')])
        with self.assertRaises(requests.ConnectionError):
            policy.get(DB)
        policy.get(DB)
        with self.assertRaises(requests.Timeout):
            policy.get(DB)  # not two in a row, so no cooldown
        self.assertIsNone(policy.cooldown_until())

    def test_not_found_is_not_a_refusal(self):
        policy, clock, calls = self.make([404, 404, 404])
        for _ in range(3):
            self.assertEqual(policy.get(DB).status_code, 404)
        self.assertIsNone(policy.cooldown_until())

    def test_state_survives_a_new_process(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            policy, clock, _ = self.make([400, 400], path=path)
            policy.set_min_interval(5)
            with self.assertRaises(RefusedResponse):
                policy.get(DB)
            with self.assertRaises(CooldownActive):
                policy.get(DB)
            again = FetchPolicy(state_path=path, clock=clock, sleeper=clock.sleep, transport=lambda *a, **k: response())
            self.assertEqual(again.min_interval, 5)
            with self.assertRaises(CooldownActive):
                again.get(DB)
            row = again.snapshot()[0]
            self.assertEqual((row['要求数'], row['拒否・通信エラー'], row['状態']), (2, 2, '停止中'))

    def test_interval_setting_is_bounded(self):
        policy, _, _ = self.make()
        policy.set_min_interval(0.1)
        self.assertEqual(policy.min_interval, 2.0)
        policy.set_min_interval(99)
        self.assertEqual(policy.min_interval, 30.0)


class LoopStopTests(unittest.TestCase):
    def setUp(self):
        refresh_names()

    def test_histories_stop_at_once_when_gate_stops(self):
        from keiba_main_integration import fetch_missing_histories
        horses = {f'202000000{i}': f'馬{i}' for i in range(1, 5)}
        calls = []
        def loader(hid, name):
            calls.append(hid)
            if hid == '2020000002':
                raise BudgetExhausted(40)
            return pd.DataFrame([{'馬ID': hid}])
        datasets, errors, unfetched = fetch_missing_histories(horses, loader, sleeper=lambda _: None, delay=0)
        self.assertEqual(calls, ['2020000001', '2020000002'])
        self.assertEqual(list(unfetched), ['2020000002', '2020000003', '2020000004'])
        self.assertEqual(len(datasets), 1)
        self.assertEqual(errors, [])

    def test_collect_by_race_separates_permanent_transient_and_unfetched(self):
        from keiba_main_integration import collect_by_race, PACE_UNAVAILABLE
        def loader(rid):
            if rid == 'b':
                raise ValueError('ラップタイム欄が見つかりません')
            if rid == 'c':
                raise RefusedResponse('400')
            if rid == 'd':
                raise CooldownActive('db.sp.netkeiba.com', 0)
            return 'ハイ寄り'
        results, errors, unavailable, unfetched = collect_by_race('abcde', loader, PACE_UNAVAILABLE)
        self.assertEqual(results, {'a': 'ハイ寄り'})
        self.assertEqual(list(unavailable), ['b'])
        self.assertEqual(set(errors), {'c'})  # a gate stop is not a failure of race d
        self.assertEqual(unfetched, ['d', 'e'])
        self.assertTrue(issubclass(CooldownActive, PolicyStop))

    def test_unexpected_parse_errors_are_retried_later_not_marked_unavailable(self):
        from keiba_main_integration import collect_by_race, PACE_UNAVAILABLE
        def loader(rid):
            raise ValueError('想定外の表')
        _, errors, unavailable, _ = collect_by_race(['a'], loader, PACE_UNAVAILABLE)
        self.assertEqual((list(errors), unavailable), (['a'], {}))

    def test_mark_race_status_keeps_results(self):
        from keiba_main_integration import mark_race_status
        d = pd.DataFrame({'レースID': ['1', '1', '2'], '上がり順位': [1.0, 2.0, 3.0],
                          'ペース取得状態': [float('nan')] * 3})
        out = mark_race_status(d, ['1'], 'ペース取得状態', '取得不可', 'ペース取得理由', {'1': 'ラップタイム欄が見つかりません'})
        self.assertEqual(out['ペース取得状態'].tolist()[:2], ['取得不可', '取得不可'])
        self.assertTrue(pd.isna(out['ペース取得状態'].iloc[2]))
        self.assertEqual(out['上がり順位'].tolist(), [1.0, 2.0, 3.0])


if __name__ == '__main__':
    unittest.main()
