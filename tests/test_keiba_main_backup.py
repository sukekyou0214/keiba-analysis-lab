from io import BytesIO
import json
import unittest
import zipfile
import pandas as pd
from keiba_main_backup import pack_main_backup,load_main_backup
from keiba_main_conditions import main_context


class MainBackupTests(unittest.TestCase):
    rid='202608050411'
    horses={'2022000001':'保存試験馬'}

    def data(self):
        return pd.DataFrame([{'馬ID':'2022000001','馬名':'保存試験馬','レースID':'202608010101',
             '日付':'2026-10-01','着順_数値':2,'netkeibaペース':'ハイ寄り','着差':float('nan')}])

    def blob(self):
        laps=pd.DataFrame([{'レースID':'202608010101','検証':'OK','展開タイプ':'瞬発戦寄り'}])
        meta=pd.DataFrame([{'馬ID':'2022000001','取得元':'https://race.netkeiba.com/race/shutuba.html?race_id='+self.rid,'想定人気':float('nan')}])
        return pack_main_backup(self.rid,self.horses,{'2022000001':7},self.data(),laps,meta,main_context(self.rid,{}))

    def test_round_trip_keeps_all_main_data_without_inventing_missing_values(self):
        manifest,frames=load_main_backup(self.blob(),self.rid)
        self.assertEqual(manifest['horses'],self.horses)
        self.assertEqual(manifest['numbers'],{'2022000001':7})
        self.assertEqual(frames['data'].iloc[0]['馬ID'],'2022000001')
        self.assertEqual(frames['data'].iloc[0]['netkeibaペース'],'ハイ寄り')
        self.assertTrue(pd.isna(frames['data'].iloc[0]['着差']))
        self.assertEqual(len(frames['laps']),1)
        self.assertTrue(pd.isna(frames['metadata'].iloc[0]['想定人気']))

    def test_wrong_target_refuses_restore_before_any_write(self):
        with self.assertRaisesRegex(ValueError,'対象レース'):
            load_main_backup(self.blob(),'202605041111')

    def test_foreign_horse_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'馬ID'):
            pack_main_backup(self.rid,self.horses,{},self.data().assign(馬ID='2022000002'))

    def test_foreign_lap_is_rejected(self):
        laps=pd.DataFrame([{'レースID':'202505010101','検証':'OK','展開タイプ':'瞬発戦寄り'}])
        with self.assertRaisesRegex(ValueError,'戦績にない'):
            pack_main_backup(self.rid,self.horses,{},self.data(),laps)

    def test_foreign_metadata_is_rejected(self):
        metadata=pd.DataFrame([{'馬ID':'2022000001','取得元':'https://race.netkeiba.com/race/shutuba.html?race_id=202605041111'}])
        with self.assertRaisesRegex(ValueError,'予定情報'):
            pack_main_backup(self.rid,self.horses,{},self.data(),metadata=metadata)

    def test_unknown_zip_member_is_rejected(self):
        out=BytesIO()
        with zipfile.ZipFile(BytesIO(self.blob())) as source,zipfile.ZipFile(out,'w') as target:
            for member in source.namelist():target.writestr(member,source.read(member))
            target.writestr('../extra.py','invalid')
        with self.assertRaises(ValueError):load_main_backup(out.getvalue())

    def test_context_of_another_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'開催条件'):
            pack_main_backup(self.rid,self.horses,{},self.data(),context=main_context('202605041111',{}))

    def test_optional_context_does_not_crash_main_context_resolution(self):
        blob=pack_main_backup(self.rid,self.horses,{},self.data())
        manifest,_=load_main_backup(blob)
        self.assertIsNone(manifest['context'])
        self.assertIsNotNone(main_context(self.rid,{'main_target_context':None}))
