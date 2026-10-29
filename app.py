
import streamlit as st

st.set_page_config(
    page_title="競馬分析ラボ",
    page_icon="🏇",
    layout="wide"
)

st.title("🏇 競馬分析ラボ Ver.8")
st.write("出走馬の成績を比較・分析するWebアプリ")

st.success("Webアプリの準備ができました！")

st.subheader("分析するレース")
race = st.text_input(
    "レースIDまたは出馬表URL",
    placeholder="ここに入力"
)

if st.button("分析する", use_container_width=True):
    if race:
        st.info("レースの読み込み機能はこれから追加します。")
    else:
        st.warning("レースIDを入力してください。")
