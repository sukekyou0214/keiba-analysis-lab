"""Present the existing common reference ranking in the original diagnosis."""
from types import SimpleNamespace
import pandas as pd


def main_ranking(data, horses, context, metadata=None, pace='M', graded_only=True, limited=None):
    """limited: horse IDs whose history fetch was stopped by the source's refusals."""
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
    selected=data
    if graded_only:
        grades=data.get('レース格',pd.Series('',index=data.index))
        selected=data.loc[grades.isin(['G1','G2','G3'])].copy()
    result=rank_entries(pd.DataFrame(rows),selected,config=SimpleNamespace(**context),pace=pace)
    if result.empty:
        return result
    known=set(data['馬ID'].dropna().astype(str).str.replace(r'\.0$','',regex=True)) if '馬ID' in data else set()
    ids=result['馬ID'].astype(str)
    no_history=~ids.isin(known)
    if graded_only:
        result.loc[result['判定'].eq('戦績未取得') & ~no_history,'判定']='対象の重賞戦績なし・保留'
    limited={str(x) for x in (limited or [])}
    result.loc[no_history,'判定']='戦績未取得（取得待ち）'
    result.loc[no_history & ids.isin(limited),'判定']='戦績未取得（取得先の制限）'
    result['予定騎手'] = result['予定騎手'].replace(r'^\s*(?:[○〇◯*－—-]+|未定)?\s*$', pd.NA, regex=True)
    held = result['参考順位'].isna() & result['判定'].eq('順位保留・根拠不足')
    if held.any():
        short = result['近5走有効数'].lt(3)
        sparse = result['評価項目充足率'].lt(60)
        result.loc[held & short & ~sparse, '判定'] = '根拠不足（有効成績3走未満）'
        result.loc[held & ~short & sparse, '判定'] = '根拠不足（充足率60%未満）'
        result.loc[held & short & sparse, '判定'] = '根拠不足（有効走数・充足率）'
    result['比較対象']='G1〜G3' if graded_only else '全クラス'
    return result


def fetch_current_metadata(context):
    from keiba_fetch_policy import policy_get
    from keiba_v22_collect import parse_entries
    url='https://race.netkeiba.com/race/shutuba.html?race_id='+context['race_id']
    response=policy_get(url,timeout=20,allow_redirects=False,headers={'User-Agent':'KeibaAnalysisLab/22'})
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
    with st.expander('予定情報の更新・比較条件',expanded=False):
        if st.button('予定騎手・想定人気を更新（戦績の再取得なし）',key='main_update_entry_metadata'):
            try:
                fresh=fetch_current_metadata(context)
                store.save(scope,{'targets':fresh})
                metadata=fresh
                st.success('出走表の予定情報を保存しました。確認できない値は欠損のままです。')
            except Exception as exc:
                st.warning('予定情報の更新を停止しました。保存済み情報を使用します：'+str(exc)[:180])
        if not metadata.empty:
            dates=metadata.get('確認日時',pd.Series(dtype=str)).dropna()
            if len(dates):
                checked=pd.to_datetime(dates.iloc[0],errors='coerce')
                st.caption('予定情報の確認日時：'+(checked.strftime('%Y/%m/%d %H:%M') if pd.notna(checked) else '未確認'))
            if set(metadata['馬ID'].astype(str)) != {str(x) for x in horses}:
                st.info('出走表と本体の対象馬が異なります。参考順位は本体で読込済みの馬が対象です。')
        # 出走表で条件戦と確認できた時だけ全クラス比較を既定にする。不明時は従来どおりG1〜G3。
        graded_default=context.get('grade')!='条件戦'
        graded_only=st.checkbox('重賞実績で比較する（G1〜G3）',value=graded_default,
                                key='main_reference_graded_v22_'+context['race_id'])
        st.caption('比較対象：'+('G1〜G3の取得済み実績。クラス間の混在を抑えますが、G1とG3の難易度差は未補正です。' if graded_only else '全クラスの取得済み実績。未勝利・条件戦の好成績も含まれるため、重賞向けの能力順位には使えません。'))
    st.caption(('G1〜G3' if graded_only else '全クラス')+'の実績参考順位（研究中）。過去検証で有効性は確認できていません。')
    pace=st.radio('参考順位に使う想定ペース',['S','M','H'],index=1,horizontal=True,key='main_reference_pace_v22')
    try:
        limited=set(st.session_state.get('fetch_unfetched_v38',{}).get(context['race_id'],{}))
        from keiba_fetch_policy import POLICY
        if POLICY.cooldown_until('db.netkeiba.com'):
            # 戦績ページの取得先が停止中の間は、未取得の馬すべてが制限による未取得。
            limited|={str(h) for h in horses}
        ranking=main_ranking(data,horses,context,metadata,pace,graded_only,limited)
    except ValueError as exc:
        st.warning('参考順位を保留しました：'+str(exc))
        return
    if ranking.empty:
        st.info('参考順位の対象馬がありません。')
        return
    ranked_count = ranking['参考順位'].notna().sum()
    st.caption(f'{pace}想定 · 順位表示 {ranked_count}/{len(ranking)}頭 · 保留 {len(ranking) - ranked_count}頭')
    missing=ranking.loc[ranking['判定'].astype(str).str.startswith('戦績未取得（')]
    if len(missing):
        st.warning(f'戦績未取得の{len(missing)}頭（'+'、'.join(missing['馬名'].astype(str))+'）は順位計算から外して保留にしています。'
                   '取得後に順位が入れ替わる可能性があります。')
    shown = display_horse_column(ranking, horses, numbers)
    course_runs = pd.to_numeric(ranking.get('対象場距離走数', pd.Series(index=ranking.index, dtype=float)), errors='coerce')
    shown['同場同距離の記録'] = course_runs.map(
        lambda runs: '未確認' if pd.isna(runs) else f'{int(runs)}走' if runs else '該当記録なし'
    ).to_numpy()
    renderer(shown,['馬名','参考順位','総合参考点','同場同距離の記録','評価項目充足率','予定騎手','想定人気','判定'],
             {'馬名':'馬番＋正式馬名','参考順位':'実績参考順位','総合参考点':'参考点','評価項目充足率':'項目充足率(%)','予定騎手':'予定騎手','想定人気':'想定人気','判定':'根拠の状態'})
    st.caption(f'同場同距離の記録は、対象日前の{context["venue"]}{context["surface"]}{context["distance"]}mを選択中のクラスで集計。')
    jockey_count = ranking['予定騎手'].fillna('').astype(str).str.strip().ne('').sum()
    popularity_count = pd.to_numeric(ranking['想定人気'], errors='coerce').notna().sum()
    st.caption(f'出走表の予定情報 · 予定騎手 {jockey_count}/{len(ranking)}頭 · 想定人気 {popularity_count}/{len(ranking)}頭')
    st.caption('着順の頭数補正40%、同場同距離20%、同距離15%、同場10%、想定ペース別15%で仮集計。欠損項目を除いて重みを再配分します。近5走の有効成績3走以上・項目充足率60%以上で順位を付けます。')
    st.caption('参考点は実績の比較用です。レース格・相手関係・時計の補正を含まず、総合能力・勝率・期待値の評価は未検証です。')
    candidates=ranking.loc[ranking['妙味'].str.startswith('中穴候補',na=False)]
    st.write('中穴の参考候補：'+('、'.join(candidates['馬名']) if len(candidates) else '保留（条件を満たす馬、または想定人気の確認が不足）'))
    st.caption('想定4〜9番人気・参考順位5位以内・項目充足率80%以上・近5走すべて有効の馬を抽出。期待値が高いと検証したものではありません。')
    with st.expander('参考順位の根拠を見る',expanded=False):
        name=st.selectbox('根拠を確認する馬',ranking['馬名'].tolist(),key='main_reference_horse_v22')
        row=ranking.loc[ranking['馬名'].eq(name)].iloc[0]
        if context['surface'] == '芝':
            distance_evidence = []
            for distance in (1800, 2000, 2200, 2400):
                runs = row.get(f'{distance}m走数', pd.NA)
                rate = row.get(f'{distance}m複勝率', pd.NA)
                if pd.isna(runs) or runs == 0:
                    value = '該当記録なし'
                else:
                    value = f'{int(runs)}走 / 3着内率 ' + ('不明' if pd.isna(rate) else f'{rate:.0f}%')
                distance_evidence.append(f'{distance}m {value}')
            st.caption('芝の距離別実績（対象日前・選択中のクラス）')
            st.write(' · '.join(distance_evidence))
        for label in ['近5走有効数','近5走着順参考点','対象場距離走数','対象場距離複勝率','対象距離走数','対象距離複勝率','対象場走数','対象場複勝率',pace+'走数',pace+'複勝率','予定騎手','想定人気','妙味']:
            value=row.get(label,pd.NA)
            st.write(label+'：'+('未確認' if pd.isna(value) else str(value)))
        mine=data.loc[data['馬ID'].astype(str).eq(str(row['馬ID']))].copy()
        if graded_only:
            mine=mine.loc[mine.get('レース格',pd.Series('',index=mine.index)).isin(['G1','G2','G3'])]
        mine=mine.loc[pd.to_datetime(mine['日付'],errors='coerce').lt(pd.Timestamp(context['race_date']))]
        st.write('検証済みラップでの3着内実績：'+summarize_lap_results(mine,load_lap_full_progress(context['race_id'])))
        from keiba_main_patterns import render_pattern_match
        render_pattern_match(context,data,row['馬ID'],st.session_state)
    st.download_button('全頭参考順位CSV',ranking.to_csv(index=False).encode('utf-8-sig'),file_name='race_reference_ranking.csv',mime='text/csv',key='main_reference_download_v22')
