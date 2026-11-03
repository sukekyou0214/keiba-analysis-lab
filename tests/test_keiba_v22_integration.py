import unittest
import pandas as pd
from keiba_main_integration import prepare_main_bridge
from keiba_race_config import ELIZABETH_CONFIG as C

class MainIntegrationTests(unittest.TestCase):
    def test_bridge_filters_future_and_maps_original_state(self):
        entries=pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','取得元':C.entry_url}])
        raw=pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','レースID':rid,'日付':day,'着順_数値':1,'レース名':'試験','頭数':12} for rid,day in [('202508010101','2025-01-01'),('202608050411','2026-11-15')]])
        b={'targets':entries,'horse_raw':raw,'horse_log':pd.DataFrame([{'horse_id':'2020000001','status':'成功'}])}
        out=prepare_main_bridge(C,b)
        self.assertEqual(len(out['auto_df']),1)
        self.assertEqual(out['auto_race_id'],'202608050411')
        self.assertEqual(out['auto_horses'],{'2020000001':'試験馬'})
        self.assertEqual(out['race_input_v141'],C.entry_url)
    def test_missing_acquisition_never_replaces_original(self):
        with self.assertRaises(ValueError):prepare_main_bridge(C,{})
    def test_original_main_exposes_shared_command(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
        self.assertEqual(len(a.exception),0)
        self.assertTrue(any(c.label=='対象レースの過去傾向・適性・取得設定を開く' for c in a.checkbox))

    def test_main_rows_reused_without_network(self):
        from keiba_main_integration import bridge_main_dataset
        state={'auto_race_id':'202608050411','auto_horses':{'2020000001':'試験馬'},'auto_df':pd.DataFrame([
            {'馬ID':'2020000001','馬名':'試験馬','日付':'2026-10-01','上がり順位':1,'netkeibaペース':'ハイ寄り'},
            {'馬ID':'2020000001','馬名':'試験馬','日付':'2026-11-15','上がり順位':2}])}
        b=bridge_main_dataset(C,state)
        self.assertEqual(len(b['horse_raw']),1)
        self.assertEqual(b['horse_raw'].iloc[0]['上がり順位'],1)
        self.assertEqual(b['horse_raw'].iloc[0]['netkeibaペース'],'ハイ寄り')
        state['auto_race_id']='202605041111'
        self.assertIsNone(bridge_main_dataset(C,state))

    def test_main_version_and_removed_standalone_page(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        self.assertIn("APP_VERSION = '22.2'",(root/'app.py').read_text())
        self.assertFalse((root/'pages/3_エリザベス女王杯_Ver22.py').exists())
        self.assertNotIn('V22_ACCESS_PASSWORD',(root/'keiba_workspace_ui.py').read_text())

    def test_pace_dedup_and_stop_preserves_partial(self):
        from keiba_main_integration import enrich_pace,recent_pace_ids
        d=pd.DataFrame([{'馬ID':str(h),'馬名':str(h),'着順_数値':2,'日付':'2026-10-01','レースID':rid} for h,rid in [(1,'202608010101'),(2,'202608010101'),(3,'202608010102')]])
        self.assertEqual(len(recent_pace_ids(d)),2)
        saved=[];calls=[]
        def loader(rid):
            calls.append(rid)
            if rid.endswith('2'):raise ValueError('制限')
            return 'ハイ寄り'
        result,error=enrich_pace(d,loader,lambda f:saved.append(f.copy()),sleeper=lambda _:None)
        self.assertIsNotNone(error)
        self.assertEqual(len(saved),1)
        self.assertEqual(result['netkeibaペース'].eq('ハイ寄り').sum(),2)
        self.assertEqual(len(recent_pace_ids(result)),1)

    def test_missing_section_skips_without_inventing_pace(self):
        from keiba_main_integration import enrich_pace,recent_pace_ids
        d=pd.DataFrame([{'馬ID':str(h),'馬名':str(h),'着順_数値':2,'日付':'2026-10-01','レースID':rid} for h,rid in [(1,'202608010101'),(2,'202608010102')]])
        saved=[]
        def loader(rid):
            if rid.endswith('1'):raise ValueError('ラップタイム欄が見つかりません')
            return 'ミドル寄り'
        result,error=enrich_pace(d,loader,lambda f:saved.append(f.copy()),sleeper=lambda _:None)
        self.assertIsNone(error)
        self.assertEqual(len(saved),2)
        self.assertEqual(result.iloc[0]['ペース取得状態'],'取得不可')
        self.assertNotIn(result.iloc[0].get('netkeibaペース'),['スロー寄り','ミドル寄り','ハイ寄り'])
        self.assertEqual(recent_pace_ids(result),[])
