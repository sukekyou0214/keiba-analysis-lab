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

st.set_page_config(page_title='エリザベス女王杯 Ver.22',page_icon='🏇',layout='centered')
st.markdown('<style>.block-container{padding-top:1rem} h1{font-size:1.65rem!important} div[data-testid="stMetricValue"]{font-size:1.3rem}</style>',unsafe_allow_html=True)
st.title('エリザベス女王杯 Ver.22')
st.caption('2026年11月15日 · 京都芝2200m · 出走予定・騎手想定は変更があります')
# A public Streamlit deployment must not expose shared provider data.
# Enable only behind an owner-controlled password; no default or committed secret.
import hmac
try:
    access_password = st.secrets.get('V22_ACCESS_PASSWORD', '')
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    access_password = ''
if not access_password:
    st.info('本人用画面の準備が必要です。Streamlit Secretsに V22_ACCESS_PASSWORD を設定すると取得・分析を利用できます。')
    st.caption('データは公開リポジトリへ保存しません。利用条件に沿った本人用の分析画面です。')
    st.stop()
if not st.session_state.get('v22_authenticated', False):
    entered = st.text_input('本人用パスワード', type='password')
    if st.button('分析画面を開く'):
        if hmac.compare_digest(entered.encode(), str(access_password).encode()):
            st.session_state['v22_authenticated'] = True
            st.rerun()
        else:
            st.error('パスワードを確認してください')
    st.stop()
store=Store(); scope=scope_key(ELIZABETH)
# Each race and cohort has its own persisted checkpoint and session key.
if 'v22_historical' not in st.session_state:
    st.session_state['v22_historical'],_=store.load(scope)
if 'v22_current' not in st.session_state:
    st.session_state['v22_current'],_=store.load('elizabeth-2026-entries')
tables=st.session_state['v22_historical']; current=st.session_state['v22_current']
mode=st.radio('画面',['総合診断','詳細分析','詳細設定'],horizontal=True)

def save_historical():
    store.save(scope,tables);st.session_state['v22_historical']=tables

def fetch_entries():
    r=requests.get(ENTRY_URL,timeout=20,headers={'User-Agent':'KeibaAnalysisLab/22'})
    r.raise_for_status();r.encoding=r.apparent_encoding or r.encoding
    return parse_entries(r.text)

if mode=='総合診断':
    st.caption('総合参考点は着順・コース実績の暫定比較。能力指数・勝率・期待値は未検証です。')
    entries=current.get('targets',pd.DataFrame())
    if entries.empty:
        st.info('予定馬を未取得です。詳細設定の「2026年の予定馬・戦績を取得」を実行してください。')
    else:
        st.caption(f"予定馬 {len(entries)}頭 / 人気・騎手の空欄は未確認")
        pace=st.select_slider('想定ペース',options=['S','M','H'],value='M')
        ranking=rank_entries(entries,current.get('horse_raw',pd.DataFrame()),pace=pace)
        cols=[c for c in ['参考順位','馬名','予定騎手','想定人気','総合参考点','判定','妙味'] if c in ranking]
        st.dataframe(ranking[cols],hide_index=True,use_container_width=True)
        candidates=ranking.loc[ranking['妙味'].str.startswith('中穴候補',na=False)]
        st.write('中穴候補：'+('、'.join(candidates['馬名']) if len(candidates) else '保留（想定人気・評価根拠が揃っていません）'))
        name=st.selectbox('馬ごとの根拠',ranking['馬名'])
        st.dataframe(ranking.loc[ranking['馬名'].eq(name)].T.rename(columns={ranking.index[ranking['馬名'].eq(name)][0]:'値'}),use_container_width=True)
        # Show pre-race historical evidence without silently turning it into EV.
        with st.expander('京都開催の過去傾向との照合'):
            try:
                from keiba_affinity_pipeline import complete_horse_manifest
                from keiba_v22 import match_historical_patterns
                historical_target=tables.get('targets',pd.DataFrame())
                historical_raw=tables.get('horse_raw',pd.DataFrame())
                target_manifest=tables.get('target_log',pd.DataFrame())
                if (target_manifest.empty or len(target_manifest)!=10
                        or not target_manifest['status'].eq('成功').all()
                        or not complete_horse_manifest(historical_target,tables.get('horse_log',pd.DataFrame()),historical_raw)):
                    raise ValueError('過去10年・全頭の取得完了後に照合できます')
                past=historical_features(historical_target,historical_raw)
                if set(pd.to_datetime(past['日付']).dt.year)!=set(range(2016,2026)):
                    raise ValueError('過去10年の年度が揃っていません')
                matched=match_historical_patterns(ranking.loc[ranking['馬名'].eq(name)].iloc[0],pattern_tables(past))
                if matched.empty:
                    st.info('前走・脚質で一致する10例以上の京都開催データはありません')
                else:
                    st.dataframe(matched,hide_index=True,use_container_width=True,column_config={'過去3着内率':st.column_config.NumberColumn(format='%.1f%%')})
                    st.caption('同じ馬や条件が重複するため、率の加算・平均による勝率換算は行いません。京都開催のみを使用します。')
            except ValueError as error:
                st.info(str(error))
        st.download_button('全頭比較CSV',to_csv_bytes(ranking),'elizabeth_2026_ranking.csv')
    st.markdown('[既存の総合分析を開く](/)')
elif mode=='詳細分析':
    st.caption('過去10年：京都7回・阪神3回（2020〜2022年）。開催場を分けて集計します。')
    target=tables.get('targets',pd.DataFrame());raw=tables.get('horse_raw',pd.DataFrame())
    if target.empty or raw.empty:st.info('過去データが未取得です。詳細設定から一括取得できます。')
    else:
        try:
            from keiba_affinity_pipeline import complete_horse_manifest
            if not tables.get('target_log',pd.DataFrame()).get('status',pd.Series(dtype=str)).eq('成功').all() or not complete_horse_manifest(target,tables.get('horse_log',pd.DataFrame()),raw):
                raise ValueError('全年度・全頭の取得が未完了です。途中のデータで好走率を表示しません。')
            years=set(pd.to_datetime(target['日付'],errors='coerce').dt.year.dropna().astype(int))
            if years != set(range(2016,2026)) or target['レースID'].nunique()!=10:
                raise ValueError('2016〜2025年の10レースが揃っていません。')
            features=historical_features(target,raw); patterns=pattern_tables(features)
            venue=st.selectbox('対象の開催場',['京都','阪神'])
            minimum=st.slider('最低サンプル数',1,30,10)
            view=patterns.loc[patterns['開催'].eq(venue)&patterns['出走数'].ge(minimum)]
            st.dataframe(view,hide_index=True,use_container_width=True,column_config={c:st.column_config.NumberColumn(c,format='%.1f%%') for c in ['3着内率','単勝回収率','複勝回収率']})
            st.caption('回収率は過去実績。将来の期待値ではありません。複勝払戻未取得は欠損です。脚質は前走4角位置の頭数比による分類です。')
            with st.expander('前走・距離・京都・S/M/Hの全項目'):
                st.dataframe(features,hide_index=True,use_container_width=True)
            st.download_button('過去10年比較CSV',to_csv_bytes(features),'elizabeth_2016_2025_features.csv')
            with st.expander('研究：過去レース経験と好走率'):
                if st.button('相性候補を計算'):
                    from keiba_affinity_workflow import analyze_verified_bundle
                    from keiba_v22 import history_frame
                    from keiba_v22 import venue_bundle
                    scoped=venue_bundle(tables,venue)
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
    st.write(f'過去レース {target.get("レースID",pd.Series(dtype=str)).nunique()}/10回 · 戦績 {done}/{total}頭')
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
                    target,targetlog=fetch_target_history(ELIZABETH,2016,2025,delay_seconds=2)
                validate_scope(target,ELIZABETH,2016,2025)
                tables.update(targets=target,target_log=targetlog);save_historical()
            if not targetlog['status'].eq('成功').all():raise ValueError('対象レースに取得失敗があります。診断は停止しています。')
            tables=collect_remaining(target,get_results,tables,store,scope,report)
            save_historical();st.success(f'取得完了：{time.monotonic()-started:.0f}秒')
        except Exception as e:
            tables,_=store.load(scope);st.session_state['v22_historical']=tables
            st.error(f'取得停止：{e}')
    if st.button('2026年の予定馬・戦績を取得',use_container_width=True):
        try:
            from keiba_core import get_results
            with st.spinner('予定馬を確認中'):
                entries=fetch_entries()
            # Refresh entries without treating withdrawn horses as current entrants.
            current['targets']=entries
            store.save('elizabeth-2026-entries',current)
            current=collect_remaining(entries,get_results,current,store,'elizabeth-2026-entries',report)
            st.session_state['v22_current']=current;st.success('予定馬・戦績を保存しました')
        except Exception as e:
            current,_=store.load('elizabeth-2026-entries');st.session_state['v22_current']=current
            st.error(f'取得停止：{e}')
    st.caption('取得間隔2秒、並列取得・自動リトライなし。通信制限時は停止します。取得元の利用条件の範囲で利用してください。')
    st.link_button('取得元の利用規約','https://netkeiba.com/info/kiyaku.html')
    if not os.environ.get('KEIBA_V22_STORE'):
        st.info('同じサーバーでは保存データを再利用できます。Streamlitの再起動・再配置をまたぐ保存は、永続ディスク未設定のためZIPバックアップが必要です。')
    if tables:
        st.download_button('過去データのバックアップZIP',pack_checkpoint(tables),'elizabeth_v22_checkpoint.zip')
    upload=st.file_uploader('エリザベス女王杯の保存ZIPを復元',type=['zip'])
    if upload is not None and st.button('検査して復元'):
        try:
            restored=load_checkpoint(upload.getvalue())
            validate_scope(restored.get('targets',pd.DataFrame()),ELIZABETH,2016,2025)
            issues=audit_checkpoint(restored)
            if issues:raise ValueError(' / '.join(issues))
            tables=restored;save_historical();st.success('対象レースを検証して復元しました')
        except Exception as e:st.error(f'復元を拒否しました：{e}')
    with st.expander('取得状況・欠損・研究ページ'):
        for key in ['target_log','horse_log']:
            if key in tables:st.dataframe(tables[key],hide_index=True,use_container_width=True)
        st.caption('研究機能は左上メニューの「過去レース相性 Ver21」から利用できます。')
