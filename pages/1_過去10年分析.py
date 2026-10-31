"""競馬分析ラボ追加機能：ダービー成績 × 天皇賞（秋）過去10年検証。

導入：GitHubで pages/1_過去10年分析.py という名前で新規ファイルを作成し、
この内容をそのままコピーする。Streamlitのページとして自動認識される。
既存の app.py への変更は不要。

入力は2つのCSV/Excel（天皇賞秋・ダービー）。1行=1頭の出走実績。
カラム：年, 馬名, 人気, 着順（任意：馬ID, 単勝オッズ, 複勝払戻）。
実データの不明欄は埋めず、欠損として処理する。
"""
from __future__ import annotations

import io
import re
import unicodedata
from typing import Optional

import pandas as pd
import streamlit as st

DATA_COLS = ["年", "馬名", "馬ID", "人気", "着順", "単勝オッズ", "複勝払戻"]


def _normalize_name(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).strip())


def _parse_number(value):
    if pd.isna(value):
        return None
    s = unicodedata.normalize("NFKC", str(value)).replace(",", "").strip()
    if not s or s in ("-", "--", "取消", "除外", "中止", "失格", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_table(file):
    if file.name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(file)
    payload = file.getvalue()
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return pd.read_csv(io.BytesIO(payload), encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(payload))


def _standardize(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    aliases = {
        "年": ["年", "開催年", "year", "年度"],
        "馬名": ["馬名", "競走馬名", "horse", "name"],
        "馬ID": ["馬ID", "馬id", "horse_id", "horseid"],
        "人気": ["人気", "単勝人気", "人気順", "popularity"],
        "着順": ["着順", "順位", "finish", "rank"],
        "単勝オッズ": ["単勝オッズ", "単勝", "単勝倍率", "odds"],
        "複勝払戻": ["複勝払戻", "複勝払戻金", "複勝配当", "place_payout"],
    }
    src = frame.copy()
    src.columns = [unicodedata.normalize("NFKC", str(c)).strip() for c in src.columns]
    rename = {}
    for standard, options in aliases.items():
        for alias in options:
            if alias in src.columns:
                rename[alias] = standard
                break
    src = src.rename(columns=rename)
    required = {"年", "馬名", "人気", "着順"}
    missing = required - set(src.columns)
    if missing:
        raise ValueError(f"{kind}の表に必須列がありません：{'、'.join(sorted(missing))}")
    src = src.loc[:, ~src.columns.duplicated()].copy()
    for col in DATA_COLS:
        if col not in src:
            src[col] = pd.NA
    src = src[DATA_COLS].copy()
    for col in ("年", "人気", "着順", "単勝オッズ", "複勝払戻"):
        src[col] = pd.to_numeric(src[col].map(_parse_number), errors="coerce")
    src["馬名"] = src["馬名"].map(_normalize_name)
    src["馬ID"] = src["馬ID"].map(lambda x: str(x).strip() if pd.notna(x) and str(x).strip() else "")
    src = src[src["年"].between(1900, 2100) & src["馬名"].ne("")].copy()
    src["年"] = src["年"].astype(int)
    if src.duplicated(["年", "馬名"]).any():
        examples = src.loc[src.duplicated(["年", "馬名"], keep=False), ["年", "馬名"]].head(5)
        raise ValueError(f"{kind}に同一年・同名馬の重複があります：{examples.to_dict('records')}")
    return src.reset_index(drop=True)


def classify_derby(pop: Optional[float], finish: Optional[float], popular_limit=3, good_limit=3):
    if pd.isna(pop) or pd.isna(finish):
        return "ダービー成績不明"
    if pop <= popular_limit and finish <= good_limit:
        return "上位人気・好走"
    if pop <= popular_limit and finish > good_limit:
        return "上位人気・凡走"
    if pop > popular_limit and finish <= good_limit:
        return "低人気・好走"
    return "低人気・凡走"


def build_join(autumn: pd.DataFrame, derby: pd.DataFrame, start_year=2016, end_year=2025,
               popular_limit=3, good_limit=3) -> pd.DataFrame:
    """秋天を母数に、同馬の『秋天開催年以前』のダービーを結合する。"""
    a = _standardize(autumn, "天皇賞・秋")
    d = _standardize(derby, "日本ダービー")
    a = a[a["年"].between(start_year, end_year)].copy()
    d = d[d["年"] <= end_year].copy()
    a["照合キー"] = a["馬ID"].where(a["馬ID"].ne(""), "NAME:" + a["馬名"])
    d["照合キー"] = d["馬ID"].where(d["馬ID"].ne(""), "NAME:" + d["馬名"])
    # 馬IDが片方だけにある場合も馬名で照合。ただし同名異馬の可能性があるため要確認。
    a["照合名"] = a["馬名"]
    d["照合名"] = d["馬名"]
    # 日本ダービーは原則1頭1回だが、重複や年不整合を安全に扱う。
    matched = a.merge(d.add_prefix("D_"), left_on="照合名", right_on="D_照合名",
                      how="left", validate="many_to_many", suffixes=("", "_derby"))
    # 秋天より未来のダービー記録は使用しない。未来の記録しかない馬も秋天側に残す。
    matched.loc[matched["D_年"].gt(matched["年"]),
                ["D_年", "D_人気", "D_着順", "D_単勝オッズ", "D_複勝払戻", "D_馬ID"]] = float("nan")
    # 最も近い過去ダービーを採用。同年に二度走ることは想定しない。
    matched = matched.sort_values("D_年", ascending=False, na_position="last")
    matched = matched.drop_duplicates(["年", "馬名"], keep="first")
    # 馬IDが両方埋まり不一致なら不一致として棄却。
    mismatch = matched["馬ID"].ne("") & matched["D_馬ID"].fillna("").ne("") & matched["馬ID"].ne(matched["D_馬ID"])
    matched.loc[mismatch, ["D_年", "D_人気", "D_着順", "D_単勝オッズ", "D_複勝払戻"]] = float("nan")
    matched["ダービー分類"] = matched.apply(
        lambda r: classify_derby(r["D_人気"], r["D_着順"], popular_limit, good_limit)
        if pd.notna(r["D_年"]) else "ダービー出走歴なし/未収録", axis=1)
    return matched.sort_values(["年", "着順", "馬名"], ascending=[False, True, True]).reset_index(drop=True)


def summarize(joined: pd.DataFrame) -> pd.DataFrame:
    """回収率は100円を各馬に均等購入した想定。単勝はオッズから計算する。"""
    rows = []
    for group, x in joined.groupby("ダービー分類", dropna=False, sort=False):
        valid = x[x["着順"].ge(1)].copy()
        n = len(valid)
        wins = int(valid["着順"].eq(1).sum())
        places = int(valid["着順"].le(3).sum())
        odds_known = valid["単勝オッズ"].notna()
        # 複勝払戻は100円あたりの実際の配当。的中時のみ必須。
        place_known = valid["着順"].gt(3) | valid["複勝払戻"].notna()
        single_roi = (float(valid.loc[valid["着順"].eq(1), "単勝オッズ"].sum())
                      * 100 / n if n and (valid.loc[valid["着順"].eq(1), "単勝オッズ"].notna().all()) else None)
        place_roi = (float(valid.loc[valid["着順"].le(3), "複勝払戻"].sum())
                     / n if n and place_known.all() else None)
        rows.append({"分類": group, "出走": n, "1着": wins, "3着以内": places,
                     "勝率(%)": round(wins / n * 100, 1) if n else None,
                     "複勝率(%)": round(places / n * 100, 1) if n else None,
                     "単勝回収率(%)": round(single_roi, 1) if single_roi is not None else None,
                     "複勝回収率(%)": round(place_roi, 1) if place_roi is not None else None,
                     "単勝オッズ取得": int(odds_known.sum()),
                     "複勝配当確認": int(place_known.sum())})
    return pd.DataFrame(rows)


def _csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8-sig")


def render_history_page():
    """既存のStreamlitページから選択時だけ呼び出す。通信・既存処理に非依存。"""
    st.subheader("過去10年・ダービー実績 × 天皇賞・秋")
    st.caption("上位人気・好走、上位人気・凡走、低人気・好走を検証。秋天の全出走馬を分母にします。")
    with st.expander("入力ファイルの形式と注意", expanded=False):
        st.write("天皇賞・秋と日本ダービーを別々のCSV/Excelで用意してください。どちらも1行1頭。必須列は「年・馬名・人気・着順」。")
        st.write("任意列は「馬ID・単勝オッズ・複勝払戻」。複勝払戻は的中馬について100円あたりの円額を入力。")
        st.write("天皇賞・秋は対象の10年分の全出走馬、日本ダービーはその馬たちが出た年まで遡って収録してください。")
        st.warning("未収録とダービー未出走はCSVだけでは区別できません。集計で両者をまとめ、網羅性を確認してください。")
    left, right = st.columns(2)
    with left:
        autumn_file = st.file_uploader("天皇賞・秋 全出走馬（CSV / Excel）", type=["csv", "xlsx"], key="autumn_history_upload")
    with right:
        derby_file = st.file_uploader("日本ダービー 全出走馬（CSV / Excel）", type=["csv", "xlsx"], key="derby_history_upload")
    c1, c2 = st.columns(2)
    with c1:
        last_year = st.number_input("集計最終年", min_value=2000, max_value=2100, value=2025, step=1, key="autumn_last_year")
        popular = st.number_input("上位人気の範囲", min_value=1, max_value=18, value=3, step=1)
    with c2:
        year_count = st.number_input("対象年数", min_value=1, max_value=30, value=10, step=1)
        good = st.number_input("ダービー好走の着順", min_value=1, max_value=18, value=3, step=1)
    if not (autumn_file and derby_file):
        st.info("2つの結果ファイルを読み込むと、分類と複勝率・回収率を表示します。")
        return
    try:
        a = _load_table(autumn_file)
        d = _load_table(derby_file)
        joined = build_join(a, d, int(last_year - year_count + 1), int(last_year), int(popular), int(good))
        if joined.empty:
            st.warning("対象期間に天皇賞・秋のデータがありません。")
            return
        summary = summarize(joined)
    except Exception as ex:
        st.error(f"読み込みまたは照合エラー：{ex}")
        return
    year_counts = joined.groupby("年").agg(出走頭数=("馬名", "count"), 馬券内頭数=("着順", lambda s: int(s.le(3).sum())))
    expected = set(range(int(last_year - year_count + 1), int(last_year) + 1))
    missing_years = sorted(expected - set(year_counts.index))
    if missing_years:
        st.warning("未収録年（この期間は10年分の完全集計ではありません）：" + ", ".join(map(str, missing_years)))
    insufficient = year_counts[(year_counts["馬券内頭数"] != 3) | (year_counts["出走頭数"] < 8)]
    if not insufficient.empty:
        st.warning("結果不足または不自然な頭数の年があります。年別取得状況を確認してください。")
    missing_derby = int(joined["ダービー分類"].eq("ダービー出走歴なし/未収録").sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("秋天の収録年", f"{year_counts.shape[0]} / {int(year_count)}")
    m2.metric("秋天の全出走記録", str(len(joined)))
    m3.metric("ダービー照合なし", str(missing_derby))
    st.markdown("#### 分類別成績")
    st.dataframe(summary, hide_index=True, use_container_width=True)
    st.caption("単勝回収率は単勝オッズ×100円、複勝回収率は100円払戻を使用。欠損時は算出不可。条件別の標本数を必ず確認してください。")
    with st.expander("年別データ品質", expanded=False):
        st.dataframe(year_counts.reset_index(), hide_index=True, use_container_width=True)
    with st.expander("全頭の照合結果", expanded=False):
        cols = ["年", "馬名", "人気", "着順", "D_年", "D_人気", "D_着順", "ダービー分類"]
        display = joined[cols].rename(columns={"人気": "秋天人気", "着順": "秋天着順", "D_年": "ダービー年", "D_人気": "ダービー人気", "D_着順": "ダービー着順"})
        st.dataframe(display, hide_index=True, use_container_width=True)
    st.download_button("分類別成績CSVを保存", _csv_bytes(summary), file_name="autumn_derby_summary.csv", mime="text/csv")
    st.download_button("全頭照合CSVを保存", _csv_bytes(joined), file_name="autumn_derby_matched.csv", mime="text/csv")
    st.info("この結果は過去の関連性であり、因果関係や将来の的中を保証しません。データの欠損や標本数に注意してください。")


if __name__ == "__main__":
    st.set_page_config(page_title="過去10年分析｜競馬分析ラボ", page_icon="📚", layout="wide")
    render_history_page()
