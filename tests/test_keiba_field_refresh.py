import unittest

import pandas as pd

from keiba_main_integration import horses_without_history


class FieldRefreshTests(unittest.TestCase):
    def test_only_current_entrants_without_history_are_requested(self):
        field = {'2020000001': '既存馬', '2020000002': '追加馬'}
        history = pd.DataFrame({'馬ID': [2020000001.0, 2020000003.0, float('nan')]})

        self.assertEqual(horses_without_history(field, history), {'2020000002': '追加馬'})
        self.assertEqual(horses_without_history(field, None), field)
        self.assertEqual(len(history), 3)


class FetchStopTests(unittest.TestCase):
    def test_stops_after_consecutive_refusals_and_keeps_fetched(self):
        import requests
        from keiba_main_integration import fetch_missing_histories
        horses = {f'202000000{i}': f'馬{i}' for i in range(1, 7)}
        calls = []
        def loader(horse_id, name):
            calls.append(horse_id)
            if horse_id in {'2020000002', '2020000003'}:
                raise requests.HTTPError('400 Client Error')
            return pd.DataFrame([{'馬ID': horse_id}])
        datasets, errors, unfetched = fetch_missing_histories(horses, loader, sleeper=lambda _: None)
        self.assertEqual(calls, ['2020000001', '2020000002', '2020000003'])
        self.assertEqual(len(datasets), 1)
        self.assertEqual(len(errors), 2)
        self.assertEqual(list(unfetched), ['2020000004', '2020000005', '2020000006'])

    def test_single_refusal_or_parse_error_continues(self):
        import requests
        from keiba_main_integration import fetch_missing_histories
        horses = {f'202000000{i}': f'馬{i}' for i in range(1, 5)}
        def loader(horse_id, name):
            if horse_id == '2020000001':
                raise requests.HTTPError('400 Client Error')
            if horse_id in {'2020000002', '2020000003'}:
                raise ValueError('成績表が見つかりません')
            return pd.DataFrame([{'馬ID': horse_id}])
        waits = []
        datasets, errors, unfetched = fetch_missing_histories(horses, loader, sleeper=waits.append)
        self.assertEqual((len(datasets), len(errors), unfetched), (1, 3, {}))
        self.assertEqual(waits, [2.0, 2.0, 2.0])
