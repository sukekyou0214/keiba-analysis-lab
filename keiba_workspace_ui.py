def render_workspace(selected_section=None,embedded=False):
    """Mobile practical workspace; legacy app remains available."""
    import os
    import time
    import pandas as pd
    import requests
    import streamlit as st
    from keiba_v22 import (ELIZABETH,ENTRY_URL,Store,scope_key,validate_scope,historical_features,pattern_tables,rank_entries)
    from keiba_v22_collect import parse_entries,collect_remaining
    from keiba_affinity_store import pack_checkpoint,load_checkpoint,audit_checkpoint
    from keiba_affinity import to_csv_bytes


    st.markdown('<style>.block-container{padding-top:1rem} h1{font-size:1.65rem!important} div[data-testid="stMetricValue"]{font-size:1.3rem}</style>',unsafe_allow_html=True)
    if not embedded:st.title('重賞共通分析 Ver.22')

    from keiba_race_config import PRESETS, RaceConfig, VENUES
    if embedded:
        current_rid=str(st.session_state.get('auto_race_id',''))
        if current_rid and current_rid!=st.session_state.get('shared_last_main_rid'):
            from urllib.parse import parse_qs,urlparse
            st.session_state['common_race_choice']='その他（設定して利用）'
            st.session_state['custom_entry22']='https://race.netkeiba.com/race/shutuba.html?race_id='+current_rid
            for key in ['custom_name','custom_special','custom_venue','custom_surface','custom_distance','custom_day','custom_read_summary']:
                st.session_state.pop(key,None)
            for label,preset in PRESETS.items():
                if parse_qs(urlparse(preset.entry_url).query).get('race_id',[''])[0]==current_rid:
                    st.session_state['common_race_choice']=label
            st.session_state['shared_last_main_rid']=current_rid
    choice=st.selectbox('対象レース',list(PRESETS)+['その他（設定して利用）'],key='common_race_choice')
    if choice in PRESETS:
        config=PRESETS[choice]
    else:
        with st.expander('対象レースの設定',expanded=True):
            entry=st.text_input('出馬表URL（過去分析のみなら空欄可）',key='custom_entry22')
            if st.button('URLからレース設定を読み取る',disabled=not entry):
                try:
                    from keiba_race_metadata import fetch_metadata
                    from datetime import date
                    with st.spinner('出馬表の情報を確認中'):
                        detected=fetch_metadata(entry)
                    mapping={'name':'custom_name','special_url':'custom_special','venue':'custom_venue','surface':'custom_surface','distance':'custom_distance'}
                    defaults={'name':'','special_url':'','venue':'札幌','surface':'芝','distance':2000}
                    for field,key in mapping.items():
                        st.session_state[key]=detected.get(field,defaults[field])
                    st.session_state['custom_day']=date.fromisoformat(detected['race_date']) if 'race_date' in detected else None
                    labels={'name':'レース名','special_url':'重賞特集URL','race_date':'開催日','venue':'競馬場','surface':'コース','distance':'距離'}
                    known=[label for field,label in labels.items() if field in detected]
                    missing=[label for field,label in labels.items() if field not in detected]
                    st.session_state['custom_read_summary']=(entry,known,missing)
                    st.success('ページで確認できた項目を入力しました。')
                except Exception as error:st.warning(f'自動入力を停止しました：{error}')
            summary=st.session_state.get('custom_read_summary')
            if summary and summary[0]==entry:
                st.caption('ページで確認済み：'+'、'.join(summary[1]))
                if summary[2]:st.warning('手入力で確認が必要：'+'、'.join(summary[2]))
            race_name=st.text_input('正式レース名（成績ページとの照合用）',key='custom_name')
            special=st.text_input('重賞特集URL',key='custom_special')
            day=st.date_input('対象開催日',value=None,key='custom_day')
            venue_input=st.selectbox('対象競馬場',list(VENUES.values()),key='custom_venue')
            surface=st.selectbox('コース',['芝','ダ'],key='custom_surface')
            distance=st.number_input('対象距離m',min_value=1000,max_value=4000,value=2000,step=100,key='custom_distance')
            last=st.number_input('過去分析の最終年',min_value=2000,max_value=2099,value=2025)
            first=st.number_input('過去分析の開始年',min_value=2000,max_value=2099,value=2016)
        if day is None:
            st.info('対象開催日を入力してください')
            if embedded:return
            st.stop()
        try:
            config=RaceConfig(race_name,special,entry,str(day),venue_input,int(distance),surface,int(first),int(last))
        except ValueError as error:
            st.info(str(error))
            if embedded:return
            st.stop()
    if embedded:
        from keiba_main_conditions import checked_context
        from urllib.parse import parse_qs,urlparse
        configured_rid=parse_qs(urlparse(config.entry_url).query).get('race_id',[''])[0]
        if configured_rid==str(st.session_state.get('auto_race_id','')):
            st.session_state['main_target_context']=checked_context(configured_rid,{**vars(config),'source':'対象レースの設定'})
            st.session_state['main_history_config']=vars(config)
    st.caption(f'{config.name} · {config.race_date} · {config.venue}{config.surface}{config.distance}m · 予定情報は変更があります')
    store=Store(); scope=config.historical_scope
    current_scope=config.current_scope
    historical_key='v22-history-'+scope
    current_key='v22-current-'+current_scope
    if historical_key not in st.session_state:
        st.session_state[historical_key],_=store.load(scope)
    if current_key not in st.session_state:
        st.session_state[current_key],_=store.load(current_scope)
        # Read-only migration of the original Elizabeth snapshot; keep the source.
        if not st.session_state[current_key] and choice=='エリザベス女王杯':
            legacy,_=store.load('elizabeth-2026-entries')
            if legacy:
                st.session_state[current_key]=legacy
                store.save(current_scope,legacy)
    if embedded:
        from keiba_main_integration import bridge_main_dataset
        try:
            inherited=bridge_main_dataset(config,st.session_state)
            if inherited is not None:
                previous=st.session_state[current_key]
                # Preserve tentative jockey/popularity only for verified same IDs/source.
                old=previous.get('targets',pd.DataFrame())
                if not old.empty and '取得元' in old and old['取得元'].eq(config.entry_url).all():
                    for column in ['想定人気','予定騎手']:
                        if column in old:
                            inherited['targets'][column]=inherited['targets']['馬ID'].map(old.drop_duplicates('馬ID').set_index('馬ID')[column])
                st.session_state[current_key]=inherited
                st.caption(f'本体で読込済みの{len(inherited["targets"])}頭・{len(inherited["horse_raw"])}走を使用しています（再取得なし）。')
        except ValueError as error:
            st.warning(f'本体データの引継ぎを停止：{error}')
            return
    tables=st.session_state[historical_key]; current=st.session_state[current_key]
    if tables.get('targets',pd.DataFrame()).shape[0]:
        try:config.validate_targets(tables['targets'])
        except ValueError as error:
            st.error(f'保存データの対象不一致：{error}');st.stop()
    if current.get('targets',pd.DataFrame()).shape[0]:
        source=current['targets'].get('取得元',pd.Series(dtype=str))
        if source.empty or not source.eq(config.entry_url).all():
            st.error('予定馬データの取得元が対象と一致しません');st.stop()
    mode=selected_section or st.radio('画面',['総合診断','詳細分析','詳細設定'],horizontal=True)

    def save_historical():
        store.save(scope,tables);st.session_state[historical_key]=tables

    def fetch_entries():
        r=requests.get(config.entry_url,timeout=20,headers={'User-Agent':'KeibaAnalysisLab/22'})
        r.raise_for_status();r.encoding=r.apparent_encoding or r.encoding
        return parse_entries(r.text,source=config.entry_url,expected_name=config.name)

    if mode=='総合診断':
        st.caption('総合参考点は着順・コース実績の暫定比較。能力指数・勝率・期待値は未検証です。')
        entries=current.get('targets',pd.DataFrame())
        if entries.empty:
            st.info('予定馬を未取得です。詳細設定の「予定馬・戦績を取得」を実行してください。')
        else:
            st.caption(f"予定馬 {len(entries)}頭 / 人気・騎手の空欄は未確認")
            pace=st.select_slider('想定ペース',options=['S','M','H'],value='M')
            ranking=rank_entries(entries,current.get('horse_raw',pd.DataFrame()),pace=pace,config=config)
            cols=[c for c in ['参考順位','馬名','予定騎手','想定人気','総合参考点','判定','妙味'] if c in ranking]
            st.dataframe(ranking[cols],hide_index=True,use_container_width=True)
            candidates=ranking.loc[ranking['妙味'].str.startswith('中穴候補',na=False)]
            st.write('中穴候補：'+('、'.join(candidates['馬名']) if len(candidates) else '保留（想定人気・評価根拠が揃っていません）'))
            name=st.selectbox('馬ごとの根拠',ranking['馬名'])
            st.dataframe(ranking.loc[ranking['馬名'].eq(name)].T.rename(columns={ranking.index[ranking['馬名'].eq(name)][0]:'値'}),use_container_width=True)
            # Show pre-race historical evidence without silently turning it into EV.
            with st.expander(f'{config.venue}開催の過去傾向との照合'):
                try:
                    from keiba_affinity_pipeline import complete_horse_manifest
                    from keiba_v22 import match_historical_patterns
                    historical_target=tables.get('targets',pd.DataFrame())
                    historical_raw=tables.get('horse_raw',pd.DataFrame())
                    target_manifest=tables.get('target_log',pd.DataFrame())
                    if (target_manifest.empty or len(target_manifest)!=len(config.years)
                            or not target_manifest['status'].eq('成功').all()
                            or not complete_horse_manifest(historical_target,tables.get('horse_log',pd.DataFrame()),historical_raw)):
                        raise ValueError('過去10年・全頭の取得完了後に照合できます')
                    past=historical_features(historical_target,historical_raw,config)
                    if set(pd.to_datetime(past['日付']).dt.year)!=config.years:
                        raise ValueError('過去10年の年度が揃っていません')
                    matched=match_historical_patterns(ranking.loc[ranking['馬名'].eq(name)].iloc[0],pattern_tables(past),venue=config.venue)
                    if matched.empty:
                        st.info(f'前走・脚質で一致する10例以上の{config.venue}開催データはありません')
                    else:
                        st.dataframe(matched,hide_index=True,use_container_width=True,column_config={'過去3着内率':st.column_config.NumberColumn(format='%.1f%%')})
                        st.caption(f'同じ馬や条件が重複するため、率を勝率に換算しません。{config.venue}開催のみを使用します。')
                except ValueError as error:
                    st.info(str(error))
            st.download_button('全頭比較CSV',to_csv_bytes(ranking),'race_ranking.csv')
        if embedded and not entries.empty:
            if st.button('取得済みデータを本体の総合診断へ反映',key='bridge_main_data'):
                from keiba_main_integration import prepare_main_bridge
                try:
                    st.session_state['pending_main_bridge']=prepare_main_bridge(config,current)
                    st.rerun()
                except ValueError as error:st.error(str(error))
        elif not embedded:
            st.markdown('[既存の総合分析を開く](/)')
    elif mode=='詳細分析':
        st.caption(f'{config.first_year}〜{config.last_year}年。実際の開催場ごとに分けて集計します。')
        target=tables.get('targets',pd.DataFrame());raw=tables.get('horse_raw',pd.DataFrame())
        if target.empty or raw.empty:st.info('過去データが未取得です。詳細設定から一括取得できます。')
        else:
            try:
                from keiba_affinity_pipeline import complete_horse_manifest
                if not tables.get('target_log',pd.DataFrame()).get('status',pd.Series(dtype=str)).eq('成功').all() or not complete_horse_manifest(target,tables.get('horse_log',pd.DataFrame()),raw):
                    raise ValueError('全年度・全頭の取得が未完了です。途中のデータで好走率を表示しません。')
                years=set(pd.to_datetime(target['日付'],errors='coerce').dt.year.dropna().astype(int))
                if years != config.years or target['レースID'].nunique()!=len(config.years):
                    raise ValueError('指定した年度のレースが揃っていません。')
                features=historical_features(target,raw,config); patterns=pattern_tables(features)
                venue=st.selectbox('対象の開催場',sorted(features['開催区分'].unique()))
                minimum=st.slider('最低サンプル数',1,30,10)
                view=patterns.loc[patterns['開催'].eq(venue)&patterns['出走数'].ge(minimum)]
                st.dataframe(view,hide_index=True,use_container_width=True,column_config={c:st.column_config.NumberColumn(c,format='%.1f%%') for c in ['3着内率','単勝回収率','複勝回収率']})
                st.caption('回収率は過去実績。将来の期待値ではありません。複勝払戻未取得は欠損です。脚質は前走4角位置の頭数比による分類です。')
                with st.expander('前走・距離・競馬場・S/M/Hの全項目'):
                    st.dataframe(features,hide_index=True,use_container_width=True)
                st.download_button('過去10年比較CSV',to_csv_bytes(features),'race_historical_features.csv')
                with st.expander('研究：過去レース経験と好走率'):
                    if st.button('相性候補を計算'):
                        from keiba_affinity_workflow import analyze_verified_bundle
                        from keiba_v22 import history_frame
                        from keiba_v22 import venue_bundle
                        scoped=venue_bundle(tables,venue,config)
                        with st.spinner(f'{venue}開催の相性を計算中'):
                            scoped['affinity'],_=analyze_verified_bundle(scoped['targets'],scoped['target_log'],history_frame(scoped['horse_raw']),scoped['horse_log'])
                        store.save(scope+'-affinity-'+venue,scoped)
                    scoped_result,_=store.load(scope+'-affinity-'+venue)
                    a=scoped_result.get('affinity',pd.DataFrame())
                    st.caption(f'研究対象も{venue}開催のみ。別の開催場の計算結果は使用しません。')
                    if not a.empty:
                        reliable=st.checkbox('暫定判定のみ',value=True)
                        a=a.loc[a['n'].ge(minimum)]
                        if reliable:a=a.loc[a['confidence'].eq('暫定（要外部検証）')]
                        st.dataframe(a,hide_index=True,use_container_width=True)
            except ValueError as e:st.warning(str(e))
    else:
        st.caption('一度の操作で未取得分を順番に取得。失敗時は停止し、保存済み地点から再開します。')
        target=tables.get('targets',pd.DataFrame());log=tables.get('horse_log',pd.DataFrame())
        done=int(log['status'].eq('成功').sum()) if not log.empty else 0
        total=target['馬ID'].nunique() if not target.empty else 0
        st.write(f'過去レース {target.get("レースID",pd.Series(dtype=str)).nunique()}/{len(config.years)}回 · 戦績 {done}/{total}頭')
        progress=st.progress(done/total if total else 0); status=st.empty()
        def report(n,total,elapsed):
            progress.progress(min(n/total,1));status.caption(f'{n}/{total}頭 · {elapsed:.0f}秒（取得済み分を保存中）')
        if st.button('過去10年を一括取得・再開',type='primary',use_container_width=True):
            started=time.monotonic()
            try:
                from keiba_affinity_collect import fetch_target_history
                from keiba_core import get_results
                targetlog=tables.get('target_log',pd.DataFrame())
                if target.empty or targetlog.empty or not targetlog['status'].eq('成功').all():
                    with st.spinner('10年分の結果を照合中'):
                        target,targetlog=fetch_target_history(config.special_url,config.first_year,config.last_year,delay_seconds=2)
                    config.validate_targets(target)
                    tables.update(targets=target,target_log=targetlog);save_historical()
                if not targetlog['status'].eq('成功').all():raise ValueError('対象レースに取得失敗があります。診断は停止しています。')
                tables=collect_remaining(target,get_results,tables,store,scope,report)
                save_historical();st.success(f'取得完了：{time.monotonic()-started:.0f}秒')
            except Exception as e:
                tables,_=store.load(scope);st.session_state[historical_key]=tables
                st.error(f'取得停止：{e}')
        if st.button('予定馬・戦績を取得',use_container_width=True,disabled=not config.entry_url):
            try:
                from keiba_core import get_results
                with st.spinner('予定馬を確認中'):
                    entries=fetch_entries()
                # Refresh entries without treating withdrawn horses as current entrants.
                current['targets']=entries
                store.save(current_scope,current)
                current=collect_remaining(entries,get_results,current,store,current_scope,report)
                st.session_state[current_key]=current;st.success('予定馬・戦績を保存しました')
            except Exception as e:
                current,_=store.load(current_scope);st.session_state[current_key]=current
                st.error(f'取得停止：{e}')
        st.caption('取得間隔2秒、並列取得・自動リトライなし。通信制限時は停止します。取得元の利用条件の範囲で利用してください。')
        st.link_button('取得元の利用規約','https://netkeiba.com/info/kiyaku.html')
        if not os.environ.get('KEIBA_V22_STORE'):
            st.info('同じサーバーでは保存データを再利用できます。Streamlitの再起動・再配置をまたぐ保存は、永続ディスク未設定のためZIPバックアップが必要です。')
        if tables:
            st.download_button('過去データのバックアップZIP',pack_checkpoint(tables),'race_v22_checkpoint.zip')
        upload=st.file_uploader('選択レースの保存ZIPを復元',type=['zip'])
        if upload is not None and st.button('検査して復元'):
            try:
                restored=load_checkpoint(upload.getvalue())
                config.validate_targets(restored.get('targets',pd.DataFrame()))
                issues=audit_checkpoint(restored)
                if issues:raise ValueError(' / '.join(issues))
                tables=restored;save_historical();st.success('対象レースを検証して復元しました')
            except Exception as e:st.error(f'復元を拒否しました：{e}')
        with st.expander('取得状況・欠損・研究ページ'):
            for key in ['target_log','horse_log']:
                if key in tables:st.dataframe(tables[key],hide_index=True,use_container_width=True)
            st.caption('研究機能は「管理・検証」の「研究用ページ」から利用できます。')
