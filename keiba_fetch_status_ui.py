"""管理・検証：netkeibaへの取得状況（要求数・拒否・次回可能時刻・未取得馬）。"""
import pandas as pd


def unfetched_rows(horses, numbers, data):
    from keiba_main_integration import horses_without_history
    missing = horses_without_history(horses or {}, data)
    return pd.DataFrame([{'馬番': numbers.get(hid, '—') if numbers else '—', '馬名': name, '状態': '戦績未取得'}
                         for hid, name in missing.items()])


def render_fetch_status(race_id, horses, numbers, data):
    import streamlit as st
    from keiba_fetch_policy import POLICY, MIN_INTERVAL_BOUNDS, DEFAULT_BUDGET, format_time
    with st.expander('取得先の状況（netkeiba）', expanded=bool(POLICY.cooldown_until())):
        until = POLICY.cooldown_until()
        if until:
            st.warning(f'取得先の制限のため {format_time(until)} まで取得を停止中です。自動では再開しません。')
        st.caption(f'すべての取得は取得先ごとに1件ずつ、{POLICY.min_interval:.0f}秒以上の間隔で行います。'
                   f'1回の操作は最大{DEFAULT_BUDGET}件まで。拒否（HTTP 400/403/429/503・通信エラー）が2回続くと、'
                   '30分（繰り返すと倍、最大6時間）取得を止めます。件数はサーバー再起動で0に戻ります。')
        rows = POLICY.snapshot()
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        else:
            st.caption('このサーバーではまだ取得していません。')
        low, high = MIN_INTERVAL_BOUNDS
        interval = st.number_input('取得先ごとの最小間隔（秒）', min_value=float(low), max_value=float(high),
                                   value=float(POLICY.min_interval), step=1.0, key='fetch_min_interval_v38')
        if float(interval) != float(POLICY.min_interval):
            POLICY.set_min_interval(interval)
            st.caption(f'最小間隔を{POLICY.min_interval:.0f}秒に変更しました。')
        if race_id and horses:
            missing = unfetched_rows(horses, numbers, data)
            if missing.empty:
                st.caption(f'対象レースの出走馬{len(horses)}頭すべての戦績を保存済みです。')
            else:
                st.write(f'戦績未取得：{len(missing)}/{len(horses)}頭（参考順位の計算から除外して保留表示）')
                st.dataframe(missing, hide_index=True, width='stretch')
