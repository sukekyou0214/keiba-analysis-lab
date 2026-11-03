from dataclasses import replace
import unittest
from unittest.mock import patch
import pandas as pd
from keiba_race_config import ELIZABETH_CONFIG
from keiba_reference_replay import replay_reference,selection_metrics


class ReferenceReplayTests(unittest.TestCase):
    def bundle(self,year=2016):
        code='09' if 2020<=year<=2022 else '08'
        venue='阪神' if code=='09' else '京都'
        rid=f'{year}{code}050411';rows=[];target=[]
        for n in [1,2]:
            base={'馬ID':f'201000000{n}','馬名':f'試験馬{n}','競馬場':venue,'コース':'芝','距離_m':2200,
                  'レース格':'G2','頭数':12,'4角順位':3,'netkeibaペース':'ミドル寄り'}
            for month in [8,9,10]:
                rows.append({**base,'日付':f'{year}-{month:02d}-01','レースID':f'{year}{code}0101{month:02d}',
                             'レース名':'前哨戦','着順_数値':n})
            rows.append({**base,'日付':f'{year}-11-13','レースID':rid,'レース名':'エリザベス女王杯',
                         '着順_数値':n,'netkeibaペース':'ハイ寄り'})
            target.append({'馬ID':base['馬ID'],'馬名':base['馬名'],'レースID':rid,'日付':f'{year}-11-13',
                           'レース名':'エリザベス女王杯','着順_数値':n,'人気':n})
        config=replace(ELIZABETH_CONFIG,first_year=year,last_year=year)
        return config,{'targets':pd.DataFrame(target),'horse_raw':pd.DataFrame(rows),
            'target_log':pd.DataFrame([{'race_id':rid,'status':'成功'}]),
            'horse_log':pd.DataFrame([{'horse_id':f'201000000{n}','status':'成功'} for n in [1,2]])}

    def test_target_results_popularity_and_future_history_do_not_change_predictions(self):
        config,tables=self.bundle()
        _,_,before=replay_reference(config,tables,paces=('M',))
        changed={k:v.copy() for k,v in tables.items()}
        changed['targets']['人気']=[2,1];changed['targets']['着順_数値']=[2,1]
        target_rows=changed['horse_raw']['レース名'].eq('エリザベス女王杯')
        changed['horse_raw'].loc[target_rows,'着順_数値']=99
        changed['horse_raw'].loc[target_rows,'netkeibaペース']='スロー寄り'
        future=changed['horse_raw'].iloc[:1].assign(日付='2017-01-01',レースID='201708010101',着順_数値=1)
        changed['horse_raw']=pd.concat([changed['horse_raw'],future],ignore_index=True)
        _,_,after=replay_reference(config,changed,paces=('M',))
        cols=['馬ID','参考点','参考順位','項目充足率','近5走有効数']
        pd.testing.assert_frame_equal(before[cols].sort_values('馬ID').reset_index(drop=True),after[cols].sort_values('馬ID').reset_index(drop=True))

    def test_tied_scores_select_every_tied_horse_without_result_order(self):
        scores=pd.Series([10,10,10,10]);finishes=pd.Series([1,2,4,5])
        metrics=selection_metrics(scores,finishes,5)
        self.assertEqual(metrics['1位群選出数'],4)
        self.assertEqual(metrics['3位以内群選出数'],4)
        self.assertEqual(metrics['3位以内群の3着内数'],2)
        reversed_metrics=selection_metrics(scores.iloc[::-1].reset_index(drop=True),finishes.iloc[::-1].reset_index(drop=True),5)
        self.assertEqual(metrics,reversed_metrics)

    def test_missing_and_invalid_results_are_not_counted_as_losses_or_wins(self):
        m=selection_metrics(pd.Series([4,3,2]),pd.Series([1,0,float('nan')]),3)
        self.assertEqual(m['3位以内群結果判明数'],1)
        self.assertEqual(m['3位以内群の3着内数'],1)

    def test_hanshin_is_replayed_as_hanshin_not_current_kyoto(self):
        config,tables=self.bundle(2020)
        summary,races,rows=replay_reference(config,tables,paces=('M',))
        self.assertEqual(set(summary['開催']),{'阪神'})
        self.assertEqual(set(rows['開催']),{'阪神'})
        self.assertEqual(len(races),5)

    def test_missing_source_course_metadata_stops_instead_of_guessing(self):
        config,tables=self.bundle()
        tables['horse_raw']=tables['horse_raw'].loc[~tables['horse_raw']['レース名'].eq('エリザベス女王杯')]
        with self.assertRaisesRegex(ValueError,'開催条件'):
            replay_reference(config,tables)

    def test_misdated_target_history_is_rejected_instead_of_leaking(self):
        config,tables=self.bundle()
        selected=tables['horse_raw']['レース名'].eq('エリザベス女王杯')
        tables['horse_raw'].loc[selected,'日付']='2016-11-12'
        with self.assertRaisesRegex(ValueError,'開催日'):
            replay_reference(config,tables)

    def test_scenarios_are_separate_and_do_not_triple_report_race_count(self):
        config,tables=self.bundle()
        summary,races,rows=replay_reference(config,tables)
        self.assertEqual(set(summary['想定ペース']),{'S','M','H'})
        self.assertTrue(summary['検証レース数'].eq(1).all())
        self.assertEqual(races['レースID'].nunique(),1)
        self.assertEqual(len(rows),6)
        weighted=races.loc[races['方法'].eq('参考点'),'順位提示数'].tolist()
        common=races.loc[races['方法'].eq('着順のみ（共通対象）'),'順位提示数'].tolist()
        self.assertEqual(weighted,common)

    def test_duplicate_scenario_is_rejected(self):
        config,tables=self.bundle()
        with self.assertRaises(ValueError):replay_reference(config,tables,paces=('M','M'))

    def test_saved_data_replay_does_not_access_source_sites(self):
        config,tables=self.bundle()
        with patch('requests.get',side_effect=AssertionError('新規取得禁止')):
            summary,_,_=replay_reference(config,tables,paces=('M',))
        self.assertFalse(summary.empty)
