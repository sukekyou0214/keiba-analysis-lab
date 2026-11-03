import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from keiba_main_ranking import main_ranking
from keiba_main_conditions import main_context


class MainRankingTests(unittest.TestCase):
    def data(self,n=5):
        return pd.DataFrame([{'馬ID':'1','馬名':'参考順位の試験馬','レースID':f'20260801010{i}',
            '日付':f'2026-10-0{i}','レース名':'試験G2','レース格':'G2','コース':'芝','競馬場':'京都',
            '距離_m':2200,'着順_数値':2,'頭数':12,'netkeibaペース':'ミドル寄り'} for i in range(1,n+1)])

    def run_rank(self,data,metadata=None):
        return main_ranking(data,{'1':'参考順位の試験馬'},main_context('202608050411',{}),metadata)

    def metadata(self,url='https://race.netkeiba.com/race/shutuba.html?race_id=202608050411'):
        return pd.DataFrame([{'馬ID':'1','取得元':url,'想定人気':6,'予定騎手':'予定騎手試験'}])

    def test_same_race_metadata_and_complete_reference_candidate(self):
        out=self.run_rank(self.data(),self.metadata()).iloc[0]
        self.assertEqual(out['参考順位'],1)
        self.assertEqual(out['近5走有効数'],5)
        self.assertEqual(out['評価項目充足率'],100)
        self.assertTrue(out['妙味'].startswith('中穴候補'))

    def test_foreign_popularity_never_produces_candidate(self):
        out=self.run_rank(self.data(),self.metadata('https://race.netkeiba.com/race/shutuba.html?race_id=202605041111')).iloc[0]
        self.assertTrue(pd.isna(out['想定人気']))
        self.assertIn('保留',out['妙味'])

    def test_one_result_cannot_earn_rank_or_candidate(self):
        out=self.run_rank(self.data(1),self.metadata()).iloc[0]
        self.assertTrue(pd.isna(out['参考順位']))
        self.assertIn('根拠不足',out['判定'])
        self.assertFalse(out['妙味'].startswith('中穴候補'))

    def test_three_results_can_rank_but_not_be_hole_candidate(self):
        out=self.run_rank(self.data(3),self.metadata()).iloc[0]
        self.assertEqual(out['参考順位'],1)
        self.assertFalse(out['妙味'].startswith('中穴候補'))

    def test_invalid_finishes_never_inflate_reference_score(self):
        d=self.data();d['着順_数値']=0
        out=self.run_rank(d,self.metadata()).iloc[0]
        self.assertTrue(pd.isna(out['参考順位']))
        self.assertEqual(out['近5走有効数'],0)

    def test_duplicate_and_future_rows_do_not_change_rank_score(self):
        d=self.data()
        future=d.iloc[:1].assign(日付='2026-11-15',レースID='202608050411',着順_数値=1)
        expanded=pd.concat([d,d,future],ignore_index=True)
        original=self.run_rank(d).iloc[0];actual=self.run_rank(expanded).iloc[0]
        self.assertEqual(original['総合参考点'],actual['総合参考点'])
        self.assertEqual(actual['近5走有効数'],5)

    def test_low_coverage_does_not_get_rank(self):
        d=self.data().assign(競馬場='東京',距離_m=1600,netkeibaペース=None)
        self.assertTrue(pd.isna(self.run_rank(d).iloc[0]['参考順位']))

    def test_lower_class_wins_do_not_replace_graded_reference_by_default(self):
        graded=self.data()
        lower=graded.assign(レース格='3勝',日付='2026-10-20',レースID='202608010201',着順_数値=1)
        original=self.run_rank(graded).iloc[0]
        actual=self.run_rank(pd.concat([graded,lower],ignore_index=True)).iloc[0]
        self.assertEqual(original['総合参考点'],actual['総合参考点'])
        self.assertEqual(actual['比較対象'],'G1〜G3')
        missing=self.run_rank(lower).iloc[0]
        self.assertTrue(pd.isna(missing['参考順位']))
        self.assertEqual(missing['判定'],'対象の重賞戦績なし・保留')

    def test_unknown_grade_does_not_become_graded_evidence(self):
        self.assertTrue(pd.isna(self.run_rank(self.data().drop(columns='レース格')).iloc[0]['参考順位']))
        result=main_ranking(self.data().drop(columns='レース格'),{'1':'試験馬'},main_context('202608050411',{}),graded_only=False)
        self.assertEqual(result.iloc[0]['比較対象'],'全クラス')
        self.assertEqual(result.iloc[0]['参考順位'],1)

    def test_dirt_ranking_uses_dirt_pace_evidence(self):
        context={**main_context('202608050411',{}),'surface':'ダ'}
        out=main_ranking(self.data().assign(コース='ダ'),{'1':'試験馬'},context).iloc[0]
        self.assertEqual(out['M走数'],5)
        self.assertEqual(out['評価項目充足率'],100)

    def test_original_main_reference_table_works_without_source_calls(self):
        from streamlit.testing.v1 import AppTest
        from keiba_core import prepare
        rid='202608050411'
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'KEIBA_V22_STORE':str(Path(root)/'db.sqlite')}):
            a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
            for k,v in {'auto_df':prepare(self.data()),'auto_horses':{'1':'参考順位の試験馬'},'auto_numbers':{},
                        'auto_race_id':rid,'race_input_v141':rid,'evidence_active_main_rid':rid,'main_shared_evidence':False}.items():
                a.session_state[k]=v
            a.run()
            self.assertEqual(len(a.exception),0)
            self.assertEqual(a.radio(key='main_evidence_kind_v22').value,'今回条件・前走')
            a.radio(key='main_evidence_kind_v22').set_value('全頭参考順位（研究中）').run()
            self.assertEqual(len(a.exception),0)
            html='\n'.join(x.value for x in a.markdown)
            self.assertIn('実績参考順位',html)
            self.assertTrue(any(b.label=='全頭参考順位CSV' for b in a.get('download_button')))
