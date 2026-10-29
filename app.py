
import streamlit as st
import pandas as pd
from urllib.parse import urlparse, parse_qs

st.set_page_config(
    page_title="競馬分析ラボ",
    page_icon="🏇",
    layout="wide"
)

st.title("🏇 競馬分析ラボ Ver.10")
st.caption("出走馬の過去成績を比較・分析")

# ===== レース指定 =====

st.header("① レースを指定")

race_input = st.text_input(
    "netkeiba出馬表URL または12桁のレースID",
    placeholder="レースID・URLを貼り付け"
)

race_id = ""

if race_input.strip():
    value = race_input.strip()

    if value.isdigit() and len(value) == 12:
        race_id = value
    else:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()

        if host == "netkeiba.com" or host.endswith(".netkeiba.com"):
            candidate = parse_qs(
                parsed.query
            ).get("race_id", [""])[0]

            if candidate.isdigit() and len(candidate) == 12:
                race_id = candidate

    if race_id:
        st.success(f"レースID：{race_id}")
    else:
        st.warning("URLか12桁のレースIDを確認してください。")

# ===== 出走馬リスト =====

st.header("② 出走馬を指定")

tenno_horses = [
    "エルトンバローズ",
    "クロワデュノール",
    "コスモキュランダ",
    "サクラファレル",
    "シェイクユアハート",
    "シャンパンカラー",
    "ジュンブロッサム",
    "ジョバンニ",
    "セイウンハーデス",
    "タガノデュード",
    "ダノンデサイル",
    "ネブラディスク",
    "ホウオウビスケッツ",
    "マスカレードボール",
    "リアライズシリウス",
    "レガレイラ",
    "レディネス"
]

preset = (
    "\n".join(tenno_horses)
    if race_id == "202605041111"
    else ""
)

horse_text = st.text_area(
    "出走馬の名前（1行に1頭）",
    value=preset,
    height=180,
    help="別のレースなら出走馬名を改行区切りで貼り付け"
)

target_horses = list(dict.fromkeys(
    name.strip()
    for name in horse_text.splitlines()
    if name.strip()
))

if target_horses:
    st.info(f"指定した出走馬：{len(target_horses)}頭")
else:
    st.caption("未入力ならExcel内の全馬を分析します。")

st.caption(
    "現在、URLによる出走馬の自動取得は未対応です。"
)

# ===== Excel読み込み =====

st.header("③ 過去成績を読み込む")

uploaded = st.file_uploader(
    "分析データを選択",
    type=["xlsx", "csv"]
)

if uploaded is None:
    st.info("過去成績のExcel・CSVをアップロードしてください。")
    st.stop()

try:
    if uploaded.name.lower().endswith(".csv"):
        try:
            df = pd.read_csv(
                uploaded,
                encoding="utf-8-sig"
            )
        except UnicodeDecodeError:
            uploaded.seek(0)
            df = pd.read_csv(
                uploaded,
                encoding="cp932"
            )
    else:
        book = pd.ExcelFile(uploaded)
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
                df = df.rename(
                    columns={candidate: target}
                )
                break

if not {"馬名", "着順_数値"}.issubset(df.columns):
    st.error("Excelに馬名または着順の列がありません。")
    st.write(list(df.columns))
    st.stop()

for col in [
    "距離_m",
    "着順_数値",
    "上がり3F_秒",
    "4角順位",
    "上がり順位"
]:
    if col not in df.columns:
        df[col] = float("nan")

    df[col] = pd.to_numeric(
        df[col], errors="coerce"
    )

for col in ["競馬場", "馬場状態"]:
    if col not in df.columns:
        df[col] = "不明"

    df[col] = df[col].fillna(
        "不明"
    ).astype(str).str.strip()

df["馬名"] = df["馬名"].astype(str).str.strip()
df = df[
    df["馬名"].ne("") &
    df["馬名"].ne("nan")
].copy()

if "日付" in df.columns:
    df["日付"] = pd.to_datetime(
        df["日付"], errors="coerce"
    )

total_rows = len(df)
total_horses = df["馬名"].nunique()

st.success(
    f"読み込み成功：{total_rows}走 / {total_horses}頭"
)

# 指定した出走馬に限定
if target_horses:
    available = set(df["馬名"].unique())

    missing = [
        horse for horse in target_horses
        if horse not in available
    ]

    df = df[
        df["馬名"].isin(target_horses)
    ].copy()

    st.write(
        f"出走馬との照合結果：{df['馬名'].nunique()}頭"
    )

    if missing:
        st.warning(
            "過去成績が見つからない馬："
            + "、".join(missing)
        )

if df.empty:
    st.error(
        "指定した馬の過去成績がありません。"
        "別のExcelか馬名を確認してください。"
    )
    st.stop()

# ===== 分析条件 =====

st.header("④ 分析条件")

mode = st.radio(
    "分析モード",
    ["ランキング", "馬を比較"],
    horizontal=True
)

venue = st.selectbox(
    "競馬場",
    ["すべて"] + sorted(df["競馬場"].unique())
)

track = st.selectbox(
    "馬場状態",
    ["すべて"] + sorted(df["馬場状態"].unique())
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
    metric = st.selectbox(
        "ランキング指標", metrics
    )
else:
    horse_options = sorted(df["馬名"].unique())

    selected = st.multiselect(
        "比較する馬（2〜3頭）",
        horse_options,
        default=horse_options[:2],
        max_selections=3
    )

view = st.radio(
    "表示方法",
    ["スマホ用カード", "横並び表"],
    horizontal=True
)

# ===== 絞り込み =====

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
            "日付",
            ascending=False,
            kind="stable"
        )

    if recent != "全走":
        n = int(
            recent.replace("直近", "").replace("走", "")
        )

        data = data.groupby(
            "馬名", sort=False
        ).head(n)

    return data

# ===== 集計 =====

def summarize(group):
    finish = group["着順_数値"].dropna()
    agari = group["上がり順位"].dropna()

    def rate(count, n):
        return (
            round(count / n * 100, 2)
            if n else float("nan")
        )

    return {
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
        "上がり対象走数": len(agari),
        "上がり最速率(%)": rate(
            (agari == 1).sum(), len(agari)
        ),
        "上がり3位以内率(%)": rate(
            (agari <= 3).sum(), len(agari)
        ),
        "平均上がり順位": agari.mean()
    }

# ===== 分析実行 =====

if st.button(
    "分析する",
    type="primary",
    use_container_width=True
):
    filtered = filter_data(df)

    if mode == "馬を比較":
        if len(selected) < 2:
            st.warning("2頭以上選んでください。")
            st.stop()

        filtered = filtered[
            filtered["馬名"].isin(selected)
        ]

    if filtered.empty:
        st.warning("条件に一致するデータがありません。")
        st.stop()

    rows = []

    for horse, group in filtered.groupby("馬名"):
        row = summarize(group)
        row["馬名"] = horse
        rows.append(row)

    result = pd.DataFrame(rows)

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
        )

        result["順位"] = result[metric].rank(
            method="min",
            ascending=ascending,
            na_option="bottom"
        ).astype(int)

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
                label = f"🐎 {row['馬名']}"

                if mode == "ランキング":
                    label = (
                        f"{int(row['順位'])}位 {label}"
                    )

                st.markdown(f"### {label}")
                st.caption(
                    f"対象：{int(row['出走数'])}走"
                )

                for key in metrics:
                    val = row[key]

                    if pd.isna(val):
                        shown = "データなし"
                    else:
                        shown = f"{val:.2f}"

                        if key.endswith("(%)"):
                            shown += "%"

                    st.write(f"**{key}：** {shown}")

                st.caption(
                    "上がり順位の割合は、"
                    f"記録のある{int(row['上がり対象走数'])}走が分母"
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
