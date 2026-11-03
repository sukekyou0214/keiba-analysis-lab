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
    if st.checkbox('対象レースの過去傾向・適性・取得設定を開く',key='main_shared_evidence'):
        from keiba_workspace_ui import render_workspace
        with st.expander('対象レースの分析',expanded=True):
            render_workspace(sections[menu],embedded=True)
