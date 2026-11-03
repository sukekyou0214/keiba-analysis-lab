import unittest
from pathlib import Path
import pandas as pd
from keiba_main_conditions import main_context, checked_context, condition_evidence


class MainConditionTests(unittest.TestCase):
    def context(self):
        return main_context('202608050411',{})

    def data(self):
        return pd.DataFrame([{'レースID':rid,'日付':day,'着順_数値':place,'頭数':12,
            '競馬場':venue,'距離_m':distance,'コース':surface,'レース名':name,'着差':margin,'上がり3F_秒':34.1}
            for rid,day,place,venue,distance,surface,name,margin in [
                ('202608010101','2026-10-01',2,'京都',2200,'芝','前走試験',.2),
                ('202609010101','2026-09-01',6,'阪神',2200,'芝','阪神試験',1.0),
                ('202608010102','2026-08-01',1,'京都',2200,'ダ','ダート試験',-.2),
                ('202608050411','2026-11-15',1,'京都',2200,'芝','当日結果',-.5),
                ('202608050412','2026-11-16',1,'京都',2200,'芝','未来結果',-.5)]])

    def test_condition_scope_excludes_future_and_other_surface_separates_hanshin(self):
        out=condition_evidence(self.data(),self.context())
        self.assertEqual(out['今回条件実績'],'同場同距離 1/1・同距離 1/2・同場 1/1')
        self.assertIn('2026/10/01 前走試験 2着',out['前走内容'])
        self.assertIn('着差 0.2秒',out['前走内容'])
        self.assertNotIn('当日結果',out['前走内容'])

    def test_missing_finish_and_margin_are_not_zero(self):
        data=self.data().iloc[:1].copy()
        data['着順_数値']=float('nan');data['着差']=float('nan')
        out=condition_evidence(data,self.context())
        self.assertEqual(out['今回条件実績'],'同場同距離 —・同距離 —・同場 —')
        self.assertIn('着差 欠損',out['前走内容'])

    def test_unknown_date_prevents_false_previous_race_claim(self):
        data=self.data().iloc[:2].copy();data.loc[1,'日付']='不明'
        self.assertIn('前走確定不可',condition_evidence(data,self.context())['前走内容'])

    def test_context_never_follows_other_race(self):
        state={'main_target_context':self.context()}
        self.assertIsNone(main_context('202605010101',state))
        self.assertIsNone(checked_context('202605010101',self.context()))
        self.assertIsNone(checked_context('202608050411',{**self.context(),'race_date':'2025-11-15'}))

    def test_custom_race_uses_its_own_venue_distance(self):
        context=checked_context('202609010101',{'race_date':'2026-10-01','venue':'阪神','surface':'芝','distance':2200})
        out=condition_evidence(self.data(),context)
        self.assertEqual(out['今回条件実績'],'同場同距離 0/1・同距離 0/1・同場 0/1')

    def test_margin_survives_legacy_conversion(self):
        from keiba_core import convert_auto
        raw=pd.DataFrame([{'馬名':'試験馬','馬ID':'1','レースID':'202608010101','日付':'2026-10-01',
            '距離':'芝2200','馬 場':'良','開催':'京都','着 順':'2','上り':'34.1','通過':'4-4-3-2',
            '着差':'.2','レース名':'試験','格付け根拠':'試験','レース格':'G2'}])
        self.assertEqual(convert_auto(raw).iloc[0]['着差'],.2)

    def test_unknown_race_settings_do_not_stop_original_main(self):
        from streamlit.testing.v1 import AppTest
        from keiba_core import prepare
        rid='202605010101';data=self.data().iloc[:1].copy()
        data['馬ID']='1';data['馬名']='試験馬';data['レース格']='G2'
        a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
        for k,v in {'auto_df':prepare(data),'auto_horses':{'1':'試験馬'},'auto_numbers':{},
                    'auto_race_id':rid,'race_input_v141':rid,'main_shared_evidence':True}.items():
            a.session_state[k]=v
        a.run()
        self.assertEqual(len(a.exception),0)
        self.assertTrue(any(s.value=='出走馬の総合診断' for s in a.subheader))
        self.assertEqual(a.selectbox(key='common_race_choice').value,'その他（設定して利用）')

    def test_original_main_displays_condition_and_previous_race(self):
        from streamlit.testing.v1 import AppTest
        from keiba_core import prepare
        rid='202608050411';data=self.data().copy()
        data['馬ID']='1';data['馬名']='正式名の条件試験馬';data['レース格']='G2'
        a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
        for k,v in {'auto_df':prepare(data),'auto_horses':{'1':'正式名の条件試験馬'},'auto_numbers':{},
                    'auto_race_id':rid,'race_input_v141':rid,'evidence_active_main_rid':rid,
                    'main_shared_evidence':False}.items():
            a.session_state[k]=v
        a.run()
        self.assertEqual(len(a.exception),0)
        self.assertEqual(a.radio(key='main_evidence_kind_v22').value,'今回条件・前走')
        rendered='\n'.join(x.value for x in a.markdown)
        self.assertIn('同場同距離 1/1',rendered)
        self.assertIn('2026/10/01 前走試験 2着',rendered)
