"""Present the existing common reference ranking in the original diagnosis."""
from types import SimpleNamespace
import pandas as pd


def main_ranking(data, horses, context, metadata=None, pace='M'):
    from keiba_v22 import rank_entries
    if context is None:
        return pd.DataFrame()
    source='https://race.netkeiba.com/race/shutuba.html?race_id='+context['race_id']
    rows=[]
    meta=metadata if isinstance(metadata,pd.DataFrame) else pd.DataFrame()
    if not meta.empty and {'取得元','馬ID'}.issubset(meta):
        meta=meta.loc[meta['取得元'].eq(source)].drop_duplicates('馬ID',keep='last')
    else:
        meta=pd.DataFrame()
    for hid,name in horses.items():
        matched=meta.loc[meta['馬ID'].astype(str).eq(str(hid))] if not meta.empty else meta
        row={'馬ID':str(hid),'馬名':name,'想定人気':float('nan'),'予定騎手':pd.NA}
        if not matched.empty:
            for col in ['想定人気','予定騎手','確認日時']:
                row[col]=matched.iloc[0].get(col,pd.NA)
        rows.append(row)
    return rank_entries(pd.DataFrame(rows),data,config=SimpleNamespace(**context),pace=pace)


def fetch_current_metadata(context):
    import requests
    from keiba_v22_collect import parse_entries
    url='https://race.netkeiba.com/race/shutuba.html?race_id='+context['race_id']
    response=requests.get(url,timeout=20,allow_redirects=False,headers={'User-Agent':'KeibaAnalysisLab/22'})
    if 300 <= response.status_code < 400:
        raise ValueError('転送先へ自動アクセスせず停止しました')
    response.raise_for_status()
    response.encoding=response.apparent_encoding or response.encoding
    return parse_entries(response.text,source=url,expected_name=context['name'])


def render_main_ranking(renderer, data, horses, numbers, context):
    import streamlit as st
    from keiba_v22 import Store
    from keiba_core import display_horse_column,load_lap_full_progress
    from keiba_diagnosis_evidence import summarize_lap_results
    if context is None:
        st.info('対象レースの開催条件を確認すると参考順位を表示できます。')
        return
    store=Store();scope='main-entry-metadata-'+context['race_id']
    stored,_=store.load(scope)
    metadata=stored.get('targets',pd.DataFrame())
    if st.button('予定騎手・想定人気を更新（戦績の再取得なし）',key='main_update_entry_metadata'):
        try:
            fresh=fetch_current_metadata(context)
            store.save(scope,{'targets':fresh})
            metadata=fresh
            st.success('出走表の予定情報を保存しました。未掲載の値は欠損のままです。')
        except Exception as exc:
            st.warning('予定情報の更新を停止しました。保存済み情報を使用します：'+str(exc)[:180])
    if not metadata.empty:
        dates=metadata.get('確認日時',pd.Series(dtype=str)).dropna()
        if len(dates):st.caption('予定情報の確認日時：'+str(dates.iloc[0]))
        if set(metadata['馬ID'].astype(str)) != {str(x) for x in horses}:
            st.info('出走表と本体の対象馬が異なります。参考順位は本体で読込済みの馬が対象です。')
    pace=st.radio('参考順位に使う想定ペース',['S','M','H'],index=1,horizontal=True,key='main_reference_pace_v22')
    try:
        ranking=main_ranking(data,horses,context,metadata,pace)
    except ValueError as exc:
        st.warning('参考順位を保留しました：'+str(exc))
        return
    if ranking.empty:
        st.info('参考順位の対象馬がありません。')
        return
    shown=display_horse_column(ranking,horses,numbers)
    renderer(shown,['馬名','参考順位','総合参考点','評価項目充足率','想定人気','判定'],
             {'馬名':'馬番＋正式馬名','参考順位':'実績参考順位','総合参考点':'参考点','評価項目充足率':'項目充足率(%)','想定人気':'想定人気','判定':'根拠の状態'})
    st.caption('着順の頭数補正40%、同場同距離20%、同距離15%、同場10%、想定ペース別15%で仮集計。欠損項目を除いて重みを再配分します。近5走の有効成績3走以上・項目充足率60%以上で順位を付けます。')
    st.caption('参考点は実績の比較用です。レース格・相手関係・時計の補正を含まず、総合能力・勝率・期待値の評価は未検証です。')
    candidates=ranking.loc[ranking['妙味'].str.startswith('中穴候補',na=False)]
    st.write('中穴の参考候補：'+('、'.join(candidates['馬名']) if len(candidates) else '保留（条件を満たす馬、または想定人気の確認が不足）'))
    st.caption('想定4〜9番人気・参考順位5位以内・項目充足率80%以上・近5走すべて有効の馬を抽出。期待値が高いと検証したものではありません。')
    with st.expander('参考順位の根拠を見る',expanded=False):
        name=st.selectbox('根拠を確認する馬',ranking['馬名'].tolist(),key='main_reference_horse_v22')
        row=ranking.loc[ranking['馬名'].eq(name)].iloc[0]
        for label in ['近5走有効数','近5走着順参考点','対象場距離走数','対象場距離複勝率','対象距離走数','対象距離複勝率','対象場走数','対象場複勝率',pace+'走数',pace+'複勝率','予定騎手','想定人気','妙味']:
            value=row.get(label,pd.NA)
            st.write(label+'：'+('未確認' if pd.isna(value) else str(value)))
        mine=data.loc[data['馬ID'].astype(str).eq(str(row['馬ID']))].copy()
        mine=mine.loc[pd.to_datetime(mine['日付'],errors='coerce').lt(pd.Timestamp(context['race_date']))]
        st.write('検証済みラップでの3着内実績：'+summarize_lap_results(mine,load_lap_full_progress(context['race_id'])))
    st.download_button('全頭参考順位CSV',ranking.to_csv(index=False).encode('utf-8-sig'),file_name='race_reference_ranking.csv',mime='text/csv',key='main_reference_download_v22')
