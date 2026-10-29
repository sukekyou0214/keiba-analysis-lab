
import streamlit as st
import pandas as pd
from io import BytesIO
from urllib.parse import urlparse, parse_qs

st.set_page_config(
    page_title="競馬分析ラボ",
    page_icon="🏇",
    layout="wide"
)

st.title("🏇 競馬分析ラボ Ver.11")
st.caption("過去成績を蓄積・統合・比較するWebアプリ")

# ======================================
# ① レース指定
# ======================================

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
        st.warning("正しいURLまたは12桁のレースIDを入力してください。")

# ======================================
# ② 出走馬の指定
# ======================================

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
    "出走馬名（1行に1頭）",
    value=preset,
    height=170
)

target_horses = list(dict.fromkeys(
    name.strip()
    for name in horse_text.splitlines()
    if name.strip()
))

if target_horses:
    st.info(f"指定した出走馬：{len(target_horses)}頭")
else:
    st.caption("未入力の場合は全馬を分析します。")

st.caption(
    "出走馬名の自動取得はまだ未実装です。"
)

# ======================================
# ③ データ読み込み・統合
# ======================================

st.header("③ 成績データを読み込む")

st.write(
    "複数のExcel・CSVをまとめて選択できます。"
    "過去の統合ファイルと新しい成績を組み合わせることもできます。"
)

uploads = st.file_uploader(
    "分析データを選択（複数選択可）",
    type=["xlsx", "csv"],
    accept_multiple_files=True
)

if not uploads:
    st.info(
        "まず過去成績のExcel・CSVをアップロードしてください。"
    )
    st.stop()

aliases = {
    "馬名": ["馬名", "馬名_正規化"],
    "競馬場": ["競馬場", "場所", "開催場"],
    "馬場状態": ["馬場状態", "馬 場", "馬場"],
    "距離_m": ["距離_m", "距離", "距離(m)"],
    "着順_数値": ["着順_数値", "着順"],
    "上がり3F_秒": [
        "上がり3F_秒", "上がり3F",
        "上り", "上がり"
    ],
    "4角順位": [
        "4角順位", "4角", "4角通過順位"
    ],
    "上がり順位": ["上がり順位", "上り順位"],
    "日付": ["日付", "年月日", "開催日", "レース日"]
}

numeric_cols = [
    "距離_m",
    "着順_数値",
    "上がり3F_秒",
    "4角順位",
    "上がり順位"
]

def read_and_prepare(file):
    file.seek(0)

    if file.name.lower().endswith(".csv"):
        try:
            data = pd.read_csv(
                file, encoding="utf-8-sig"
            )
        except UnicodeDecodeError:
            file.seek(0)
            data = pd.read_csv(
                file, encoding="cp932"
            )
    else:
        book = pd.ExcelFile(file)

        preferred = [
            "分析データ",
            "分析用296走"
        ]

        sheet = next(
            (
                name for name in preferred
                if name in book.sheet_names
            ),
            book.sheet_names[0]
        )

        data = pd.read_excel(
            book, sheet_name=sheet
        )

    data.columns = (
        data.columns.astype(str).str.strip()
    )

    for target, candidates in aliases.items():
        if target not in data.columns:
            for candidate in candidates:
                if candidate in data.columns:
                    data = data.rename(
                        columns={candidate: target}
                    )
                    break

    required = ["馬名", "着順_数値"]

    missing = [
        name for name in required
        if name not in data.columns
    ]

    if missing:
        raise ValueError(
            "必要な列がありません："
            + "、".join(missing)
        )

    for col in numeric_cols:
        if col not in data.columns:
            data[col] = float("nan")

        data[col] = pd.to_numeric(
            data[col], errors="coerce"
        )

    for col in ["競馬場", "馬場状態"]:
        if col not in data.columns:
            data[col] = "不明"

        data[col] = (
            data[col]
            .fillna("不明")
            .astype(str)
            .str.strip()
        )

    data["馬名"] = (
        data["馬名"].astype(str).str.strip()
    )

    data = data[
        data["馬名"].ne("")
        & data["馬名"].ne("nan")
    ].copy()

    if "日付" in data.columns:
        data["日付"] = pd.to_datetime(
            data["日付"], errors="coerce"
        )

    return data

datasets = []

for file in uploads:
    try:
        data = read_and_prepare(file)
        datasets.append(data)

    except Exception as e:
        st.error(
            f"{file.name} の読み込みに失敗：{e}"
        )
        st.stop()

# 全データを統合
df = pd.concat(
    datasets, ignore_index=True, sort=False
)

before = len(df)

# 完全に一致する行のみ重複削除
df = df.drop_duplicates().reset_index(drop=True)

removed = before - len(df)

total_rows = len(df)
total_horses = df["馬名"].nunique()

st.success(
    f"統合完了！ {total_rows}走 / {total_horses}頭"
)

st.write(f"読み込んだファイル：{len(uploads)}個")
st.write(f"削除した完全重複行：{removed}行")

st.caption(
    "重複削除は、全列の内容が一致する行が対象です。"
    "表記や列構成が異なる同一レースは残る場合があります。"
)

# ======================================
# 統合データをExcelで保存
# ======================================

def make_excel(data):
    output = BytesIO()

    with pd.ExcelWriter(
        output, engine="openpyxl"
    ) as writer:
        data.to_excel(
            writer,
            sheet_name="分析データ",
            index=False
        )

    return output.getvalue()

excel_data = make_excel(df)

st.download_button(
    "📥 統合データをExcelで保存",
    data=excel_data,
    file_name="競馬分析_統合データ.xlsx",
    mime=(
        "application/vnd.openxmlformats-"
        "officedocument.spreadsheetml.sheet"
    ),
    use_container_width=True
)

st.caption(
    "このExcelをiPhoneに保存すれば、"
    "次回もアップロードして再利用できます。"
    "サーバーへの永続保存は行いません。"
)

# ======================================
# 出走馬との照合
# ======================================

st.header("④ 出走馬との照合")

analysis_df = df.copy()

if target_horses:
    available = set(
        analysis_df["馬名"].unique()
    )

    missing_horses = [
        name for name in target_horses
        if name not in available
    ]

    analysis_df = analysis_df[
        analysis_df["馬名"].isin(target_horses)
    ].copy()

    st.write(
        "照合できた出走馬："
        f"{analysis_df['馬名'].nunique()}頭"
    )

    if missing_horses:
        st.warning(
            "データが見つからない馬："
            + "、".join(missing_horses)
        )

else:
    st.write(
        f"全{analysis_df['馬名'].nunique()}頭を分析対象にします。"
    )

if analysis_df.empty:
    st.error("分析対象の馬の成績がありません。")
    st.stop()

# ======================================
# ⑤ 分析条件
# ======================================

st.header("⑤ 分析条件")

mode = st.radio(
    "分析モード",
    ["ランキング", "馬を比較"],
    horizontal=True
)

venue = st.selectbox(
    "競馬場",
    ["すべて"] + sorted(
        analysis_df["競馬場"].unique().tolist()
    )
)

track = st.selectbox(
    "馬場状態",
    ["すべて"] + sorted(
        analysis_df["馬場状態"].unique().tolist()
    )
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

st.caption(
    "直近5走などは、競馬場・馬場・距離で"
    "絞り込んだ後の直近走数です。"
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
    horse_options = sorted(
        analysis_df["馬名"].unique().tolist()
    )

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

# ======================================
# 絞り込み
# ======================================

def filter_data(data):
    data = data.copy()

    if venue != "すべて":
        data = data[
            data["競馬場"] == venue
        ]

    if track != "すべて":
        data = data[
            data["馬場状態"] == track
        ]

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

# ======================================
# 成績集計
# ======================================

def summarize(group):
    finish = group["着順_数値"].dropna()
    agari = group["上がり順位"].dropna()

    def rate(count, denominator):
        if denominator == 0:
            return float("nan")

        return round(
            count / denominator * 100, 2
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

# ======================================
# ⑥ 分析実行
# ======================================

st.header("⑥ 分析する")

if st.button(
    "分析する",
    type="primary",
    use_container_width=True
):
    filtered = filter_data(analysis_df)

    if mode == "馬を比較":
        if len(selected) < 2:
            st.warning(
                "比較する馬を2頭以上選んでください。"
            )
            st.stop()

        filtered = filtered[
            filtered["馬名"].isin(selected)
        ]

    if filtered.empty:
        st.warning(
            "条件に一致するレースがありません。"
        )
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

        st.subheader(
            f"🏆 {metric} ランキング"
        )

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

                st.caption(
                    "上がり順位の集計対象："
                    f"{int(row['上がり対象走数'])}走"
                )

    csv_data = result.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "📥 分析結果をCSVで保存",
        data=csv_data,
        file_name="keiba_analysis.csv",
        mime="text/csv",
        use_container_width=True
    )
