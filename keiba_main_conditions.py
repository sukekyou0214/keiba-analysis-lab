"""Race-scoped condition evidence, without a new page or predictive scores."""
from datetime import date
import re
import pandas as pd
from keiba_race_config import PRESETS, VENUES


def checked_context(rid, values):
    if not re.fullmatch(r'\d{12}', str(rid)):
        return None
    try:
        day = date.fromisoformat(str(values['race_date']))
        distance = int(values['distance'])
        venue = values['venue']
        surface = values['surface']
        if day.year != int(rid[:4]) or venue != VENUES.get(rid[4:6]) or surface not in {'芝','ダ'} or not 1000 <= distance <= 4000:
            return None
        return {'race_id':rid, 'race_date':day.isoformat(), 'venue':venue,
                'surface':surface, 'distance':distance, 'name':values.get('name','対象レース'),
                'source':values.get('source','入力設定')}
    except (KeyError, ValueError, TypeError):
        return None


def main_context(rid, state):
    saved = state.get('main_target_context', {})
    if saved.get('race_id') == rid:
        result = checked_context(rid, saved)
        if result:
            return result
    from urllib.parse import urlparse, parse_qs
    for config in PRESETS.values():
        if parse_qs(urlparse(config.entry_url).query).get('race_id',[''])[0] == rid:
            return checked_context(rid, {**vars(config), 'source':'登録済みの開催設定'})
    return None


def render_condition_context():
    import streamlit as st
    rid = str(st.session_state.get('auto_race_id',''))
    if not rid or st.session_state.get('auto_df') is None:
        return
    context = main_context(rid, st.session_state)
    if context:
        st.caption(f"今回の条件：{context['name']} · {context['race_date']} · {context['venue']}{context['surface']}{context['distance']}m（{context['source']}）")
    else:
        st.caption('今回条件の比較には、対象出走表の開催日・競馬場・コース・距離が必要です。')
        if st.button('出走表から開催条件を確認', key='main_read_condition_context'):
            from keiba_race_metadata import fetch_metadata
            try:
                detected = fetch_metadata('https://race.netkeiba.com/race/shutuba.html?race_id='+rid)
                context = checked_context(rid, {**detected, 'source':'出走表から取得'})
                if context:
                    st.session_state['main_target_context'] = context
                    st.rerun()
                else:
                    st.info('開催条件をすべて確認できませんでした。対象レースの取得設定で不足項目を入力できます。')
            except Exception as exc:
                st.warning('開催条件の確認を停止しました：'+str(exc)[:180])


def condition_evidence(data, context):
    empty = {'今回条件実績':'条件未確認', '前走内容':'対象開催日未確認'}
    if context is None:
        return empty
    if data.empty or '日付' not in data:
        return {'今回条件実績':'戦績未取得', '前走内容':'戦績未取得'}
    frame = data.copy().reset_index(drop=True)
    dates = pd.to_datetime(frame['日付'], errors='coerce')
    unknown_dates = dates.isna().any()
    frame = frame.loc[dates.lt(pd.Timestamp(context['race_date']))].copy()
    frame['日付'] = dates.loc[frame.index]
    if 'レースID' in frame:
        known = frame['レースID'].fillna('').astype(str).str.fullmatch(r'\d{12}')
        frame = pd.concat([frame.loc[known].drop_duplicates('レースID'), frame.loc[~known]])
    finish = pd.to_numeric(frame.get('着順_数値', pd.Series(index=frame.index,dtype=float)), errors='coerce')
    valid = finish.ge(1) & finish.mod(1).eq(0)
    fields = pd.to_numeric(frame.get('頭数', pd.Series(index=frame.index,dtype=float)), errors='coerce')
    valid &= fields.isna() | finish.le(fields)
    surface = frame.get('コース',pd.Series('',index=frame.index)).eq(context['surface'])
    venue = frame.get('競馬場',pd.Series('',index=frame.index)).eq(context['venue'])
    distance = pd.to_numeric(frame.get('距離_m',pd.Series(index=frame.index,dtype=float)), errors='coerce').eq(context['distance'])
    parts=[]
    for label, mask in [('同場同距離',surface & venue & distance), ('同距離',surface & distance), ('同場',surface & venue)]:
        total = int((mask & valid).sum())
        good = int((mask & valid & finish.le(3)).sum())
        parts.append(f'{label} {good}/{total}' if total else f'{label} —')
    previous='取得済み戦績に対象日前の走歴なし'
    if unknown_dates:
        previous='日付不明の戦績あり・前走確定不可'
    elif not frame.empty:
        latest = frame.sort_values('日付',ascending=False,kind='stable').iloc[0]
        if frame['日付'].eq(latest['日付']).sum() > 1:
            previous='同日の複数戦績あり・前走確定不可'
        else:
            def num(col, suffix, decimals=1):
                value=pd.to_numeric(latest.get(col),errors='coerce')
                return f'{value:.{decimals}f}{suffix}' if pd.notna(value) else '欠損'
            previous=(f"{latest['日付']:%Y/%m/%d} {latest.get('レース名','レース名欠損')} "
                      f"{num('着順_数値','着',0)}／着差 {num('着差','秒')}／上がり {num('上がり3F_秒','秒')}")
    return {'今回条件実績':'・'.join(parts), '前走内容':previous}
