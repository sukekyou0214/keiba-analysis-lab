"""Reuse verified ten-year research as descriptive evidence in the main app."""
from urllib.parse import urlparse,parse_qs
import pandas as pd
from keiba_race_config import PRESETS,RaceConfig


def history_config(context,state):
    if context is None:return None
    def same_conditions(config):
        return all(getattr(config,key)==context.get(key) for key in ['race_date','venue','surface','distance'])
    saved=state.get('main_history_config')
    if isinstance(saved,dict) and parse_qs(urlparse(saved.get('entry_url','')).query).get('race_id',[''])[0]==context['race_id']:
        try:
            config=RaceConfig(**saved)
            return config if same_conditions(config) else None
        except (TypeError,ValueError):return None
    for config in PRESETS.values():
        if parse_qs(urlparse(config.entry_url).query).get('race_id',[''])[0]==context['race_id']:
            return config if same_conditions(config) else None
    return None


def verified_patterns(config,tables):
    from keiba_v22 import historical_features,pattern_tables
    from keiba_affinity_pipeline import complete_horse_manifest
    target=tables.get('targets',pd.DataFrame())
    raw=tables.get('horse_raw',pd.DataFrame())
    logs=tables.get('target_log',pd.DataFrame())
    horse_logs=tables.get('horse_log',pd.DataFrame())
    config.validate_targets(target)
    if not {'馬ID','着順_数値','人気'}.issubset(target) or target.duplicated(['レースID','馬ID']).any():
        raise ValueError('対象レースの出走記録に不足または重複があります')
    if (not {'race_id','status'}.issubset(logs) or len(logs)!=len(config.years)
            or logs['race_id'].astype(str).duplicated().any() or not logs['status'].eq('成功').all()
            or set(logs['race_id'].astype(str))!=set(target['レースID'].astype(str))
            or not complete_horse_manifest(target,horse_logs,raw)):
        raise ValueError('過去10年・全頭の取得完了後に照合できます')
    features=historical_features(target,raw,config)
    if set(pd.to_datetime(features['日付']).dt.year)!=config.years:
        raise ValueError('過去10年の年度が揃っていません')
    selected=features.loc[features['開催区分'].eq(config.venue)]
    coverage={'開催':config.venue,'全出走記録':len(features),'照合出走記録':len(selected),
              '照合開催数':selected['レースID'].nunique()}
    return pattern_tables(features),coverage


def current_pattern_features(data,horse_id,config):
    from keiba_v22 import history_frame,pre_features
    if data.empty or '馬ID' not in data:return {}
    mine=history_frame(data.loc[data['馬ID'].astype(str).eq(str(horse_id))])
    if mine.empty:return {}
    if mine['日付'].isna().any():raise ValueError('日付不明の戦績があるため前走照合を保留します')
    # Use the actual previous race, including lower classes, regardless of ranking filters.
    return pre_features(mine,config.race_date,config)


def render_pattern_match(context,data,horse_id,state):
    import streamlit as st
    from keiba_v22 import Store,match_historical_patterns
    config=history_config(context,state)
    if config is None:
        st.caption('過去10年との照合：対象レースの過去分析設定が未確認です。')
        return
    stored,_=Store().load(config.historical_scope)
    tables={k:v for k,v in stored.items() if k in ['targets','horse_raw','target_log','horse_log']}
    st.markdown('**過去10年の傾向との照合**')
    try:
        @st.cache_data(ttl=3600,show_spinner=False)
        def cached_patterns(config,tables):
            return verified_patterns(config,tables)
        patterns,coverage=cached_patterns(config,tables)
        features=current_pattern_features(data,horse_id,config)
        matched=match_historical_patterns(features,patterns,minimum=10,venue=config.venue)
        st.caption(f"{config.first_year}〜{config.last_year}年の全{coverage['全出走記録']}出走記録から、{config.venue}{coverage['照合開催数']}開催・{coverage['照合出走記録']}出走記録を照合対象に使用。")
        if matched.empty:
            st.info('前走・脚質で一致する、結果確認済み10例以上の条件はありません。')
        else:
            st.dataframe(matched[['条件','標本数','3着内数','過去3着内率']],hide_index=True,width='stretch',
                         column_config={'過去3着内率':st.column_config.NumberColumn(format='%.1f%%')})
        st.caption('標本数は結果確認済みの件数です。欠損は率の分母から除外します。')
        st.caption('脚質は前走4角の順位を頭数で補正した便宜分類です。')
        st.caption('同じ馬・条件の重複を含む探索的集計です。条件ごとの率を足し合わせず、今回の勝率や期待値には換算しません。')
    except ValueError as exc:
        st.info('過去傾向の照合は保留：'+str(exc))
