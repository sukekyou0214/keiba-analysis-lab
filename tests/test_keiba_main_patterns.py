import unittest
import pandas as pd
from keiba_main_patterns import history_config,verified_patterns,current_pattern_features
from keiba_main_conditions import main_context
from keiba_race_config import ELIZABETH_CONFIG as C
from keiba_v22 import match_historical_patterns,history_frame


class MainPatternTests(unittest.TestCase):
    def tables(self):
        targets=[];logs=[]
        for year in range(2016,2026):
            venue='09' if 2020<=year<=2022 else '08'
            rid=f'{year}{venue}050411'
            for n in [1,2]:
                targets.append({'レースID':rid,'日付':f'{year}-11-15','馬ID':f'202000000{n}',
                    '馬名':f'試験馬{n}','レース名':'エリザベス女王杯','着順_数値':1 if n==1 else 6,'人気':n})
            logs.append({'race_id':rid,'status':'成功'})
        raw=pd.DataFrame([{'レースID':'201408010101','日付':'2014-10-01','馬ID':f'202000000{n}',
            '馬名':f'試験馬{n}','レース名':'試験前走','着順_数値':2,'コース':'芝','競馬場':'京都',
            '距離_m':2200,'頭数':12,'4角順位':3} for n in [1,2]])
        return {'targets':pd.DataFrame(targets),'target_log':pd.DataFrame(logs),'horse_raw':raw,
                'horse_log':pd.DataFrame([{'horse_id':f'202000000{n}','status':'成功'} for n in [1,2]])}

    def test_verified_patterns_keep_kyoto_separate_from_hanshin(self):
        patterns,coverage=verified_patterns(C,self.tables())
        self.assertEqual(coverage,{'開催':'京都','全出走記録':20,'照合出走記録':14,'照合開催数':7})
        matches=match_historical_patterns({'前走':'試験前走','脚質':'先行'},patterns,venue='京都')
        self.assertEqual(len(matches),2)
        self.assertTrue(matches['標本数'].eq(14).all())
        self.assertTrue(matches['過去3着内率'].eq(50).all())

    def test_incomplete_log_blocks_patterns(self):
        tables=self.tables();tables['target_log'].loc[0,'status']='失敗'
        with self.assertRaises(ValueError):verified_patterns(C,tables)

    def test_duplicate_target_records_cannot_inflate_samples(self):
        tables=self.tables()
        tables['targets']=pd.concat([tables['targets'],tables['targets'].iloc[:1]],ignore_index=True)
        with self.assertRaises(ValueError):verified_patterns(C,tables)

    def test_invalid_finish_does_not_count_as_top_three(self):
        tables=self.tables()
        tables['targets'].loc[tables['targets']['馬ID'].eq('2020000001'),'着順_数値']=0
        patterns,_=verified_patterns(C,tables)
        row=patterns.loc[patterns['開催'].eq('京都')&patterns['項目'].eq('前走')].iloc[0]
        self.assertEqual(row['出走数'],14)
        self.assertEqual(row['結果判明数'],7)
        self.assertEqual(row['3着内数'],0)
        matches=match_historical_patterns({'前走':'試験前走','脚質':'先行'},patterns,minimum=10,venue='京都')
        self.assertTrue(matches.empty)

    def test_other_race_and_missing_year_cannot_be_matched(self):
        tables=self.tables();tables['targets']['レース名']='天皇賞'
        with self.assertRaises(ValueError):verified_patterns(C,tables)
        tables=self.tables();tables['targets']=tables['targets'].loc[lambda d:~d['日付'].str.startswith('2016')]
        with self.assertRaises(ValueError):verified_patterns(C,tables)

    def test_actual_previous_race_uses_all_classes_and_excludes_target_day(self):
        raw=self.tables()['horse_raw'].iloc[:1].copy()
        before=raw.assign(日付='2026-09-01',レース名='古い重賞',レース格='G2',レースID='202608010101')
        previous=raw.assign(日付='2026-10-01',レース名='試験前走',レース格='3勝',レースID='202608010102')
        target=raw.assign(日付='2026-11-15',レース名='当日結果',レースID='202608050411')
        result=current_pattern_features(pd.concat([before,previous,target]),'2020000001',C)
        self.assertEqual(result['前走'],'試験前走')

    def test_context_isolation_and_custom_config(self):
        context=main_context('202608050411',{})
        self.assertEqual(history_config(context,{}),C)
        self.assertIsNone(history_config({**context,'distance':2000},{}))
        saved=vars(C)
        self.assertEqual(history_config(context,{'main_history_config':saved}),C)

    def test_invalid_corner_position_is_unknown_not_running_style(self):
        raw=self.tables()['horse_raw'].assign(**{'4角順位':0})
        self.assertTrue(history_frame(raw)['脚質'].eq('不明').all())
