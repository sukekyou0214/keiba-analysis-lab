
"""競馬分析ラボ Ver.21"""
import streamlit as st

st.set_page_config(
    page_title="競馬分析ラボ",
    page_icon="🏇",
    layout="wide"
)

st.title("🏇 競馬分析ラボ Ver.21")
st.caption("総合診断・各馬の特徴・分析条件")

from keiba_ui import *
