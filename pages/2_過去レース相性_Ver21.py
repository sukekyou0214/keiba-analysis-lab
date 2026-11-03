"""Ver.21 pilot UI: analyze verified historical target entrants from CSV.

Until the complete historical data-fetch workflow is implemented, the
user must supply acquired data and historical target race IDs. This page
does not silently fetch, infer or fill missing results.
"""
from __future__ import annotations

import io
import re
import zipfile
import pandas as pd
import streamlit as st

from keiba_affinity import to_csv_bytes
from keiba_affinity_adapter import analyze_legacy_csv

st.set_page_config(page_title="Ver.21 過去レース相性", page_icon="📊", layout="centered")
st.markdown("""
<style>
/* Keep the mobile header and acquisition controls readable without zooming. */
.block-container {padding-top: 1rem; padding-bottom: 2rem;}
h1 {font-size: clamp(1.45rem, 5vw, 2rem) !important; line-height: 1.25 !important;}
h2, h3 {font-size: 1.15rem !important;}
div[data-testid="stAlert"] {font-size: .9rem;}
div[data-testid="stMetricValue"] {font-size: 1.35rem;}
</style>
""", unsafe_allow_html=True)
st.title("過去レース相性分析")
st.caption("Ver.21 試験版 · 過去の出走歴と好走傾向を検証")
st.info("使い方：①対象レースを取得 → ②馬別戦績を20頭ずつ取得 → ③相性を計算。取得途中はZIPで保存できます。")
st.caption("注意：結果は探索的な相性候補です。IDを確認できない海外戦績は除外し、予測精度は保証しません。")

# Show progress before the long acquisition controls on narrow iPhone screens.
_progress_targets = st.session_state.get("v21_target_results")
_progress_log = st.session_state.get("v21_horse_log")
if isinstance(_progress_targets, pd.DataFrame) and not _progress_targets.empty:
    _total_horses = int(_progress_targets["馬ID"].astype(str).nunique())
    _done_horses = (int(_progress_log.loc[
        _progress_log["status"].eq("成功"), "horse_id"
    ].astype(str).nunique()) if isinstance(_progress_log, pd.DataFrame)
        and not _progress_log.empty else 0)
    _done_horses = min(_done_horses, _total_horses)
    a, b = st.columns(2)
    a.metric("対象レース出走", f"{len(_progress_targets)}件")
    b.metric("馬別戦績取得", f"{_done_horses}/{_total_horses}頭")
    st.progress(_done_horses / _total_horses)
    if _done_horses < _total_horses:
        st.caption("②を繰り返して全頭取得してください。途中でも③のZIP保存が使えます。")
    else:
        st.success("馬別戦績の取得が揃いました。②内の相性計算へ進んでください。")
from keiba_affinity_collect import fetch_target_history

with st.expander("① 対象レースを取得", expanded=True):
    st.caption("netkeibaの重賞特集ページを指定します。まずは10レースまで。取得先の利用条件・アクセス制限を守って利用してください。")
    special_url = st.text_input(
        "netkeiba重賞特集ページ",
        value="https://race.netkeiba.com/special/index.html?id=0109",
        key="v21_special_url",
    )
    current_start = st.number_input("取得開始年", min_value=2000, max_value=2025, value=2016, step=1)
    current_end = st.number_input("取得終了年", min_value=2000, max_value=2025, value=2025, step=1)
    if st.button("対象レース結果を取得", use_container_width=True):
        try:
            with st.spinner("対象レースの結果と欠損を照合しています"):
                historical_df, acquisition_log = fetch_target_history(
                    special_url, int(current_start), int(current_end)
                )
            for cache_key in ("v21_horse_log", "v21_horse_raw", "v21_affinity_result", "v21_data_quality"):
                st.session_state.pop(cache_key, None)
            st.session_state["v21_target_results"] = historical_df
            st.session_state["v21_acquisition_log"] = acquisition_log
        except Exception as error:
            st.error(f"取得に失敗しました：{error}")
    if "v21_acquisition_log" in st.session_state:
        log_df = st.session_state["v21_acquisition_log"]
        st.dataframe(log_df, use_container_width=True, hide_index=True)
        st.download_button(
            "取得状況CSVを保存", to_csv_bytes(log_df),
            "acquisition_manifest.csv", mime="text/csv", use_container_width=True,
        )
        result_df = st.session_state.get("v21_target_results")
        if result_df is not None and not result_df.empty:
            st.caption(f"取得できた対象レース出走データ：{len(result_df)}行")
            st.download_button(
                "対象レース結果CSVを保存", to_csv_bytes(result_df),
                "historical_target_results.csv", mime="text/csv",
                use_container_width=True,
            )
    st.caption("対象レース取得後、②で各馬の過去戦績を取得してください。")

with st.expander("② 馬別戦績を取得・相性計算", expanded=True):
    st.caption("過去レース全頭の取得成功が前提です。重複取得を避けて最大20頭ずつ取得します。")
    if st.button("未取得の馬を20頭取得", use_container_width=True):
        from keiba_affinity_pipeline import collect_batch
        from keiba_core import get_results
        target_df = st.session_state.get("v21_target_results")
        manifest_df = st.session_state.get("v21_acquisition_log")
        if target_df is None or target_df.empty or manifest_df is None or not manifest_df["status"].eq("成功").all():
            st.error("対象レースの全年度取得が完了していません。")
        else:
            previous_log = st.session_state.get("v21_horse_log", pd.DataFrame())
            finished = set(previous_log.loc[previous_log["status"].eq("成功"), "horse_id"]) if not previous_log.empty else set()
            with st.spinner("過去戦績を取得中"):
                new_raw, new_log = collect_batch(target_df, get_results, completed=finished)
            if not new_log.empty:
                st.session_state.pop("v21_affinity_result", None)
                st.session_state.pop("v21_data_quality", None)
            if not new_log.empty:
                st.session_state["v21_horse_log"] = pd.concat([previous_log, new_log], ignore_index=True).drop_duplicates("horse_id", keep="last")
            if not new_raw.empty:
                previous_raw = st.session_state.get("v21_horse_raw", pd.DataFrame())
                st.session_state["v21_horse_raw"] = pd.concat([previous_raw, new_raw], ignore_index=True)
    horse_log = st.session_state.get("v21_horse_log")
    if horse_log is not None and not horse_log.empty:
        st.dataframe(horse_log, hide_index=True, use_container_width=True)
        st.download_button("馬別取得ログCSV", to_csv_bytes(horse_log), "horse_acquisition.csv", mime="text/csv")
    if st.button("全頭取得完了を検査して相性計算", use_container_width=True):
        from keiba_affinity_pipeline import complete_horse_manifest
        from keiba_core import convert_auto
        targets = st.session_state.get("v21_target_results")
        target_log = st.session_state.get("v21_acquisition_log")
        history = st.session_state.get("v21_horse_raw")
        if targets is None or target_log is None or history is None or horse_log is None:
            st.error("必要な取得結果が不足しています。")
        elif not target_log["status"].eq("成功").all() or not complete_horse_manifest(targets, horse_log, history):
            st.error("未取得・取得失敗の馬がいるため相性判定を中止しました。")
        else:
            try:
                from keiba_affinity_workflow import analyze_verified_bundle
                # Restored checkpoints may contain raw provider columns or
                # already-converted history columns; never convert twice.
                if {"着順_数値", "人気", "レースID", "馬ID", "日付", "レース名"}.issubset(history.columns):
                    converted_history = history.copy()
                else:
                    converted_history = convert_auto(history)
                affinity_result, data_quality = analyze_verified_bundle(
                    targets, target_log, converted_history, horse_log
                )
                st.session_state["v21_affinity_result"] = affinity_result
                st.session_state["v21_data_quality"] = data_quality
            except Exception as error:
                st.error(f"相性計算を中止しました：{error}")
    affinity_result = st.session_state.get("v21_affinity_result")
    if affinity_result is not None:
        st.subheader("相性候補")
        if affinity_result.empty:
            st.info("条件を満たす候補がありません。データ品質を確認してください。")
        else:
            _view = affinity_result.copy()
            _view["3着内率"] = (_view["place_rate"] * 100).round(1)
            _view["比較群3着内率"] = (_view["comparison_place_rate"] * 100).round(1)
            _view["比較差（pt）"] = (_view["adjusted_place_difference"] * 100).round(1)
            _view = _view.rename(columns={
                "race_name": "過去レース", "n": "経験馬数",
                "places": "3着内頭数", "confidence": "判定",
            })
            _view = _view[["過去レース", "経験馬数", "3着内頭数", "3着内率",
                            "比較群3着内率", "比較差（pt）", "判定"]]
            st.dataframe(_view, hide_index=True, use_container_width=True)
            st.caption("比較差は同じ対象レース・人気帯の比較に基づく参考値。小標本は判定保留です。")
        quality_frame = st.session_state.get("v21_data_quality")
        if quality_frame is not None:
            st.dataframe(quality_frame, hide_index=True, use_container_width=True)
        st.download_button("相性候補CSV", to_csv_bytes(affinity_result), "race_affinity.csv", mime="text/csv")




with st.expander("③ 途中保存・ZIP復元"):
    from keiba_affinity_store import pack_checkpoint, load_checkpoint, audit_checkpoint
    checkpoint_keys = {
        "targets": "v21_target_results",
        "target_log": "v21_acquisition_log",
        "horse_raw": "v21_horse_raw",
        "horse_log": "v21_horse_log",
        "affinity": "v21_affinity_result",
    }
    snapshot = {key: st.session_state[value] for key, value in checkpoint_keys.items()
                if value in st.session_state and isinstance(st.session_state[value], pd.DataFrame)}
    if snapshot:
        try:
            st.download_button(
                "取得途中のデータもZIPで一括保存",
                data=pack_checkpoint(snapshot),
                file_name="keiba_ver21_checkpoint.zip",
                mime="application/zip",
                use_container_width=True,
            )
        except ValueError as error:
            st.error(f"保存できません：{error}")
    uploaded_checkpoint = st.file_uploader("以前の保存ZIPを復元", type=["zip"], key="v21_checkpoint_zip")
    if uploaded_checkpoint is not None and st.button("保存ZIPから復元", use_container_width=True):
        try:
            tables = load_checkpoint(uploaded_checkpoint.getvalue())
            problems = audit_checkpoint(tables)
            if problems:
                raise ValueError(" / ".join(problems))
            for key in (*checkpoint_keys.values(), "v21_data_quality"):
                st.session_state.pop(key, None)
            for key, table in tables.items():
                st.session_state[checkpoint_keys[key]] = table
            st.success("復元しました。取得状況と対象レースを確認してください。")
        except (ValueError, KeyError, OSError, UnicodeError, RuntimeError, zipfile.BadZipFile) as error:
            st.error(f"ZIPを復元できません：{error}")
    st.caption("ZIPはiPhoneに保存してください。Streamlitのセッションだけでは永続保存されません。")


with st.expander("上級者向け：手持ちのCSVから分析"):
    uploaded = st.file_uploader("保存した成績CSVを選択（複数ファイル可）", type=["csv"], accept_multiple_files=True)
    race_ids_raw = st.text_area(
    "過去の対象レースID（12桁、改行またはカンマ区切り）",
    placeholder="例：202505041111\n202405041111",
    height=95,
)
    if st.button("過去レース相性を計算", type="primary", use_container_width=True):
        if not uploaded:
            st.error("CSVファイルを選択してください。")
        else:
            try:
                frames = []
                for upload in uploaded:
                    payload = upload.getvalue()
                    parsed = None
                    for enc in ("utf-8-sig", "cp932", "utf-8"):
                        try:
                            parsed = pd.read_csv(
                                io.BytesIO(payload), encoding=enc,
                                dtype={"馬ID": "string", "レースID": "string", "horse_id": "string", "race_id": "string"},
                            )
                            break
                        except UnicodeDecodeError:
                            pass
                    if parsed is None:
                        raise ValueError(f"{upload.name}: unsupported encoding")
                    frames.append(parsed)
                ids = [part.strip() for part in re.split(r"[,，\s]+", race_ids_raw) if part.strip()]
                result, quality = analyze_legacy_csv(pd.concat(frames, ignore_index=True), ids)
                st.subheader("データ品質")
                st.dataframe(quality.T.rename(columns={0: "値"}), use_container_width=True)
                if result.empty:
                    st.info("出走経験の候補がありません。元データに過去出走歴が含まれているか確認してください。")
                else:
                    st.subheader("相性候補（探索的）")
                    st.dataframe(result, use_container_width=True, hide_index=True)
                    st.download_button(
                        "race_affinity.csv をダウンロード",
                        to_csv_bytes(result),
                        "race_affinity.csv", mime="text/csv",
                        use_container_width=True,
                    )
                st.download_button(
                    "data_quality.csv をダウンロード",
                    to_csv_bytes(quality), "data_quality.csv", mime="text/csv",
                    use_container_width=True,
                )
            except (ValueError, TypeError, KeyError, pd.errors.ParserError) as error:
                st.error(f"計算できませんでした：{error}")
