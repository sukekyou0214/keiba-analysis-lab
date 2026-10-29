
import streamlit as st
import pandas as pd
from urllib.parse import urlparse, parse_qs

st.set_page_config(
    page_title="競馬分析ラボ",
    page_icon="🏇",
    layout="wide"
)

st.title("🏇 競馬分析ラボ Ver.9")
st.caption("出走馬の過去成績を比較・分析するWebアプリ")

# =================================
# レースURL・レースIDの入力
# =================================

st.subheader("レースを指定")

race_url = st.text_input(
    "netkeibaの出馬表URL、または12桁のレースID",
    placeholder="https://race.sp.netkeiba.com/race/shutuba.html?race_id=..."
)

if race_url:
    race_url = race_url.strip()
    race_id = None

    if race_url.isdigit() and len(race_url) == 12:
        race_id = race_url
    else:
        parsed = urlparse(race_url)
        host = (parsed.hostname or "").lower()

        if host == "netkeiba.com" or host.endswith(".netkeiba.com"):
            candidate = parse_qs(parsed.query).get("race_id", [""])[0]

            if candidate.isdigit() and len(candidate) == 12:
                race_id = candidate

    if race_id:
        st.success(f"レースIDを認識しました：{race_id}")
        st.info("出走馬の自動取得機能は現在準備中です。")
    else:
        st.warning("正しい出馬表URLまたはレースIDを入力してください。")

st.divider()

# =================================
# Excelデータの読み込み
# =================================

st.subheader("過去成績データ")

uploaded_file = st.file_uploader(
    "分析データを選択",
    type=["xlsx", "csv"]
)

if uploaded_file is None:
    st.info("ExcelまたはCSVをアップロードすると分析できます。")
    st.stop()

try:
    if uploaded_file.name.lower().endswith(".csv"):
        try:
            df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, encoding="cp932")
    else:
        book = pd.ExcelFile(uploaded_file)
        sheet = (
            "分析用296走"
            if "分析用296走" in book.sheet_names
            else book.sheet_names[0]
        )
        df = pd.read_excel(book, sheet_name=sheet)
except Exception as e:
    st.error(f"読み込みエラー：{e}")
    st.stop()

df.columns = df.columns.astype(str).str.strip()

aliases = {
    "馬名": ["馬名", "馬名_正規化"],
    "競馬場": ["競馬場", "場所", "開催場"],
    "馬場状態": ["馬場状態", "馬 場", "馬場"],
    "距離_m": ["距離_m", "距離", "距離(m)"],
    "着順_数値": ["着順_数値", "着順"],
    "上がり3F_秒": [
        "上がり3F_秒", "上がり3F", "上り", "上がり"
    ],
    "4角順位": ["4角順位", "4角", "4角通過順位"],
    "上がり順位": ["上がり順位", "上り順位"],
    "日付": ["日付", "年月日", "開催日", "レース日"]
}

for target, candidates in aliases.items():
    if target not in df.columns:
        for candidate in candidates:
            if candidate in df.columns:
                df = df.rename(columns={candidate: target})
                break

required = ["馬名", "着順_数値"]
missing = [c for c in required if c not in df.columns]

if missing:
    st.error("必要な列がありません：" + ", ".join(missing))
    st.write("読み込んだ列名：", list(df.columns))
    st.stop()

numeric_cols = [
    "距離_m",
    "着順_数値",
    "上がり3F_秒",
    "4角順位",
    "上がり順位"
]

for col in numeric_cols:
    if col not in df.columns:
        df[col] = float("nan")
    df[col] = pd.to_numeric(df[col], errors="coerce")

for col in ["競馬場", "馬場状態"]:
    if col not in df.columns:
        df[col] = "不明"
    df[col] = df[col].fillna("不明").astype(str).str.strip()

df["馬名"] = df["馬名"].astype(str).str.strip()
df = df[
    df["馬名"].ne("") & df["馬名"].ne("nan")
].copy()

if "日付" in df.columns:
    df["日付"] = pd.to_datetime(
        df["日付"], errors="coerce"
    )

horses = sorted(df["馬名"].unique().tolist())

st.success(
    f"データ読み込み成功！ {len(df)}走 / {len(horses)}頭"
)

# =================================
# 分析条件
# =================================

st.divider()
st.subheader("分析条件")

mode = st.radio(
    "分析モード",
    ["ランキング", "馬を比較"],
    horizontal=True
)

c1, c2 = st.columns(2)

with c1:
    venue = st.selectbox(
        "競馬場",
        ["すべて"] + sorted(df["競馬場"].unique().tolist())
    )

    distance = st.selectbox(
        "距離",
        [
            "すべて",
            "1200m以下",
            "1201〜1600m",
            "1601〜1800m",
            "1801〜2200m",
            "2201〜2400m",
            "2401m以上"
        ]
    )

with c2:
    track = st.selectbox(
        "馬場状態",
        ["すべて"] + sorted(df["馬場状態"].unique().tolist())
    )

    recent = st.selectbox(
        "対象レース",
        ["直近5走", "直近10走", "直近15走", "全走"]
    )

metrics = [
    "勝率(%)",
    "連対率(%)",
    "複勝率(%)",
    "平均着順",
    "平均4角",
    "平均上がり",
    "上がり最速率(%)",
    "上がり3位以内率(%)",
    "平均上がり順位"
]

if mode == "ランキング":
    metric = st.selectbox("ランキング指標", metrics)
else:
    selected = st.multiselect(
        "比較する馬（2〜3頭）",
        horses,
        default=horses[:min(2, len(horses))],
        max_selections=3
    )

view = st.radio(
    "表示方法",
    ["スマホ用カード", "横並び表"],
    horizontal=True
)

# =================================
# データ絞り込み
# =================================

def filter_data(data):
    data = data.copy()

    if venue != "すべて":
        data = data[data["競馬場"] == venue]

    if track != "すべて":
        data = data[data["馬場状態"] == track]

    ranges = {
        "1200m以下": (0, 1200),
        "1201〜1600m": (1201, 1600),
        "1601〜1800m": (1601, 1800),
        "1801〜2200m": (1801, 2200),
        "2201〜2400m": (2201, 2400),
        "2401m以上": (2401, float("inf"))
    }

    if distance in ranges:
        low, high = ranges[distance]
        data = data[
            data["距離_m"].between(low, high)
        ]

    if "日付" in data.columns:
        data = data.sort_values(
            "日付", ascending=False, kind="stable"
        )

    if recent != "全走":
        n = int(
            recent.replace("直近", "").replace("走", "")
        )
        data = data.groupby(
            "馬名", sort=False
        ).head(n)

    return data

# =================================
# 集計処理
# =================================

def summarize(group):
    finish = group["着順_数値"].dropna()
    agari_rank = group["上がり順位"].dropna()

    def rate(count, denominator):
        if denominator == 0:
            return float("nan")
        return round(count / denominator * 100, 2)

    return pd.Series({
        "出走数": len(group),
        "平均着順": finish.mean(),
        "平均4角": group["4角順位"].mean(),
        "平均上がり": group["上がり3F_秒"].mean(),
        "勝率(%)": rate(
            (finish == 1).sum(), len(finish)
        ),
        "連対率(%)": rate(
            (finish <= 2).sum(), len(finish)
        ),
        "複勝率(%)": rate(
            (finish <= 3).sum(), len(finish)
        ),
        "上がり対象走数": len(agari_rank),
        "上がり最速率(%)": rate(
            (agari_rank == 1).sum(), len(agari_rank)
        ),
        "上がり3位以内率(%)": rate(
            (agari_rank <= 3).sum(), len(agari_rank)
        ),
        "平均上がり順位": agari_rank.mean()
    })

# =================================
# 分析結果
# =================================

if st.button(
    "分析する",
    type="primary",
    use_container_width=True
):
    filtered = filter_data(df)

    if filtered.empty:
        st.warning("条件に一致するレースがありません。")
        st.stop()

    if mode == "馬を比較":
        if len(selected) < 2:
            st.warning("比較する馬を2頭以上選んでください。")
            st.stop()

        filtered = filtered[
            filtered["馬名"].isin(selected)
        ]

    results = []

    for horse, group in filtered.groupby("馬名"):
        row = summarize(group).to_dict()
        row["馬名"] = horse
        results.append(row)

    result = pd.DataFrame(results)

    if result.empty:
        st.warning("分析可能なデータがありません。")
        st.stop()

    st.divider()

    if mode == "ランキング":
        ascending = metric in [
            "平均着順",
            "平均4角",
            "平均上がり",
            "平均上がり順位"
        ]

        result = result.sort_values(
            metric,
            ascending=ascending,
            na_position="last",
            kind="stable"
        ).reset_index(drop=True)

        result.insert(
            0,
            "順位",
            result[metric].rank(
                method="min",
                ascending=ascending,
                na_option="bottom"
            ).astype(int)
        )

        st.subheader(f"🏆 {metric} ランキング")

    else:
        result["表示順"] = result["馬名"].apply(
            selected.index
        )
        result = result.sort_values(
            "表示順"
        ).drop(columns="表示順")

        st.subheader("🐎 馬の成績比較")

    result = result.round(2)

    if view == "横並び表":
        if mode == "馬を比較":
            st.dataframe(
                result.set_index("馬名").T,
                use_container_width=True
            )
        else:
            st.dataframe(
                result,
                hide_index=True,
                use_container_width=True
            )

    else:
        for _, row in result.iterrows():
            with st.container(border=True):
                heading = f"🐎 {row['馬名']}"

                if mode == "ランキング":
                    heading = (
                        f"{int(row['順位'])}位  {heading}"
                    )

                st.markdown(f"### {heading}")
                st.caption(
                    f"対象：{int(row['出走数'])}走"
                )

                for key in metrics:
                    value = row[key]

                    if pd.isna(value):
                        shown = "データなし"
                    else:
                        shown = f"{value:.2f}"

                        if key.endswith("(%)"):
                            shown += "%"

                    st.write(
                        f"**{key}：** {shown}"
                    )

    csv_data = result.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "分析結果をCSVで保存",
        data=csv_data,
        file_name="keiba_analysis.csv",
        mime="text/csv",
        use_container_width=True
    )
