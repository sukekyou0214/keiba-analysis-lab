"""Embed common evidence in the original app without replacing its diagnosis."""
import pandas as pd
from urllib.parse import urlparse,parse_qs


def prepare_main_bridge(config,current):
    from keiba_core import convert_auto,prepare
    from keiba_affinity_pipeline import complete_horse_manifest
    entries=current.get('targets',pd.DataFrame())
    raw=current.get('horse_raw',pd.DataFrame())
    if entries.empty or raw.empty:raise ValueError('予定馬・戦績が未取得です')
    if not entries.get('取得元',pd.Series(dtype=str)).eq(config.entry_url).all():raise ValueError('出馬表が一致しません')
    ids=set(entries['馬ID'].astype(str))
    raw=raw.loc[raw['馬ID'].astype(str).isin(ids)].copy()
    log=current.get('horse_log',pd.DataFrame())
    if not log.empty:log=log.loc[log['horse_id'].astype(str).isin(ids)]
    if not complete_horse_manifest(entries,log,raw):raise ValueError('全頭の取得完了が必要です')
    data=raw if '着順_数値' in raw else convert_auto(raw)
    dates=pd.to_datetime(data['日付'],errors='coerce')
    if dates.isna().any():raise ValueError('日付不明の戦績があります')
    data=prepare(data.loc[dates.lt(pd.Timestamp(config.race_date))].copy())
    rid=parse_qs(urlparse(config.entry_url).query).get('race_id',[''])[0]
    if not rid:raise ValueError('出馬表URLが必要です')
    return {'auto_df':data,'auto_horses':dict(zip(entries['馬ID'].astype(str),entries['馬名'])),
            'auto_race_id':rid,'auto_numbers':{},'race_input_v141':config.entry_url}


def render_main_integration(menu):
    import streamlit as st
    sections={'🏇 総合診断':'総合診断','🐎 各馬の特徴':'詳細分析','⚙️ 分析条件':'詳細設定'}
    if menu not in sections:return
    main_data=st.session_state.get('auto_df')
    main_rid=st.session_state.get('auto_race_id','')
    if isinstance(main_data,pd.DataFrame) and not main_data.empty and main_rid and main_rid!=st.session_state.get('evidence_active_main_rid'):
        st.session_state['main_shared_evidence']=False
        st.session_state['evidence_active_main_rid']=main_rid
    from keiba_main_conditions import render_condition_context
    render_condition_context()
    if menu=='⚙️ 分析条件':
        from keiba_main_backup import render_main_backup
        render_main_backup()
    if st.checkbox('対象レースの過去傾向・適性・取得設定を開く',key='main_shared_evidence'):
        from keiba_workspace_ui import render_workspace
        with st.expander('対象レースの分析',expanded=True):
            render_workspace(sections[menu],embedded=True)
            render_pace_enrichment()
            from keiba_main_laps import render_main_laps
            render_main_laps()


def bridge_main_dataset(config,state):
    """Reuse the main session's actual rows, without issuing source requests."""
    rid=parse_qs(urlparse(config.entry_url).query).get('race_id',[''])[0]
    if not rid or str(state.get('auto_race_id',''))!=rid:return None
    data=state.get('auto_df')
    horses=state.get('auto_horses',{})
    if not isinstance(data,pd.DataFrame) or data.empty or not horses:return None
    if not {'馬ID','馬名','日付'}.issubset(data):raise ValueError('本体データに馬ID・馬名・日付が必要です')
    ids={str(h) for h in horses}
    raw=data.loc[data['馬ID'].astype(str).isin(ids)].copy()
    dates=pd.to_datetime(raw['日付'],errors='coerce')
    if dates.isna().any():raise ValueError('本体戦績に日付不明の行があります')
    raw=raw.loc[dates.lt(pd.Timestamp(config.race_date))].copy()
    entries=pd.DataFrame([{'馬ID':str(hid),'馬名':name,'取得元':config.entry_url,
        '情報区分':'本体読込済み・予定情報は元データに準拠','想定人気':float('nan'),
        '予定騎手':pd.NA,'引継元':'本体の読込済みデータ'} for hid,name in horses.items()])
    logs=[]
    for hid in ids:
        n=int(raw['馬ID'].astype(str).eq(hid).sum())
        logs.append({'horse_id':hid,'status':'成功' if n else '未取得','rows':n,
                     'reason':'本体内の戦績存在を確認（新規通信なし）' if n else '対象日前の戦績なし'})
    return {'targets':entries,'horse_raw':raw,'horse_log':pd.DataFrame(logs)}


def recent_pace_ids(data,per_horse=5):
    required={'馬ID','日付','レースID'}
    if data.empty or not required.issubset(data):return []
    frame=data.copy();frame['日付']=pd.to_datetime(frame['日付'],errors='coerce')
    recent=frame.sort_values('日付',ascending=False).groupby('馬ID',sort=False).head(per_horse)
    ids=recent['レースID'].fillna('').astype(str)
    known=data.get('netkeibaペース',pd.Series('',index=data.index)).isin(['スロー寄り','ミドル寄り','ハイ寄り'])
    done=set(data.loc[known,'レースID'].astype(str))
    if 'ペース取得状態' in data:
        done.update(data.loc[data['ペース取得状態'].eq('取得不可'),'レースID'].astype(str))
    return [x for x in ids[ids.str.fullmatch(r'\d{12}')].drop_duplicates() if x not in done]


def enrich_pace(data,loader,save,progress=lambda *args:None,sleeper=None):
    """Persist semantic missing labels; stop on transport or unknown failures."""
    import time
    from keiba_core import apply_netkeiba_pace
    sleeper=sleeper or time.sleep
    out=data.copy();pending=recent_pace_ids(out);error=None
    for n,rid in enumerate(pending,1):
        sleeper(2)
        try:
            value=loader(rid)
            if value not in ['スロー寄り','ミドル寄り','ハイ寄り']:raise ValueError('S/M/Hの明示判定を確認できません')
            out=apply_netkeiba_pace(out,{rid:value})
            save(out)
            progress(n,len(pending))
        except Exception as exc:
            reason=str(exc)[:180]
            missing_messages=('ラップタイム欄が見つかりません','ラップ欄にS/M/Hの明示表記が見つかりません','S/M/Hの明示判定を確認できません')
            if isinstance(exc,ValueError) and reason in missing_messages:
                mask=out['レースID'].astype(str).eq(rid)
                out.loc[mask,'ペース取得状態']='取得不可'
                out.loc[mask,'ペース取得理由']=reason
                # Never fabricate a pace for a semantic missing value.
                save(out)
                progress(n,len(pending))
                continue
            error=reason;break
    return out,error


def render_pace_enrichment():
    import streamlit as st
    data=st.session_state.get('auto_df')
    if not isinstance(data,pd.DataFrame) or data.empty:return
    pending=recent_pace_ids(data)
    unavailable=(int(data.loc[data['ペース取得状態'].eq('取得不可'),'レースID'].nunique()) if 'ペース取得状態' in data else 0)
    if unavailable:st.caption(f'ペース判定の取得不可：{unavailable}レース（欠損として保持・自動再取得しません）')
    st.caption(f'本体の直近5走：S/M/H未取得 {len(pending)}レース（同一レースは1回だけ取得）')
    if st.button('本体の不足データを補完：直近5走のS/M/H',disabled=not pending,key='main_enrich_pace'):
        from keiba_core import get_netkeiba_pace,save_race_cache
        bar=st.progress(0)
        def save(frame):
            save_race_cache(st.session_state['auto_race_id'],st.session_state['auto_horses'],frame)
            st.session_state['auto_df']=frame
        _,error=enrich_pace(data,get_netkeiba_pace,save,lambda n,total:bar.progress(n/total))
        if error:st.warning('取得を停止しました。取得済み分は保存済みです：'+error)
        else:st.success('本体のS/M/Hを補完しました。総合診断・ペース分析へ反映されます。')
