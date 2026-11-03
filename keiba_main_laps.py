"""Resume original lap collection from the main screen, one request at a time."""
import pandas as pd


def recent_lap_ids(data, per_horse=5):
    if data.empty or not {'馬ID', '日付', 'レースID'}.issubset(data):
        return []
    frame = data.copy()
    frame['日付'] = pd.to_datetime(frame['日付'], errors='coerce')
    frame = frame.loc[frame['日付'].notna()]
    frame = frame.sort_values('日付', ascending=False, kind='stable').groupby('馬ID', sort=False).head(per_horse)
    ids = frame['レースID'].fillna('').astype(str)
    return ids.loc[ids.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()


def pending_lap_ids(data, stored):
    done = set()
    if not stored.empty and {'レースID', '検証'}.issubset(stored):
        done = set(stored.loc[stored['検証'].eq('OK') | stored.get('取得状態', pd.Series('', index=stored.index)).eq('取得不可'), 'レースID'].astype(str))
    return [rid for rid in recent_lap_ids(data) if rid not in done]


def enrich_laps(data, stored, loader, save, cached=lambda _:None,
                cache_save=lambda *args:None, progress=lambda *args:None, sleeper=None):
    import time
    from keiba_core import classify_lap_shape_v16
    sleeper = sleeper or time.sleep
    rows = {str(r['レースID']):r for r in stored.to_dict('records')} if not stored.empty else {}
    pending = pending_lap_ids(data, stored)
    error = None
    for n, rid in enumerate(pending, 1):
        try:
            payload = cached(rid)
            hit = bool(payload and payload.get('検証') == 'OK')
            if not hit:
                sleeper(2)
                payload = loader(rid)
            if not isinstance(payload, dict) or '検証' not in payload:
                raise ValueError('ラップ取得結果の形式が不正です')
            extra = classify_lap_shape_v16(payload.get('200mラップ', ''), payload.get('レース距離'), payload['検証'])
            valid = payload['検証'] == 'OK' and extra['展開タイプ'] != '判定不可'
            if valid and not hit:
                cache_save(rid, payload)
            sample = data.loc[data['レースID'].astype(str).eq(rid)].iloc[0]
            row = {'レースID':rid, 'レース名':sample.get('レース名', ''), '開催日':str(sample.get('日付', '')),
                   'レース距離':payload.get('レース距離'), '200mラップ':payload.get('200mラップ', ''),
                   '検証':payload['検証'] if valid else '取得不可',
                   '取得状態':'成功' if valid else '取得不可',
                   '取得方法':'保存済み' if hit else '新規取得',
                   '取得理由':'' if valid else str(payload['検証']), **extra}
        except ValueError as exc:
            if str(exc) != 'ラップタイム見出しが見つかりません':
                error = str(exc)[:180]
                break
            row = {'レースID':rid, '検証':'取得不可', '取得状態':'取得不可',
                   '展開タイプ':'判定不可', '取得理由':str(exc)}
        except Exception as exc:
            error = str(exc)[:180]
            break
        rows[rid] = row
        save(pd.DataFrame(list(rows.values())))
        progress(n, len(pending))
    return pd.DataFrame(list(rows.values())), error


def render_main_laps():
    import streamlit as st
    from keiba_core import load_lap_full_progress, save_lap_full_progress, load_lap_v16, save_lap_v16, get_lap_table_test_v1594
    data = st.session_state.get('auto_df')
    rid = st.session_state.get('auto_race_id', '')
    if not isinstance(data, pd.DataFrame) or data.empty or not rid:
        return
    stored = load_lap_full_progress(rid)
    ids = recent_lap_ids(data)
    pending = pending_lap_ids(data, stored)
    scope = stored.loc[stored['レースID'].astype(str).isin(ids)] if not stored.empty else stored
    known = int(scope['検証'].eq('OK').sum()) if not scope.empty else 0
    missing = len(ids) - len(pending) - known
    st.caption(f'本体の直近5走：200mラップ確認済み {known}／取得不可 {missing}／未処理 {len(pending)}レース')
    st.caption('既存のラップ分析へ保存・反映。2秒間隔で順次取得し、通信エラー時は停止します。レース全体の暫定分類です。')
    if st.button('本体の不足データを補完：直近5走の200mラップ', disabled=not pending, key='main_enrich_laps'):
        import time
        start = time.perf_counter()
        bar = st.progress(0)
        def save(frame):
            save_lap_full_progress(rid, frame)
            st.session_state['lap_shapes_v163'] = frame
            st.session_state['lap_shapes_v163_race'] = rid
        out, error = enrich_laps(data, stored, get_lap_table_test_v1594, save,
                                cached=load_lap_v16, cache_save=save_lap_v16,
                                progress=lambda n,total:bar.progress(n/total))
        if error:
            st.warning('ラップ取得を停止しました。取得済み分は保存済みです：' + error)
        else:
            st.success('200mラップを保存しました。本体の総合診断に反映します。')
        st.caption(f'処理時間 {time.perf_counter()-start:.0f}秒')
