"""Frozen reference-score replay: strict prior dates, no actual-pace oracle."""
from types import SimpleNamespace
import pandas as pd
from keiba_race_config import VENUES

VERSION='graded-min3-coverage60-v1'
METHODS=['参考点','着順のみ','確定人気（比較用）','着順のみ（共通対象）','確定人気（共通対象）']


def selection_metrics(scores, finishes, field_size):
    """Include all tied ranks; never break ties with result-table row order."""
    scores=pd.to_numeric(scores,errors='coerce')
    finishes=pd.to_numeric(finishes,errors='coerce')
    valid=finishes.ge(1)&finishes.le(field_size)&finishes.mod(1).eq(0)
    ranks=scores.rank(method='min',ascending=False)
    first=ranks.eq(1);three=ranks.le(3)
    winner=valid&finishes.eq(1)
    return {'順位提示数':int(scores.notna().sum()),'出走数':field_size,
            '1位群選出数':int(first.sum()),'1位群検証可':int(first.any() and winner.any()),
            '1位群に勝ち馬':int((first&winner).any()),
            '3位以内群選出数':int(three.sum()),'3位以内群結果判明数':int((three&valid).sum()),
            '3位以内群の3着内数':int((three&valid&finishes.le(3)).sum())}


def replay_reference(config,tables,paces=('S','M','H'),progress=lambda *args:None):
    from keiba_main_patterns import verified_patterns
    from keiba_v22 import history_frame,rank_entries
    if not paces or len(set(paces))!=len(paces) or any(p not in {'S','M','H'} for p in paces):
        raise ValueError('想定ペースは重複のないS/M/Hを指定してください')
    # Use the same completeness checks as the main evidence; don't fetch anything.
    verified_patterns(config,tables)
    target=tables['targets'].copy()
    history=history_frame(tables['horse_raw'])
    dates=pd.to_datetime(target['日付'],errors='coerce')
    if dates.isna().any():raise ValueError('対象レースの日付が不明です')
    target['日付']=dates
    records=[];metrics=[];done=0
    total=target['レースID'].nunique()*len(paces)
    for rid,entrants in target.groupby('レースID',sort=True):
        rid=str(rid);cutoffs=entrants['日付'].drop_duplicates()
        if len(cutoffs)!=1:raise ValueError('同一レースの開催日が一致しません')
        cutoff=cutoffs.iloc[0]
        # Course settings are declared race facts, not target finish or actual pace.
        race_rows=history.loc[history['レースID'].astype(str).eq(rid)]
        conditions=race_rows[['競馬場','コース','距離_m']].dropna().drop_duplicates()
        if len(conditions)!=1:raise ValueError('過去レースの開催条件を一意に確認できません：'+rid)
        if race_rows['日付'].isna().any() or not race_rows['日付'].eq(cutoff).all():
            raise ValueError('対象結果と戦績の開催日が一致しません：'+rid)
        venue,surface,distance=conditions.iloc[0]
        if venue!=VENUES.get(rid[4:6]) or surface not in {'芝','ダ'} or not 1000<=distance<=4000 or float(distance)%1:
            raise ValueError('過去レースの開催条件とIDが一致しません：'+rid)
        setting=SimpleNamespace(race_date=cutoff.date().isoformat(),venue=venue,surface=surface,distance=int(distance))
        ids=set(entrants['馬ID'].astype(str))
        prior=history.loc[history['馬ID'].astype(str).isin(ids)&history['日付'].lt(cutoff)&history['レースID'].astype(str).ne(rid)]
        prior=prior.loc[prior['レース格'].isin(['G1','G2','G3'])].copy()
        entries=entrants[['馬ID','馬名']].copy()  # no final popularity/finish as features
        actual=entrants[['馬ID','着順_数値','人気']].copy().rename(columns={'着順_数値':'実着順','人気':'確定人気'})
        actual['馬ID']=actual['馬ID'].astype(str)
        for pace in paces:
            ranked=rank_entries(entries,prior,config=setting,pace=pace)
            ranked['馬ID']=ranked['馬ID'].astype(str)
            joined=ranked.merge(actual,on='馬ID',how='left',validate='one_to_one')
            recent=pd.to_numeric(joined.get('近5走有効数',pd.Series(index=joined.index,dtype=float)),errors='coerce')
            baseline=pd.to_numeric(joined.get('近5走着順参考点',pd.Series(index=joined.index,dtype=float)),errors='coerce').where(recent.ge(3))
            popularity=pd.to_numeric(joined['確定人気'],errors='coerce')
            popularity=popularity.where(popularity.between(1,len(entrants))&popularity.mod(1).eq(0))
            eligible=joined['総合参考点'].notna()
            scores={'参考点':joined['総合参考点'],'着順のみ':baseline,'確定人気（比較用）':-popularity,
                    '着順のみ（共通対象）':baseline.where(eligible),'確定人気（共通対象）':(-popularity).where(eligible)}
            common={'レースID':rid,'年':cutoff.year,'開催':venue,'想定ペース':pace,'検証方式':VERSION,
                    '過去ペース判定数':int(prior['ペース区分'].notna().sum())}
            for method,values in scores.items():
                metrics.append({**common,'方法':method,**selection_metrics(values,joined['実着順'],len(entrants))})
            for _,row in joined.iterrows():
                records.append({**common,'馬ID':row['馬ID'],'馬名':row['馬名'],'参考順位':row['参考順位'],
                    '参考点':row['総合参考点'],'項目充足率':row.get('評価項目充足率'),
                    '近5走有効数':row.get('近5走有効数'),'実着順':row['実着順'],'確定人気':row['確定人気']})
            done+=1;progress(done,total)
    race_metrics=pd.DataFrame(metrics)
    summary=[]
    for (venue,pace,method),group in race_metrics.groupby(['開催','想定ペース','方法'],sort=False):
        counts={c:int(group[c].sum()) for c in ['順位提示数','出走数','1位群選出数','1位群検証可','1位群に勝ち馬',
                                               '3位以内群選出数','3位以内群結果判明数','3位以内群の3着内数']}
        known=counts['3位以内群結果判明数']
        summary.append({'開催':venue,'想定ペース':pace,'方法':method,'検証レース数':group['レースID'].nunique(),**counts,
            '順位提示率':round(100*counts['順位提示数']/counts['出走数'],1),
            '選出馬3着内率':round(100*counts['3位以内群の3着内数']/known,1) if known else float('nan')})
    return pd.DataFrame(summary),race_metrics,pd.DataFrame(records)


def render_reference_replay(context,state):
    import streamlit as st
    from keiba_main_patterns import history_config
    from keiba_v22 import Store
    with st.expander('参考順位の過去データ検証（研究用）',expanded=False):
        config=history_config(context,state)
        if config is None:
            st.info('対象レースの過去分析設定を確認すると検証できます。');return
        st.caption('保存済みの過去10年を、各開催日より前の重賞戦績だけで再計算します。想定S/M/Hを別々に固定し、実際の対象レースのペース・結果・人気は予測に使いません。')
        store=Store();scope='reference-replay-'+VERSION+'-'+config.current_scope
        saved,updated=store.load(scope)
        if st.button('保存済みの過去10年で検証する（通信なし）',key='reference_replay_run_v22'):
            tables,_=store.load(config.historical_scope)
            try:
                from time import perf_counter
                start=perf_counter();bar=st.progress(0)
                summary,races,rows=replay_reference(config,tables,progress=lambda n,total:bar.progress(n/total))
                saved={'target_log':summary,'targets':races,'horse_raw':rows}
                store.save(scope,saved)
                updated=pd.Timestamp.now(tz='Asia/Tokyo').timestamp()
                st.success(f"{races['レースID'].nunique()}レースの回顧検証を保存しました（{perf_counter()-start:.0f}秒）。")
            except ValueError as exc:st.warning('検証を停止しました：'+str(exc))
        summary=saved.get('target_log',pd.DataFrame())
        if summary.empty:return
        if updated:
            stamp=pd.Timestamp(updated,unit='s',tz='UTC').tz_convert('Asia/Tokyo').strftime('%Y/%m/%d %H:%M')
            st.caption('保存済み検証結果：'+stamp+' · 実行時の保存データ · '+VERSION)
        cols=['開催','想定ペース','方法','検証レース数','順位提示率','3位以内群選出数','3位以内群結果判明数','3位以内群の3着内数','選出馬3着内率']
        show_pace=st.radio('表示する想定ペース',['S','M','H'],index=1,horizontal=True,key='reference_replay_show_pace_v22')
        st.dataframe(summary.loc[summary['想定ペース'].eq(show_pace),cols],hide_index=True,width='stretch',
                     column_config={col:st.column_config.NumberColumn(format='%.1f%%') for col in ['順位提示率','選出馬3着内率']})
        st.caption('共通対象は参考点で順位を提示できた馬に揃えた比較です。対象馬数が異なる全体の比較とは分けて確認します。')
        st.caption('同点は全馬を選出するため「上位3位群」が3頭を超える場合があります。率は結果確認済みの選出馬を分母にします。順位提示率の低い比較は、出走全馬の評価ではありません。')
        st.caption('確定人気は発走直前の比較基準です。前もって取得した想定人気とは異なります。重みの調整・シナリオの事後選択はせず、京都・阪神を分けて表示します。少数レースの回顧で、将来精度の保証ではありません。')
        races=saved['targets']
        if races['過去ペース判定数'].eq(0).all():
            st.info('過去10年の検証用データでは、対象日前の明示S/M/Hが0件です。出走馬用に補完したデータとは別の集計で、ペース別の差は検証できません。')
        st.download_button('検証集計CSV',summary.to_csv(index=False).encode('utf-8-sig'),file_name='reference_replay_summary.csv',key='reference_replay_summary_v22')
        st.download_button('レース別検証CSV',races.to_csv(index=False).encode('utf-8-sig'),file_name='reference_replay_races.csv',key='reference_replay_races_v22')
