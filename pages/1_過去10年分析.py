"""競馬分析ラボ追加機能：ダービー成績 × 天皇賞（秋）過去10年検証。

導入：既存GitHubの pages/1_過去10年分析.py を全文置換。
現行のapp.py（分析メニュー対応版）は変更しない。

自動取得はnetkeibaの公開結果を参照。通信できない場合は手動CSV/Excelも使用可能。
CSV/Excelの入力は1行=1頭の出走実績。
カラム：年, 馬名, 人気, 着順（任意：馬ID, 単勝オッズ, 複勝払戻）。
実データの不明欄は埋めず、欠損として処理する。
"""
from __future__ import annotations

import io
import re
import unicodedata
import time

import requests
from bs4 import BeautifulSoup
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



def analyze_popularity(joined: pd.DataFrame):
    """秋天人気帯ごとの実績を、全出走馬を母数として集計。"""
    valid = joined.loc[joined["人気"].notna() & joined["着順"].ge(1)].copy()
    valid["秋天人気帯"] = pd.cut(
        valid["人気"], bins=[0, 3, 6, 9, float("inf")],
        labels=["1～3番人気", "4～6番人気", "7～9番人気", "10番人気以下"],
        include_lowest=True,
    )
    rows = []
    for (band, category), block in valid.groupby(["秋天人気帯", "ダービー分類"], observed=True):
        n = len(block)
        placed = int(block["着順"].le(3).sum())
        wins = int(block["着順"].eq(1).sum())
        winning_odds = block.loc[block["着順"].eq(1), "単勝オッズ"]
        roi = round(winning_odds.sum() * 100 / n, 1) if winning_odds.notna().all() else None
        rows.append({"秋天人気帯": str(band), "ダービー分類": category, "出走": n,
                     "1着": wins, "3着以内": placed,
                     "勝率(%)": round(wins * 100 / n, 1),
                     "複勝率(%)": round(placed * 100 / n, 1),
                     "単勝回収率(%)": roi})
    band_summary = []
    for band, block in valid.groupby("秋天人気帯", observed=True):
        n = len(block)
        band_summary.append({"秋天人気帯": str(band), "全頭数": n,
                             "3着以内": int(block["着順"].le(3).sum()),
                             "基準複勝率(%)": round(block["着順"].le(3).mean() * 100, 1)})
    return valid, pd.DataFrame(rows), pd.DataFrame(band_summary)


def show_popularity_analysis(joined: pd.DataFrame):
    st.markdown("#### 秋天人気別 × ダービー実績")
    st.caption("秋天で何番人気だったかを揃えて比較。好走馬だけを抽出せず、各条件の全出走馬を分母にします。")
    valid, cross, baselines = analyze_popularity(joined)
    invalid_count = len(joined) - len(valid)
    if invalid_count:
        st.warning(f"秋天人気または着順が欠損した{invalid_count}頭は、この分析から除外しています。")
    if valid.empty:
        st.warning("秋天人気と着順の両方が揃うデータがありません。")
        return
    st.markdown("**秋天人気帯ごとの全体基準**")
    st.dataframe(baselines, hide_index=True, use_container_width=True)
    st.markdown("**ダービー分類を加えた比較**")
    if cross.empty:
        st.warning("比較に使える分類がありません。")
        return
    st.dataframe(cross, hide_index=True, use_container_width=True)

    st.markdown("#### 穴馬傾向（秋天4～9番人気）")
    holes = valid[valid["人気"].between(4, 9)].copy()
    if holes.empty:
        st.info("秋天4～9番人気の記録がありません。")
        return
    base_n, base_p = len(holes), int(holes["着順"].le(3).sum())
    baseline = base_p / base_n * 100
    c1, c2, c3 = st.columns(3)
    c1.metric("4～9番人気の出走", f"{base_n}頭")
    c2.metric("3着以内", f"{base_p}頭")
    c3.metric("全体複勝率", f"{baseline:.1f}%")
    rows = []
    for category, group in holes.groupby("ダービー分類", dropna=False):
        n = len(group)
        count = int(group["着順"].le(3).sum())
        rate = 100 * count / n
        rows.append({"ダービー分類": category, "出走": n, "3着以内": count,
                     "複勝率(%)": round(rate, 1),
                     "全体との差(pt)": round(rate - baseline, 1),
                     "信頼性": "少数（10頭未満）" if n < 10 else "要継続検証"})
    comparison = pd.DataFrame(rows).sort_values(["複勝率(%)", "出走"], ascending=[False, False])
    st.dataframe(comparison, hide_index=True, use_container_width=True)
    st.caption("『全体との差』は同じ秋天4～9番人気全体との単純比較です。統計的な有意差や因果効果を意味しません。")

    st.markdown("**実際に4～9番人気で3着以内に入った馬**")
    hits = holes[holes["着順"].le(3)].sort_values(["年", "着順"], ascending=[False, True])
    show_cols = ["年", "馬名", "人気", "着順", "D_年", "D_人気", "D_着順", "ダービー分類", "単勝オッズ"]
    names = {"人気": "秋天人気", "着順": "秋天着順", "D_年": "ダービー年",
             "D_人気": "ダービー人気", "D_着順": "ダービー着順"}
    st.dataframe(hits[show_cols].rename(columns=names), hide_index=True, use_container_width=True)
    st.download_button("人気別×ダービー分類の集計CSV", _csv_bytes(cross),
                       file_name="autumn_derby_popularity.csv", mime="text/csv", key="history_popularity_csv")
    st.download_button("穴馬条件の集計CSV", _csv_bytes(comparison),
                       file_name="autumn_derby_longshots.csv", mime="text/csv", key="history_longshots_csv")
    st.warning("『ダービー出走歴なし/未収録』には取得漏れの可能性があります。複勝払戻がない場合、複勝回収率は算出しません。少数標本の高複勝率を過信しないでください。")



def analyze_popularity_gap(joined: pd.DataFrame, threshold: int = 3, autumn_min: int = 4,
                           autumn_max: int = 9):
    """同一馬のダービー人気から秋天人気への変化を分析する。

    人気落差 = 秋天人気 - ダービー人気（正なら人気低下）。
    ダービー人気が不明な馬は除外し、着順不明も除外する。
    """
    candidates = joined.loc[
        joined["人気"].notna() & joined["着順"].ge(1)
        & joined["D_人気"].notna() & joined["D_着順"].ge(1)
    ].copy()
    candidates["人気落差"] = candidates["人気"] - candidates["D_人気"]
    candidates["落差区分"] = candidates["人気落差"].apply(
        lambda v: f"{threshold}段階以上人気低下" if v >= threshold
        else (f"{threshold}段階以上人気上昇" if v <= -threshold else "ほぼ同程度"))
    candidates["秋天3着以内"] = candidates["着順"].le(3)
    candidates["ダービー3着以内"] = candidates["D_着順"].le(3)
    candidates["秋天人気帯"] = pd.cut(candidates["人気"],
                                bins=[0, 3, 6, 9, float("inf")],
                                labels=["1～3番人気", "4～6番人気", "7～9番人気", "10番人気以下"])

    def aggregate(df, fields):
        result = []
        for key, block in df.groupby(fields, observed=True, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            n = len(block)
            hits = int(block["秋天3着以内"].sum())
            wins = int(block["着順"].eq(1).sum())
            vals = {field: str(v) for field, v in zip(fields, key)}
            vals.update({"出走": n, "1着": wins, "3着以内": hits,
                         "複勝率(%)": round(100 * hits / n, 1)})
            win_odds = block.loc[block["着順"].eq(1), "単勝オッズ"]
            vals["単勝回収率(%)"] = round(100 * win_odds.sum() / n, 1) if win_odds.notna().all() else None
            result.append(vals)
        return pd.DataFrame(result)

    whole = aggregate(candidates, ["落差区分"])
    focus = candidates[candidates["人気"].between(autumn_min, autumn_max)].copy()
    focus_summary = aggregate(focus, ["落差区分", "ダービー3着以内"])
    band_summary = aggregate(candidates, ["秋天人気帯", "落差区分"])
    return candidates, whole, focus, focus_summary, band_summary


def show_popularity_gap_analysis(joined: pd.DataFrame):
    st.markdown("#### ダービー人気 → 秋天人気の落差分析")
    st.caption("人気落差＝秋天人気順位－ダービー人気順位。プラスならダービー時より評価が低下した馬。人気順位の差は確率差ではありません。")
    c1, c2, c3 = st.columns(3)
    with c1:
        threshold = st.number_input("人気落差の判定幅", min_value=1, max_value=15, value=3, step=1,
                                    key="derby_gap_threshold")
    with c2:
        autumn_min = st.number_input("穴馬・人気下限", min_value=1, max_value=18, value=4, step=1,
                                     key="derby_gap_min")
    with c3:
        autumn_max = st.number_input("穴馬・人気上限", min_value=1, max_value=18, value=9, step=1,
                                     key="derby_gap_max")
    if autumn_min > autumn_max:
        st.warning("人気の下限は上限以下にしてください。")
        return
    matched, whole, focus, focus_summary, by_band = analyze_popularity_gap(
        joined, int(threshold), int(autumn_min), int(autumn_max))
    if matched.empty:
        st.warning("ダービーと秋天の両方の人気・着順が揃う馬がいません。")
        return
    excluded = len(joined) - len(matched)
    m1, m2, m3 = st.columns(3)
    m1.metric("比較可能", f"{len(matched)}頭")
    m2.metric("除外・未照合", f"{excluded}頭")
    m3.metric("対象の秋天人気帯", f"{int(autumn_min)}～{int(autumn_max)}番人気")
    st.caption("除外にはダービー未出走・未収録・人気や着順の欠損を含みます。母集団は照合できた馬のみです。")

    st.markdown("**人気の上昇・低下別成績（全人気）**")
    st.dataframe(whole, hide_index=True, use_container_width=True)
    with st.expander("秋天人気帯を揃えて比較", expanded=False):
        st.dataframe(by_band, hide_index=True, use_container_width=True)

    st.markdown(f"**秋天{int(autumn_min)}～{int(autumn_max)}番人気 × ダービー好走歴**")
    if focus.empty:
        st.info("指定した秋天人気帯でダービー実績を照合できた馬はいません。")
        return
    baseline = focus["秋天3着以内"].mean() * 100
    focus_summary["同人気帯との差(pt)"] = (focus_summary["複勝率(%)"] - baseline).round(1)
    st.caption(f"この表の比較基準は『同じ秋天人気帯かつダービー照合済み』の{len(focus)}頭（複勝率{baseline:.1f}%）。")
    st.dataframe(focus_summary, hide_index=True, use_container_width=True)
    st.caption("ダービー3着以内は人気とは独立した分類。『True』は3着以内、『False』は4着以下。")
    if (focus_summary["出走"] < 10).any():
        st.warning("10頭未満のグループがあります。少数例の複勝率は偶然で大きく変動します。")

    st.markdown("**人気を落として秋天で3着以内に入った馬**")
    hits = focus[focus["人気落差"].ge(threshold) & focus["秋天3着以内"]].copy()
    columns = ["年", "馬名", "人気", "着順", "D_年", "D_人気", "D_着順", "人気落差", "単勝オッズ"]
    names = {"人気": "秋天人気", "着順": "秋天着順", "D_年": "ダービー年",
             "D_人気": "ダービー人気", "D_着順": "ダービー着順"}
    if hits.empty:
        st.info("指定条件に該当する好走馬はありません。")
    else:
        st.dataframe(hits[columns].rename(columns=names), hide_index=True, use_container_width=True)
    with st.expander("比較可能な全頭の人気落差", expanded=False):
        st.dataframe(matched[columns + ["落差区分", "秋天人気帯"]].rename(columns=names),
                     hide_index=True, use_container_width=True)
    st.download_button("人気落差・全頭データCSV", _csv_bytes(matched),
                       file_name="autumn_derby_popularity_gap.csv", mime="text/csv", key="history_gap_csv")
    st.download_button("人気落差・穴馬条件集計CSV", _csv_bytes(focus_summary),
                       file_name="autumn_derby_gap_longshot_summary.csv", mime="text/csv", key="history_gap_summary_csv")
    st.info("これは記述統計であり、過去の人気変動が将来の好走を引き起こすとは限りません。ダービー未出走・未収録は区別できず、年齢や秋天人気などの交絡要因にも注意してください。")


def _csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8-sig")




# netkeibaの重賞特集ページにある過去レース一覧から実際のレースIDを取得する。
# HTML変更、アクセス制限、取得漏れは「失敗」として表示し、推測のIDや成績は補わない。
SPECIAL_URLS = {
    "秋天": "https://race.netkeiba.com/special/index.html?id=0109",
    "ダービー": "https://race.netkeiba.com/special/index.html?id=0059",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
}


def _request_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=18)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return BeautifulSoup(response.text, "html.parser")


def _discover_ids(session, label, start, end):
    soup = _request_soup(session, SPECIAL_URLS[label])
    found = {}
    # 特集ページの「過去のレース結果」テーブルを抽出。
    title = next((h for h in soup.find_all(["h2", "h3", "h4"])
                  if "過去のレース結果" in h.get_text(" ", strip=True)), None)
    table = title.find_next("table") if title else None
    if table is None:
        raise ValueError("重賞特集から過去レース一覧を検出できません（サイト構成変更の可能性）")
    for tr in table.select("tr"):
        for a in tr.select('a[href*="/race/"]'):
            match = re.search(r"/race/(\d{12})(?:/|[?#]|$)", a.get("href", ""))
            if not match:
                continue
            race_id = match.group(1)
            year = int(race_id[:4])
            if start <= year <= end:
                found[year] = race_id
                break
    return found


def _number_or_none(value):
    m = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _parse_result(soup, race_id, label):
    # 別レースへの誤接続を回避。タイトルとIDの両方を検証。
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = soup.select_one("h1.RaceName") or soup.find("h1")
    name = (heading.get_text(" ", strip=True) if heading else "") + " " + title
    if label == "秋天" and not ("天皇賞" in name and "秋" in name):
        raise ValueError("指定IDのページが天皇賞・秋ではありません")
    if label == "ダービー" and not ("日本ダービー" in name or "東京優駿" in name):
        raise ValueError("指定IDのページが日本ダービーではありません")
    rows = []
    table = soup.select_one("table.RaceTable01")
    if table is None:
        raise ValueError("レース結果テーブルが見つかりません")
    for tr in table.select("tr.HorseList"):
        cols = tr.find_all("td", recursive=False)
        if len(cols) < 11:
            continue
        # netkeiba PC結果表：着順/枠/馬番/馬名/性齢/斤量/騎手/タイム/着差/人気/単勝オッズ
        finish = _number_or_none(cols[0].get_text(" ", strip=True))
        horse = cols[3].select_one('a[href*="/horse/"]')
        if horse is None:
            continue
        name = _normalize_name(horse.get_text(" ", strip=True))
        pop = _number_or_none(cols[9].get_text(" ", strip=True))
        odds = _number_or_none(cols[10].get_text(" ", strip=True))
        number = _number_or_none(cols[2].get_text(" ", strip=True))
        horse_href = horse.get("href", "")
        horse_id = re.search(r"/horse/(\d{10})(?:/|[?#]|$)", horse_href)
        rows.append({"年": int(race_id[:4]), "馬名": name,
                     "馬ID": horse_id.group(1) if horse_id else "",
                     "人気": pop, "着順": finish, "単勝オッズ": odds,
                     "複勝払戻": None, "馬番": int(number) if number is not None else None})
    if len(rows) < 8:
        raise ValueError(f"出走馬が{len(rows)}頭しか取得できませんでした")
    # 人気・着順の欠損が多い場合は取得成功扱いしない。
    if sum(x["人気"] is not None for x in rows) < len(rows) - 1:
        raise ValueError("人気データが欠損しています")
    if sum(x["着順"] is not None for x in rows) < 8:
        raise ValueError("着順データが不足しています")
    # 払戻表はHTML構造に差があるため、完全に対応づけられた場合のみ採用。
    for paytable in soup.select("table.PayTable01"):
        for tr in paytable.select("tr"):
            first = tr.find(["th", "td"])
            if first is None or first.get_text(" ", strip=True) != "複勝":
                continue
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 3:
                continue
            numbers = [int(z) for z in re.findall(r"\d+", cells[1].get_text(" ", strip=True))]
            payouts = [int(z.replace(",", "")) for z in re.findall(r"[\d,]+", cells[2].get_text(" ", strip=True))]
            if len(numbers) == len(payouts) and 1 <= len(numbers) <= 3:
                mapping = dict(zip(numbers, payouts))
                for row in rows:
                    if row["馬番"] in mapping and row["着順"] is not None and row["着順"] <= 3:
                        row["複勝払戻"] = mapping[row["馬番"]]
    return rows


def fetch_history_auto(start_year, end_year, status_callback=None):
    """ネット取得結果と取得ログを返す。未取得年の推測補完は禁止。"""
    if not 2000 <= start_year <= end_year <= 2025:
        raise ValueError("自動取得は2000〜2025年の確定済み結果に対応します")
    session = requests.Session()
    # 対象期間の秋天出走馬のダービー経験を見たいので、2007年以降まで遡る。
    derby_start = max(2000, start_year - 9)
    race_maps = {}
    logs = []
    for label, begin in (("秋天", start_year), ("ダービー", derby_start)):
        try:
            race_maps[label] = _discover_ids(session, label, begin, end_year)
        except Exception as exc:
            race_maps[label] = {}
            logs.append({"対象": label, "年": "一覧", "状況": "失敗", "詳細": str(exc)})
    output = {"秋天": [], "ダービー": []}
    targets = [(label, year) for label, begin in (("秋天", start_year), ("ダービー", derby_start))
               for year in range(begin, end_year + 1)]
    for idx, (label, year) in enumerate(targets, 1):
        race_id = race_maps[label].get(year)
        if race_id is None:
            logs.append({"対象": label, "年": year, "状況": "失敗", "詳細": "特集ページにレースIDなし"})
        else:
            try:
                url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
                records = _parse_result(_request_soup(session, url), race_id, label)
                output[label].extend(records)
                logs.append({"対象": label, "年": year, "状況": "成功", "詳細": f"{len(records)}頭 / {race_id}"})
            except Exception as exc:
                logs.append({"対象": label, "年": year, "状況": "失敗", "詳細": str(exc)[:180]})
            time.sleep(0.35)
        if status_callback is not None:
            status_callback(idx, len(targets), label, year)
    return pd.DataFrame(output["秋天"], columns=DATA_COLS + ["馬番"]), pd.DataFrame(
        output["ダービー"], columns=DATA_COLS + ["馬番"]), pd.DataFrame(logs)


def render_history_page():
    """既存のStreamlitページから選択時だけ呼び出す。通信・既存処理に非依存。"""
    st.subheader("過去10年・ダービー実績 × 天皇賞・秋")
    st.caption("上位人気・好走、上位人気・凡走、低人気・好走を検証。秋天の全出走馬を分母にします。")
    with st.expander("入力ファイルの形式と注意", expanded=False):
        st.write("天皇賞・秋と日本ダービーを別々のCSV/Excelで用意してください。どちらも1行1頭。必須列は「年・馬名・人気・着順」。")
        st.write("任意列は「馬ID・単勝オッズ・複勝払戻」。複勝払戻は的中馬について100円あたりの円額を入力。")
        st.write("天皇賞・秋は対象の10年分の全出走馬、日本ダービーはその馬たちが出た年まで遡って収録してください。")
        st.warning("未収録とダービー未出走はCSVだけでは区別できません。集計で両者をまとめ、網羅性を確認してください。")
    mode = st.radio("データの準備方法", ["自動取得（netkeiba）", "CSV・Excelをアップロード"], horizontal=True, key="history_input_mode")
    autumn_file = derby_file = None
    if mode == "CSV・Excelをアップロード":
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
    if mode == "自動取得（netkeiba）":
        st.caption("netkeibaの重賞特集・結果ページから取得。初回は複数ページへのアクセスが必要です。取得失敗年は補完せず明示します。")
        if st.button("過去データを自動取得して分析", type="primary", key="history_fetch_button"):
            progress = st.progress(0, text="レース結果の取得を開始します")
            def on_progress(index, total, label, year):
                progress.progress(index / total, text=f"{label} {year}年を確認 ({index}/{total})")
            with st.spinner("レース結果を取得中"):
                try:
                    fetched = fetch_history_auto(int(last_year - year_count + 1), int(last_year), on_progress)
                    st.session_state["history_auto_cache"] = (int(last_year), int(year_count), fetched)
                except Exception as exc:
                    st.error(f"自動取得エラー：{exc}")
            progress.empty()
        cache = st.session_state.get("history_auto_cache")
        if not cache or cache[:2] != (int(last_year), int(year_count)):
            st.info("上のボタンを押すとデータ取得を開始します。")
            return
        a, d, logs = cache[2]
        with st.expander("取得状況・失敗年を確認", expanded=bool((logs["状況"] == "失敗").any())):
            st.dataframe(logs, hide_index=True, use_container_width=True)
        succeeded = logs[(logs["対象"] == "秋天") & (logs["状況"] == "成功")]
        if len(succeeded) != int(year_count):
            st.error("天皇賞・秋の全対象年が揃っていません。誤解を防ぐため、集計を停止します。")
            return
        if d.empty:
            st.error("日本ダービーの結果を取得できませんでした。集計を停止します。")
            return
        if ((logs["対象"] == "ダービー") & (logs["状況"] == "失敗")).any():
            st.warning("ダービーの取得に欠損年があります。未照合は『未出走』とは区別できず、推定される分類に注意してください。")
        st.download_button("取得した秋天データを保存", _csv_bytes(a), "autumn_auto.csv", "text/csv")
        st.download_button("取得したダービーデータを保存", _csv_bytes(d), "derby_auto.csv", "text/csv")
    else:
        if not (autumn_file and derby_file):
            st.info("2つの結果ファイルを読み込むと、分類と複勝率・回収率を表示します。")
            return
        try:
            a = _load_table(autumn_file)
            d = _load_table(derby_file)
        except Exception as exc:
            st.error(f"ファイル読み込みエラー：{exc}")
            return
    try:
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
    show_popularity_analysis(joined)
    show_popularity_gap_analysis(joined)
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
