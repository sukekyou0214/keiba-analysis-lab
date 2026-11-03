"""Three main departments; legacy views keep their existing implementation."""
DEPARTMENTS=['🏇 総合診断','🔍 詳細分析','⚙️ 詳細設定','🧰 管理・検証']
DETAILS={'ペース・展開':'🌊 ペース分析','各馬の特徴':'🐎 各馬の特徴','過去10年分析':'📚 過去10年分析'}


def render_navigation():
    import streamlit as st
    with st.sidebar:
        st.markdown('## 🏇 メニュー')
        selected=st.radio('メインメニュー',DEPARTMENTS,key='nav_department_v22',label_visibility='collapsed')
        if selected=='🔍 詳細分析':
            detail=st.radio('詳細分析の項目',list(DETAILS),key='nav_detail_v22')
            page=DETAILS[detail]
        elif selected=='⚙️ 詳細設定':
            page='⚙️ 分析条件'
        else:
            page=selected
        st.caption('管理・検証は予想の主画面から分けたオプションです。')
        st.divider()
        st.caption('競馬分析ラボ Ver.22.1')
    return page
