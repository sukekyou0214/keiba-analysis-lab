"""Ver.21 pilot UI: analyze verified historical target entrants from CSV.

Until the complete historical data-fetch workflow is implemented, the
user must supply acquired data and historical target race IDs. This page
does not silently fetch, infer or fill missing results.
"""
from __future__ import annotations

import io
import re
import pandas as pd
import streamlit as st

from keiba_affinity import to_csv_bytes
from keiba_affinity_adapter import analyze_legacy_csv

st.set_page_config(page_title="Ver.21 過去レース相性", page_icon="📊", layout="centered")
st.title("過去レース相性分析｜Ver.21 試験版")
st.caption("既存の総合診断は変更しません。過去の対象レース全出走馬の成績CSVを使って検証します。")
st.warning("試験版：対象レースの全出走馬の網羅性はまだ自動検証できません。相性は仮説候補であり予測的中を保証しません。")
from keiba_affinity_collect import fetch_target_history

with st.expander("過去10年の対象レース結果を自動取得（試験版）"):
    st.caption("netkeibaの重賞特集ページを指定します。まずは10レースまで。取得先の利用条件・アクセス制限を守って利用してください。")
    special_url = st.text_input(
        "netkeiba重賞特集ページ",
        placeholder="https://race.netkeiba.com/special/index.html?id=0109",
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
    st.caption("このデータだけでは過去出走歴の相性は判定できません。各馬の過去戦績と結合する次工程が必要です。")


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
