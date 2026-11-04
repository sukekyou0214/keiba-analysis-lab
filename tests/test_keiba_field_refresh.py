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
