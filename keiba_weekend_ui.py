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
