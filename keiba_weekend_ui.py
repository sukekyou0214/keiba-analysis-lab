"""管理・検証：週末の全芝レース一括分析（サーバー上で実行し、結果をダウンロード）。"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path('/tmp/keiba_analysis_v141/weekend')


def upcoming_weekend(today=None):
    today = today or datetime.now(JST).date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return saturday, saturday + timedelta(days=1)


def folders(dates):
    key = '_'.join(d.strftime('%Y%m%d') for d in dates)
    return ROOT / key / 'work', ROOT / key / 'out', [d.strftime('%Y%m%d') for d in dates]


def render_weekend_batch():
    import streamlit as st
    import keiba_weekend_batch as wb
    from keiba_fetch_policy import POLICY, format_time
    with st.expander('週末の全芝レース一括分析', expanded=False):
        st.caption('指定日の全芝レース（障害・ダート除く）の出走表・戦績・S/M/Hを取得し、ラボの参考順位（S/M/H別）と'
                   'データ充足の信頼度を出します。取得は共通ゲート（取得先ごと1件ずつ・最小間隔・拒否が続けば停止）を通し、'
                   'サーバー上で進むので画面を閉じても続きます。結果は取得時点の固定記録です。')
        sat, sun = upcoming_weekend()
        c1, c2 = st.columns(2)
        d1 = c1.date_input('1日目', value=sat, key='weekend_d1_v40')
        use2 = c2.checkbox('2日目も対象', value=True, key='weekend_use2_v40')
        dates = [d1] + ([d1 + timedelta(days=1)] if use2 else [])
        work, out, keys = folders(dates)
        running = wb.is_running(work)
        until = POLICY.cooldown_until()
        status = wb.read_status(work)
        if until and not running:
            st.warning(f'取得先の制限のため {format_time(until)} まで開始できません。')
        label = '続きから再開' if status and not running else '一括分析を開始'
        if st.button(label, disabled=running or bool(until), key='weekend_start_v40', type='primary', width='stretch'):
            started = wb.start_background(keys, work, out)
            st.success('サーバー上で開始しました。「状況を更新」で進み具合を確認できます。' if started else 'すでに実行中です。')
            status = wb.read_status(work)
            running = True
        st.button('状況を更新', key='weekend_refresh_v40')
        if status:
            st.write(f"状態：**{'実行中' if running else status.get('状態', '—')}**　段階：{status.get('段階', '—')}　"
                     f"更新：{status.get('更新日時', '—')}")
            details = {k: status[k] for k in ('対象レース', '対象馬', '照合対象') if k in status}
            if details:
                st.caption('　'.join(f'{k}：{v}' for k, v in details.items()))
            if status.get('結果'):
                st.caption('結果：' + str(status['結果']))
        files = sorted(out.glob('*')) if out.exists() else []
        if files and not running:
            st.caption('出力（取得データは私的利用の範囲で扱い、再配布しないでください）')
            for path in files:
                mime = 'text/csv' if path.suffix == '.csv' else 'text/html' if path.suffix == '.html' else \
                    'application/zip' if path.suffix == '.zip' else 'application/json'
                st.download_button(path.name, data=path.read_bytes(), file_name=path.name, mime=mime,
                                   key='weekend_dl_' + path.name, width='stretch')


def render_weekend_picker(tap_choice):
    """「レースを選ぶ」：今週末の一括取得済み芝レースを、通信なしで本体の保存データとして開く。"""
    import streamlit as st
    import keiba_weekend_batch as wb
    races = wb.weekend_races(ROOT)
    work, _, _ = folders(list(upcoming_weekend()))
    running = wb.is_running(work)
    if running:
        status = wb.read_status(work)
        st.info(f"今週末の芝レースを取得中です（{status.get('段階', '準備中')}・更新 {status.get('更新日時', '—')}）。"
                '終わるまで、開いたレースの戦績・S/M/Hは揃っていない場合があります。')
    if races.empty:
        if not running:
            st.caption('今週末の芝レース：まだ取得していません。サーバーが再起動すると保存データは消えます。'
                       '管理・検証の「週末の全芝レース一括分析」で取り直せます（約1時間）。')
        return
    labels = {r['レースID']: wb.race_label(r) for _, r in races.iterrows()}
    choice = tap_choice('今週末の芝レース', ['選択しない'] + list(labels), 'weekend_race_choice_v44',
                        format_func=lambda rid: labels.get(rid, rid))
    if st.button('このレースを開く', disabled=choice == '選択しない', key='weekend_race_open_v44', width='stretch'):
        open_weekend_race(races.set_index('レースID').loc[choice], choice)
        st.rerun()
    st.caption(f'一括取得済み：{len(races)}レース（取得時点の出走表・戦績・S/M/H。開くときは再取得しません）')


def open_weekend_race(row, race_id):
    import streamlit as st
    import keiba_weekend_batch as wb
    from keiba_core import load_race_cache, save_race_cache, prepare
    from keiba_main_conditions import checked_context
    from keiba_v22 import Store
    info, horses, numbers, data, metadata, missing = wb.race_bundle(row['作業フォルダ'], race_id)
    restored = load_race_cache(race_id)
    merged = wb.merge_into_saved(restored[1] if restored else None, data)
    if merged is not None and not merged.empty:
        save_race_cache(race_id, horses, prepare(merged))
    context = checked_context(race_id, {'race_date': info['開催日'], 'venue': info['競馬場'], 'surface': info['コース'],
                                        'distance': info['距離'], 'name': info['レース名'],
                                        'grade': info.get('格'), 'source': '出走表から取得（一括取得）'})
    if context:
        st.session_state['main_target_context'] = context
    Store().save('main-entry-metadata-' + race_id, {'targets': metadata})
    st.session_state['weekend_numbers_v44'] = {race_id: numbers}
    st.session_state['weekend_missing_v44'] = {race_id: missing}
    st.session_state.race_input_v141 = race_id
    st.session_state.auto_race_id = ''  # 次の描画で保存データから復元させる
