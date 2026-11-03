import tempfile
import unittest
from pathlib import Path
import pandas as pd
from keiba_v22 import *
from keiba_v22_collect import collect_remaining,parse_entries

class V22Test(unittest.TestCase):
    def history(self):
        return pd.DataFrame([
            {'馬ID':'2020000001','馬名':'試験馬','レースID':f'{year}08010101','日付':date,'レース名':name,'コース':'芝','競馬場':'京都','距離_m':2200,'着順_数値':finish,'頭数':12,'4角順位':3,'上がり3F_秒':34.1,'着差':.2,'netkeibaペース':'S'}
            for year,date,name,finish in [(2024,'2024-10-01','前哨戦',2),(2025,'2025-11-16','当日',1),(2026,'2026-01-01','未来戦',1)]])
    def target(self):
        return pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','レースID':'202508050611','日付':'2025-11-16','レース名':'エリザベス女王杯','着順_数値':1,'人気':6,'単勝オッズ':12.5}])
    def test_venue_bundle_scopes_all_inputs(self):
        t=self.target()
        t=pd.concat([t,t.assign(レースID='202109050411',日付='2021-11-14',馬ID='2020000002')],ignore_index=True)
        b={'targets':t,'target_log':pd.DataFrame({'race_id':t['レースID']}),'horse_raw':pd.DataFrame({'馬ID':t['馬ID']}),'horse_log':pd.DataFrame({'horse_id':t['馬ID']}),'affinity':pd.DataFrame({'stale':[1]})}
        for venue,code in [('京都','08'),('阪神','09')]:
            scoped=venue_bundle(b,venue)
            self.assertNotIn('affinity',scoped)
            self.assertTrue(scoped['targets']['レースID'].str[4:6].eq(code).all())
            self.assertEqual(len(scoped['horse_raw']),1)
            self.assertEqual(len(scoped['horse_log']),1)
            self.assertEqual(len(scoped['target_log']),1)

    def test_scope_and_hanshin(self):
        t=self.target();validate_scope(t,ELIZABETH,2016,2025)
        t['レース名']='天皇賞（秋）'
        with self.assertRaises(ValueError):validate_scope(t,ELIZABETH,2016,2025)
        t=self.target();t['日付']='2021-11-14';t['レースID']='202108050411'
        with self.assertRaises(ValueError):validate_scope(t,ELIZABETH,2016,2025)
        t['レースID']='202109050411';validate_scope(t,ELIZABETH,2016,2025)
    def test_strict_prior_and_unknown(self):
        f=historical_features(self.target(),self.history())
        self.assertEqual(f.iloc[0]['過去走数'],1)
        self.assertEqual(f.iloc[0]['前走'],'前哨戦')
        self.assertEqual(f.iloc[0]['S走数'],1)
        self.assertTrue(pd.isna(f.iloc[0]['H複勝率']))
        h=self.history().drop(columns='netkeibaペース')
        self.assertTrue(history_frame(h)['ペース区分'].isna().all())
    def test_persistence_scope_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            s=Store(Path(root)/'a.sqlite');s.save('elizabeth',{'targets':self.target()})
            self.assertFalse(Store(s.path).load('elizabeth')[0]['targets'].empty)
            self.assertEqual(s.load('autumn')[0],{})
    def test_failure_stops_and_resume_skips(self):
        with tempfile.TemporaryDirectory() as root:
            s=Store(Path(root)/'a.sqlite');t=self.target()
            t=pd.concat([t,t.assign(馬ID='2020000002',馬名='次馬')],ignore_index=True)
            called=[]
            def loader(hid,name):
                called.append(hid)
                if hid.endswith('2'):raise ValueError('429')
                return self.history()
            with self.assertRaises(RuntimeError):collect_remaining(t,loader,{},s,'scope',sleeper=lambda _:None)
            saved,_=s.load('scope');self.assertEqual(len(saved['horse_raw']),3)
            called.clear()
            with self.assertRaises(RuntimeError):collect_remaining(t,loader,saved,s,'scope',sleeper=lambda _:None)
            self.assertEqual(called,['2020000002'])
    def test_missing_popularity_no_value_claim(self):
        entries=self.target().assign(想定人気=float('nan'))
        ranking=rank_entries(entries,self.history(),cutoff='2025-11-16')
        self.assertIn('保留',ranking.iloc[0]['妙味'])
        empty=rank_entries(entries,pd.DataFrame())
        self.assertTrue(empty['参考順位'].isna().all())
    def test_venue_separation_returns_missing_place_roi(self):
        f=historical_features(self.target(),self.history())
        f=pd.concat([f,f.assign(開催区分='阪神',着順_数値=7)],ignore_index=True)
        p=pattern_tables(f);p=p.loc[p['項目'].eq('人気帯')]
        self.assertEqual(p.loc[p['開催'].eq('京都'),'3着内率'].iloc[0],100)
        self.assertEqual(p.loc[p['開催'].eq('阪神'),'3着内率'].iloc[0],0)
        self.assertTrue(p['複勝回収率'].isna().all())
    def test_entry_parser_missing_pop_and_jockey(self):
        html='<h1 class="RaceName">エリザベス女王杯</h1><table>'+''.join(f'<tr class="HorseList"><td class="HorseName"><a href="https://db.netkeiba.com/horse/202000000{i}/">馬{i}</a></td></tr>' for i in range(1,7))+'</table>'
        d=parse_entries(html);self.assertEqual(len(d),6);self.assertTrue(d['想定人気'].isna().all())
    def test_page_without_secret_is_closed(self):
        from streamlit.testing.v1 import AppTest
        a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'pages/3_エリザベス女王杯_Ver22.py')).run()
        self.assertEqual(len(a.exception),0)
        self.assertEqual(len(a.button),0)
    def test_page_authenticated_all_sections(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as root:
            from unittest.mock import patch
            with patch.dict(os.environ,{'KEIBA_V22_STORE':str(Path(root)/'test.sqlite')}):
                a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'pages/3_エリザベス女王杯_Ver22.py'))
                a.secrets['V22_ACCESS_PASSWORD']='test-only';a.session_state['v22_authenticated']=True
                a.run();self.assertEqual(len(a.exception),0)
                for section in ['詳細分析','詳細設定','総合診断']:
                    a.radio[0].set_value(section).run();self.assertEqual(len(a.exception),0)

if __name__=='__main__':unittest.main()
