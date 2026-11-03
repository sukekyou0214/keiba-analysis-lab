import unittest
import pandas as pd
from keiba_main_laps import enrich_laps, pending_lap_ids, recent_lap_ids
from keiba_diagnosis_evidence import summarize_lap_results


class MainLapTests(unittest.TestCase):
    def data(self):
        return pd.DataFrame([{'馬ID':str(h), 'レースID':rid, '日付':date, '着順_数値':place}
            for h,rid,date,place in [(1,'202608010101','2026-10-01',2),
                                     (2,'202608010101','2026-10-01',5),
                                     (1,'202608010102','2026-09-01',1)]])

    def payload(self):
        return {'検証':'OK', 'レース距離':2000,
                '200mラップ':'12.5 - 11.5 - 12.0 - 12.0 - 12.5 - 12.5 - 12.0 - 11.5 - 11.0 - 11.0'}

    def test_reuse_cached_race_once_and_keep_other_race_scope(self):
        saved=[];calls=[];sleeps=[]
        old=pd.DataFrame([{'レースID':'202505010101','検証':'OK','展開タイプ':'消耗戦寄り'}])
        def load(rid):calls.append(rid);return self.payload()
        out,error=enrich_laps(self.data(),old,load,lambda f:saved.append(f.copy()),
            cached=lambda rid:self.payload() if rid.endswith('1') else None, sleeper=sleeps.append)
        self.assertIsNone(error)
        self.assertEqual(calls,['202608010102'])
        self.assertEqual(sleeps,[2])
        self.assertEqual(len(saved),2)
        self.assertEqual(set(out['レースID']),{'202505010101','202608010101','202608010102'})
        self.assertEqual(pending_lap_ids(self.data(),out),[])

    def test_transport_failure_stops_and_restarts_at_failed_race(self):
        saved=[]
        def load(rid):
            if rid.endswith('2'):raise ConnectionError('通信停止')
            return self.payload()
        out,error=enrich_laps(self.data(),pd.DataFrame(),load,lambda f:saved.append(f.copy()),sleeper=lambda _:None)
        self.assertEqual(error,'通信停止')
        self.assertEqual(len(saved),1)
        self.assertEqual(pending_lap_ids(self.data(),out),['202608010102'])

    def test_missing_and_invalid_laps_do_not_get_invented_or_retried(self):
        def load(rid):
            if rid.endswith('1'):raise ValueError('ラップタイム見出しが見つかりません')
            return {'検証':'区間不足：3/10区間','200mラップ':'12.0 - 11.0 - 11.0','レース距離':2000}
        out,error=enrich_laps(self.data(),pd.DataFrame(),load,lambda _:None,sleeper=lambda _:None)
        self.assertIsNone(error)
        self.assertTrue(out['取得状態'].eq('取得不可').all())
        self.assertTrue(out['展開タイプ'].eq('判定不可').all())
        self.assertEqual(pending_lap_ids(self.data(),out),[])

    def test_malformed_response_stops(self):
        out,error=enrich_laps(self.data(),pd.DataFrame(),lambda _: {},lambda _:None,sleeper=lambda _:None)
        self.assertIsNotNone(error)
        self.assertTrue(out.empty)

    def test_short_distance_without_baseline_is_unclassified_not_crash(self):
        from keiba_core import classify_lap_shape_v16
        result=classify_lap_shape_v16('12.0 - 11.0 - 11.0 - 11.0 - 11.0 - 12.0',1200)
        self.assertEqual(result['展開タイプ'],'判定不可')

    def test_only_recent_five_per_horse_and_no_invalid_ids(self):
        data=pd.DataFrame([{'馬ID':'1','レースID':f'20260801010{i}','日付':f'2026-10-0{i}'} for i in range(1,7)])
        self.assertNotIn('202608010101',recent_lap_ids(data))
        self.assertEqual(len(recent_lap_ids(data)),5)

    def test_horse_outcomes_join_only_verified_laps_without_duplicate_count(self):
        shapes=pd.DataFrame([{'レースID':'202608010101','検証':'OK','展開タイプ':'瞬発戦寄り'},
                             {'レースID':'202608010102','検証':'取得不可','展開タイプ':'消耗戦寄り'}])
        h=self.data().loc[lambda d:d['馬ID'].eq('1')]
        self.assertEqual(summarize_lap_results(pd.concat([h,h]),shapes),'瞬 1/1・持 —・消 —・混 —')
        self.assertEqual(summarize_lap_results(self.data().loc[lambda d:d['馬ID'].eq('2')],shapes),'瞬 0/1・持 —・消 —・混 —')
