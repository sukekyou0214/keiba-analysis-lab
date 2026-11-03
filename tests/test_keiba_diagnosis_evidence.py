import unittest
import pandas as pd
from keiba_diagnosis_evidence import summarize_source_pace


class SourcePaceEvidenceTests(unittest.TestCase):
    def test_observed_rates_exclude_missing_finish_and_inferred_pace(self):
        data = pd.DataFrame([
            {'netkeibaペース':'スロー寄り', '着順_数値':2},
            {'netkeibaペース':'スロー寄り', '着順_数値':7},
            {'netkeibaペース':'ハイ寄り', '着順_数値':'取消'},
            {'netkeibaペース':None, '推定ペース':'ハイ寄り', '着順_数値':1},
            {'netkeibaペース':'ミドル寄り', '着順_数値':3},
        ])
        self.assertEqual(summarize_source_pace(data), 'S 1/2・M 1/1・H —')

    def test_duplicate_identified_race_is_not_double_counted(self):
        row = {'馬ID':'1','レースID':'202608010101','netkeibaペース':'ハイ寄り','着順_数値':1}
        self.assertEqual(summarize_source_pace(pd.DataFrame([row,row])), 'S —・M —・H 1/1')

    def test_missing_labels_are_not_zero_performance(self):
        self.assertEqual(summarize_source_pace(pd.DataFrame([{'着順_数値':1}])), '未取得')
