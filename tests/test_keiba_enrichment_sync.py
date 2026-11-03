from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from keiba_main_integration import merge_saved_enrichment,sync_main_enrichment


class EnrichmentSyncTests(unittest.TestCase):
    def data(self):
        return pd.DataFrame([{'馬ID':'2020000001','馬名':'試験馬','レースID':'202608010101',
            '日付':'2026-10-01','着順_数値':2,'頭数':12,'netkeibaペース':'','上がり順位':float('nan'),'着差':float('nan')}])

    def cached(self):
        return self.data().assign(netkeibaペース='ハイ寄り',上がり順位=2,着差=.2,着順_数値=9)

    def test_only_missing_factual_enrichment_is_filled(self):
        before=self.data();out,count=merge_saved_enrichment(before,self.cached())
        self.assertEqual(count,3)
        self.assertEqual(out.iloc[0]['netkeibaペース'],'ハイ寄り')
        self.assertEqual(out.iloc[0]['着順_数値'],2)
        self.assertEqual(out.iloc[0]['上がり順位'],2)
        self.assertEqual(out.iloc[0]['着差'],.2)
        self.assertEqual(before.iloc[0]['netkeibaペース'],'')

    def test_existing_known_values_are_preserved(self):
        data=self.data().assign(netkeibaペース='スロー寄り',上がり順位=1,着差=0.)
        out,count=merge_saved_enrichment(data,self.cached())
        self.assertEqual(count,0)
        self.assertIs(out,data)

    def test_other_horse_race_or_day_never_matches(self):
        for column,value in [('馬ID','2020000002'),('レースID','202608010102'),('日付','2026-10-02')]:
            cached=self.cached().copy();cached[column]=value
            self.assertEqual(merge_saved_enrichment(self.data(),cached)[1],0)

    def test_duplicate_cached_keys_are_not_arbitrarily_selected(self):
        cached=pd.concat([self.cached(),self.cached().assign(netkeibaペース='スロー寄り')],ignore_index=True)
        self.assertEqual(merge_saved_enrichment(self.data(),cached)[1],0)

    def test_unknown_day_or_invalid_ids_never_matches(self):
        for column,value in [('日付',None),('馬ID','不明'),('レースID','')]:
            data=self.data().copy();data[column]=value
            self.assertEqual(merge_saved_enrichment(data,self.cached())[1],0)

    def test_unavailable_is_saved_without_fabricating_pace(self):
        cached=self.data().assign(ペース取得状態='取得不可',ペース取得理由='明示なし')
        out,count=merge_saved_enrichment(self.data(),cached)
        self.assertEqual(count,1)
        self.assertEqual(out.iloc[0]['netkeibaペース'],'')
        self.assertEqual(out.iloc[0]['ペース取得状態'],'取得不可')

    def test_valid_saved_pace_resolves_stale_unavailable_status(self):
        data=self.data().assign(ペース取得状態='取得不可',ペース取得理由='以前の欠損')
        out,_=merge_saved_enrichment(data,self.cached())
        self.assertEqual(out.iloc[0]['netkeibaペース'],'ハイ寄り')
        self.assertEqual(out.iloc[0]['ペース取得状態'],'成功')
        self.assertEqual(out.iloc[0]['ペース取得理由'],'')

    def test_two_sessions_reuse_saved_data_and_skip_unchanged_reads(self):
        import keiba_core as core
        with tempfile.TemporaryDirectory() as root,patch.object(core,'CACHE_DIR',Path(root)):
            core.save_race_cache('202608050411',{'2020000001':'試験馬'},self.cached())
            state={'auto_race_id':'202608050411','auto_df':core.prepare(self.data())}
            self.assertEqual(sync_main_enrichment(state),3)
            self.assertEqual(state['auto_df'].iloc[0]['推定ペース'],'ハイ寄り')
            self.assertEqual(state['auto_df'].iloc[0]['着順_数値'],2)
            with patch.object(core,'load_race_cache',side_effect=AssertionError('不要な再読込')):
                self.assertEqual(sync_main_enrichment(state),0)
            state['auto_df']=core.prepare(self.data())
            self.assertEqual(sync_main_enrichment(state),3)

    def test_other_target_cache_is_not_used(self):
        import keiba_core as core
        with tempfile.TemporaryDirectory() as root,patch.object(core,'CACHE_DIR',Path(root)):
            core.save_race_cache('202608050411',{'2020000001':'試験馬'},self.cached())
            state={'auto_race_id':'202605041111','auto_df':core.prepare(self.data())}
            self.assertEqual(sync_main_enrichment(state),0)

    def test_original_row_index_and_count_are_preserved(self):
        data=pd.concat([self.data(),self.data()]);data.index=[7,7]
        out,count=merge_saved_enrichment(data,self.cached())
        self.assertEqual(out.index.tolist(),[7,7])
        self.assertEqual(len(out),2)
        self.assertEqual(count,6)
