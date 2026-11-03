import os
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from keiba_race_config import *
from keiba_v22 import historical_features,rank_entries,venue_bundle,Store

class ConfigTests(unittest.TestCase):
    def test_race_scopes_separate(self):
        self.assertNotEqual(ELIZABETH_CONFIG.historical_scope,AUTUMN_CONFIG.historical_scope)
        self.assertNotEqual(ELIZABETH_CONFIG.current_scope,AUTUMN_CONFIG.current_scope)
        with tempfile.TemporaryDirectory() as root:
            s=Store(Path(root)/'db');s.save(ELIZABETH_CONFIG.historical_scope,{'targets':pd.DataFrame({'marker':['eliza']})})
            self.assertEqual(s.load(AUTUMN_CONFIG.historical_scope)[0],{})
    def test_other_race_requires_real_metadata(self):
        with self.assertRaises(ValueError):RaceConfig('test',ELIZABETH_CONFIG.special_url,ELIZABETH_CONFIG.entry_url,'2026-11-15','東京',2000)
        with self.assertRaises(ValueError):RaceConfig('test',ELIZABETH_CONFIG.special_url,'','2026-11-15','東京',2000,last_year=2026)
        c=RaceConfig('別重賞','https://race.netkeiba.com/special/index.html?id=123','','2027-02-01','中山',1800,first_year=2020,last_year=2026)
        self.assertIn('2020-2026',c.historical_scope)
    def test_tokyo_features_and_distance_not_kyoto(self):
        t=pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','レースID':'202505041111','日付':'2025-11-02','レース名':'天皇賞(秋)','着順_数値':2,'人気':2}])
        h=pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','日付':'2025-05-01','レースID':'202505010101','レース名':'前哨戦','着順_数値':2,'頭数':12,'コース':'芝','競馬場':'東京','距離_m':2000}])
        f=historical_features(t,h,AUTUMN_CONFIG)
        self.assertEqual(f.iloc[0]['開催区分'],'東京')
        self.assertEqual(f.iloc[0]['対象場距離走数'],1)
        self.assertEqual(f.iloc[0]['京都2200走数'],0)
        with self.assertRaises(ValueError):historical_features(t,h,ELIZABETH_CONFIG)
        ranking=rank_entries(t,h,config=AUTUMN_CONFIG)
        self.assertEqual(ranking.iloc[0]['対象場距離走数'],1)
    def test_page_switch_does_not_display_other_race(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'KEIBA_V22_STORE':'/tmp/config-test-placeholder.sqlite'}):
            # Use per-test path; no network actions in switching tests.
            os.environ['KEIBA_V22_STORE']=str(Path(root)/'db')
            a=AppTest.from_string("from keiba_workspace_ui import render_workspace\nrender_workspace()")
            
            a.run()
            for name in PRESETS:
                a.selectbox[0].set_value(name).run()
                self.assertEqual(len(a.exception),0)
                self.assertTrue(any(PRESETS[name].venue in x.value for x in a.caption))
                a.radio[0].set_value('詳細分析').run()
                self.assertEqual(len(a.exception),0)
            a.selectbox[0].set_value('その他（設定して利用）').run()
            self.assertEqual(len(a.exception),0)
