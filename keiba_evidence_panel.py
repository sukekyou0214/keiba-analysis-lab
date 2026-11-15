"""詳細分析：各馬のデータの裏付け（標本数・鮮度・欠損）。点数ではなく、分子／分母と日付で示す。"""
import pandas as pd


def evidence_table(data, horses, numbers, context):
    import keiba_model_v1 as v1
    from keiba_model_data import from_prepared
    h = from_prepared(data)
    by = {k: g for k, g in h.groupby('馬ID')} if not h.empty else {}
    info = {'開催日': context['race_date'], '距離': int(context['distance']), '競馬場': context['venue'],
            'クラス': None, '馬場': None}
    params = v1.Params(features=())
    params.priors = {'top3': 0.0}
    rows = []
    for hid, name in horses.items():
        f = v1.horse_rows(by.get(str(hid), pd.DataFrame(columns=h.columns)), context['race_date'], info, params)
        num = numbers.get(hid) if numbers else None
        frac = lambda key: f"{f.get(key + '_分子', 0)}/{f.get(key + '_分母', 0)}"
        date = lambda key: f[key].strftime('%Y/%m/%d') if pd.notna(f.get(key)) else '—'
        missing = []
        if not f.get('芝有効走数'):
            missing.append('芝の比較可能な走なし')
        if pd.isna(f.get('最終出走日')):
            missing.append('出走記録なし')
        rows.append({'馬': f"{num} {name}" if num else name, '芝有効走数（直近5）': f.get('芝有効走数', 0),
                     '最終出走': date('最終出走日'), '休養日数': f.get('休養日数', '—'), '最終芝出走': date('最終芝出走日'),
                     '直近5走のダート': f.get('直近ダート数', '—'),
                     'クラス水準（直近芝5走）': round(f['クラス水準'], 1) if pd.notna(f.get('クラス水準')) else '—',
                     f"距離±200m（3着内/走）": frac('距離適性'), '同場（3着内/走）': frac('同場適性'),
                     '欠損': '・'.join(missing) or '—'})
    return pd.DataFrame(rows)


def render_evidence(data, horses, numbers, context):
    import streamlit as st
    with st.expander('データの裏付け（標本数・鮮度・欠損）', expanded=False):
        if context is None:
            st.info('対象レースの開催条件を確認すると表示できます。')
            return
        st.dataframe(evidence_table(data, horses, numbers, context), hide_index=True, width='stretch')
        st.caption('3着内/走は、対象日前の芝で着順を比較できた走の数え上げです（1/1と10/10は確かさが違います）。'
                   'クラス水準：未勝利・新馬0、1勝1、2勝2、3勝3、OP4、L4.5、G3 5、G2 6、G1 7（仮の目盛り）。'
                   '本体の保存データでは取消・除外と競走中止を区別できないため、着順が数値の走だけを数えています。')
