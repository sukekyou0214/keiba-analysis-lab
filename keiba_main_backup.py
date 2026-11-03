"""Portable main-screen data, with explicit scope and no executable payloads."""
from io import BytesIO
import json
import re
import zipfile
import pandas as pd

FILES={'data':'history.csv','laps':'laps.csv','metadata':'entry_metadata.csv'}
MAX_ARCHIVE=25_000_000
MAX_RAW=75_000_000


def validate_bundle(manifest, frames):
    rid=manifest.get('race_id','')
    if manifest.get('format')!='keiba-main-v22' or not re.fullmatch(r'\d{12}',str(rid)):
        raise ValueError('本体の保存形式・レースIDを確認できません')
    horses=manifest.get('horses')
    if not isinstance(horses,dict) or not horses or any(not re.fullmatch(r'\d{10}',str(hid)) or not isinstance(name,str) or not name.strip() for hid,name in horses.items()):
        raise ValueError('馬ID・馬名が不正です')
    data=frames.get('data',pd.DataFrame())
    if data.empty or not {'馬ID','馬名','レースID','日付','着順_数値'}.issubset(data):
        raise ValueError('戦績の必須列が不足しています')
    if not set(data['馬ID'].astype(str)).issubset(horses):
        raise ValueError('出走馬と戦績の馬IDが一致しません')
    names=data['馬ID'].astype(str).map(horses)
    if not data['馬名'].astype(str).eq(names).all():
        raise ValueError('出走馬と戦績の馬名が一致しません')
    context=manifest.get('context')
    if context is not None:
        from keiba_main_conditions import checked_context
        if not isinstance(context,dict) or context.get('race_id')!=rid or checked_context(rid,context) is None:
            raise ValueError('開催条件とレースIDが一致しません')
    numbers=manifest.get('numbers',{})
    if not isinstance(numbers,dict) or not set(numbers).issubset(horses) or any(type(v)!=int or not 1<=v<=99 for v in numbers.values()):
        raise ValueError('馬番が不正です')
    laps=frames.get('laps',pd.DataFrame())
    if not laps.empty:
        if not {'レースID','検証','展開タイプ'}.issubset(laps) or not set(laps['レースID'].astype(str)).issubset(set(data['レースID'].astype(str))):
            raise ValueError('戦績にないレースのラップが含まれています')
        if laps['レースID'].duplicated().any():raise ValueError('ラップのレースIDが重複しています')
    meta=frames.get('metadata',pd.DataFrame())
    if not meta.empty:
        expected='https://race.netkeiba.com/race/shutuba.html?race_id='+rid
        if not {'馬ID','取得元'}.issubset(meta) or not meta['取得元'].eq(expected).all():
            raise ValueError('予定情報の対象レースが一致しません')
    return manifest,frames


def pack_main_backup(rid,horses,numbers,data,laps=None,metadata=None,context=None):
    frames={'data':data}
    for key,value in [('laps',laps),('metadata',metadata)]:
        if isinstance(value,pd.DataFrame) and not value.empty:frames[key]=value
    manifest={'format':'keiba-main-v22','race_id':str(rid),'horses':horses,'numbers':numbers,'context':context,
              'tables':list(frames),'created_at':pd.Timestamp.now(tz='Asia/Tokyo').isoformat()}
    validate_bundle(manifest,frames)
    target=BytesIO()
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
        for key,frame in frames.items():archive.writestr(FILES[key],frame.to_csv(index=False).encode('utf-8-sig'))
    result=target.getvalue()
    if len(result)>MAX_ARCHIVE:raise ValueError('保存サイズが25MBを超えています')
    return result


def load_main_backup(blob, expected_rid=None):
    if len(blob)>MAX_ARCHIVE:raise ValueError('保存サイズが25MBを超えています')
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        infos=archive.infolist()
        if len(infos)>4 or sum(x.file_size for x in infos)>MAX_RAW or len({x.filename for x in infos})!=len(infos):
            raise ValueError('保存ファイルのサイズ・構成が不正です')
        manifest=json.loads(archive.read('manifest.json'))
        tables=manifest.get('tables')
        if not isinstance(tables,list) or len(set(tables))!=len(tables) or any(x not in FILES for x in tables) or 'data' not in tables:
            raise ValueError('保存テーブルが不正です')
        if set(archive.namelist())!={'manifest.json'}|{FILES[x] for x in tables}:
            raise ValueError('保存ファイルの構成が一致しません')
        if expected_rid and manifest.get('race_id')!=str(expected_rid):
            raise ValueError('選択中の対象レースと保存ファイルが一致しません')
        frames={x:pd.read_csv(BytesIO(archive.read(FILES[x])),encoding='utf-8-sig',dtype={'馬ID':'string','レースID':'string'}) for x in tables}
    return validate_bundle(manifest,frames)


def render_main_backup():
    import streamlit as st
    from keiba_core import load_lap_full_progress,save_lap_full_progress,save_race_cache,prepare,CACHE_DIR
    from keiba_main_conditions import main_context
    from keiba_v22 import Store
    rid=str(st.session_state.get('auto_race_id',''))
    data=st.session_state.get('auto_df')
    with st.expander('本体データの保存・復元',expanded=False):
        st.caption('戦績・S/M/H・200mラップ・予定情報を一緒に保存します。過去10年の研究データは従来の保存ZIPで管理します。サーバー再起動に備えて保存できます。')
        if isinstance(data,pd.DataFrame) and not data.empty and rid:
            try:
                stored,_=Store().load('main-entry-metadata-'+rid)
                blob=pack_main_backup(rid,st.session_state['auto_horses'],st.session_state.get('auto_numbers',{}),data,
                    load_lap_full_progress(rid),stored.get('targets'),main_context(rid,st.session_state))
                st.download_button('本体データをまとめて保存',blob,file_name=f'keiba_main_{rid}.zip',mime='application/zip',key='main_backup_download_v22')
            except ValueError as exc:st.warning('保存ファイルを作成できません：'+str(exc))
        upload=st.file_uploader('本体の保存ZIPを復元',type=['zip'],key='main_backup_upload_v22')
        if upload is not None:
            try:
                manifest,frames=load_main_backup(upload.getvalue(),expected_rid=rid or None)
                st.caption(f"復元対象：{manifest['race_id']} · {len(manifest['horses'])}頭 · {len(frames['data'])}走")
                if st.button('保存データを本体へ復元',key='main_backup_restore_v22'):
                    # Keep the preceding files and metadata before replacing this race.
                    from shutil import copy2
                    import uuid
                    stamp=uuid.uuid4().hex
                    old_dir=CACHE_DIR/'restore-backups'/stamp
                    old_dir.mkdir(parents=True,exist_ok=True)
                    for path in CACHE_DIR.glob(manifest['race_id']+'.*'):
                        if path.is_file():copy2(path,old_dir/path.name)
                    lap_path=CACHE_DIR/f"lap_full_v165_{manifest['race_id']}.csv.gz"
                    if lap_path.exists():copy2(lap_path,old_dir/lap_path.name)
                    store=Store();scope='main-entry-metadata-'+manifest['race_id']
                    previous,_=store.load(scope)
                    if previous:store.save(scope+'-before-restore-'+stamp,previous)
                    prepared=prepare(frames['data'])
                    save_race_cache(manifest['race_id'],manifest['horses'],prepared)
                    if 'laps' in frames:save_lap_full_progress(manifest['race_id'],frames['laps'])
                    if 'metadata' in frames:store.save(scope,{'targets':frames['metadata']})
                    st.session_state['pending_main_bridge']={'auto_df':prepared,'auto_horses':manifest['horses'],
                        'auto_numbers':manifest['numbers'],'auto_race_id':manifest['race_id'],
                        'race_input_v141':manifest['race_id'],'main_target_context':manifest.get('context'),
                        'main_shared_evidence':False,'evidence_active_main_rid':manifest['race_id']}
                    st.rerun()
            except Exception as exc:st.warning('復元を停止しました：'+str(exc)[:180])
