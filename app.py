import re
import json
from pathlib import Path
import time
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

_keiba_start_t = time.perf_counter()
st.set_page_config(page_title='競馬分析ラボ', page_icon='🏇', layout='wide')
st.markdown('### 🏇 競馬分析ラボ Ver.20.18.0')
st.caption('総合診断・各馬の特徴・分析条件は左上の ☰ メニューから選べます。')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
VENUES = ['札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉']
METRICS = ['勝率(%)', '連対率(%)', '複勝率(%)', '平均着順', '平均4角', '平均上がり', '上がり最速率(%)', '上がり3位以内率(%)', '平均上がり順位', '前方率(%)', '差し追込率(%)']
NUMERIC = ['距離_m', '着順_数値', '上がり3F_秒', '4角順位', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差']
ALIASES = {'馬名': ['馬名', '馬名_正規化'], '競馬場': ['競馬場', '場所', '開催場'], '馬場状態': ['馬場状態', '馬 場', '馬場'], '距離_m': ['距離_m', '距離', '距離(m)'], '着順_数値': ['着順_数値', '着順'], '上がり3F_秒': ['上がり3F_秒', '上がり3F', '上り', '上がり'], '4角順位': ['4角順位', '4角', '4角通過順位'], '上がり順位': ['上がり順位', '上り順位'], '日付': ['日付', '年月日', '開催日', 'レース日'], '頭数': ['頭数', '頭 数'], '前半3F': ['前半3F'], '後半3F': ['後半3F'], '前後半3F差': ['前後半3F差'], '推定ペース': ['推定ペース'], '推定脚質': ['推定脚質'], 'レース格': ['レース格', '格付け', 'グレード'], 'レース名': ['レース名'], '格付け根拠': ['格付け根拠'], '通過順': ['通過順', '通過']}



def interpret_running_pattern(row):
    """好走/凡走した *レース全体* のラップ差を説明する。能力スコアではない。"""
    ng = int(row.get('好走走数', 0) or 0)
    nb = int(row.get('凡走走数', 0) or 0)
    if ng == 0:
        return '好走データなし', '判定不可', '3着以内に入ったラップ確認レースがありません。'
    if nb == 0:
        return '凡走データなし', '判定不可', '4着以下のラップ確認レースがないため、好走・凡走の比較はできません。'
    if min(ng, nb) < 2:
        confidence = '参考（片側1走）'
    elif min(ng, nb) < 3:
        confidence = '暫定（片側2走）'
    else:
        confidence = '傾向（各3走以上）'
    def diff(k):
        x = row.get(k + '_差', float('nan'))
        try:
            x = float(x)
            return x if pd.notna(x) else None
        except (ValueError, TypeError):
            return None
    end, early, width = diff('終盤加速'), diff('残1000m加速'), diff('残1000mラップ幅')
    labels, explanations = [], []
    # 0.25秒/200mという差は表示上の仮閾値。統計的有意性を意味しない。
    if end is not None and end >= 0.25:
        labels.append('終盤加速型')
        explanations.append('好走時は凡走時より終盤600mの加速が大きい')
    elif end is not None and end <= -0.25:
        labels.append('急加速に頼らない型')
        explanations.append('好走時は凡走時ほど終盤の急加速を伴わない')
    if early is not None and early >= 0.25:
        labels.append('早め加速型')
        explanations.append('好走時は残り1000mからのペースアップが大きい')
    elif early is not None and early <= -0.25:
        labels.append('早仕掛け不要型')
        explanations.append('好走時は残り1000mからのペースアップが小さい')
    if width is not None and width <= -0.30:
        labels.append('一定ラップ型')
        explanations.append('好走時は残り1000mのラップ変動が小さい')
    elif width is not None and width >= 0.30:
        labels.append('緩急対応型')
        explanations.append('好走時は残り1000mのラップ変動が大きい')
    if not labels:
        return '特徴差が小さい', confidence, '今回の集計では好走時と凡走時に明瞭なラップ差がありません。'
    # ラベルは多くても2つに整理。複数指標を総合し、元データを確認できるようにする。
    ranked = list(zip(labels, explanations))
    # 展開を説明する順番: 終盤加速 → 早め加速 → 持続性。3つ目は補足文だけに残す。
    title = '＋'.join(x[0] for x in ranked[:2])
    reason = '。'.join(x[1] for x in ranked) + '。'
    if confidence.startswith('参考'):
        reason += ' ※片側1走のため特に不確実です。'
    return title, confidence, reason



def diagnose_horse_v17(horse_df, lap_label=''):
    """位置・末脚は馬別、展開はレース全体。欠測をゼロ評価にしない。"""
    d = horse_df.copy()
    if d.empty:
        return {'走りの総合診断': '判定材料なし', '位置取り': '不明', '末脚': '不明',
                '展開': '不明', 'S/M/H': '不明', '信頼度': '判定不可', '対象走数': 0,
                '好走走数': 0, '位置取得': 0, '上がり取得': 0, '根拠': '該当レースなし'}
    d['着順_数値'] = pd.to_numeric(d['着順_数値'], errors='coerce')
    d = d.loc[d['着順_数値'].ge(1)].copy()
    good = d.loc[d['着順_数値'].le(3)].copy()
    def numeric(k):
        return pd.to_numeric(good[k], errors='coerce') if k in good.columns else pd.Series(dtype=float)
    n_good = len(good)
    pos = numeric('4角順位')
    field = numeric('頭数')
    valid_pos = (pos.ge(1) & field.ge(2) & pos.le(field))
    fractions = (pos[valid_pos] / field[valid_pos]).dropna()
    n_pos = len(fractions)
    pos_label = '不明'
    if n_pos:
        med = fractions.median()
        pos_label = '前方' if med <= .35 else ('中団' if med <= .70 else '後方')
    pace_values = good['推定ペース'].dropna().astype(str) if '推定ペース' in good.columns else pd.Series(dtype=str)
    pace_values = pace_values[pace_values.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])]
    pace_summary = '・'.join(f'{k.replace("寄り", "")}{v}走' for k, v in pace_values.value_counts().items()) if len(pace_values) else '不明'
    agari = numeric('上がり順位')
    agari = agari.loc[agari.ge(1)].dropna()
    n_agari = len(agari)
    agari_label = '不明'
    if n_agari:
        rate = agari.le(3).mean()
        agari_label = '末脚上位' if rate >= .6 else ('末脚まちまち' if rate >= .3 else '末脚上位少')
    if n_good == 0:
        title = '好走データなし'
    elif not n_pos and not n_agari:
        title = '走り方は不明'
    elif n_pos and n_agari:
        suffix = '×末脚' if agari_label == '末脚上位' else ''
        title = {'前方':'前方型','中団':'中団型','後方':'後方型'}[pos_label] + suffix
    elif n_pos:
        title = pos_label + '型'
    else:
        title = '末脚型' if agari_label == '末脚上位' else '脚質不明'
    # ラップ判定の根拠は各馬のラップではなく出走したレース全体のもの。
    short_lap = str(lap_label).split('＋')[0]
    if short_lap in ('特徴差が小さい', '好走データなし', '凡走データなし'):
        short_lap = '判定保留'
    if len(short_lap) > 12:
        short_lap = short_lap[:12]
    if n_good == 0:
        conf = '判定不可'
    elif n_good < 3 or (n_pos and n_pos < 2) or (n_agari and n_agari < 2):
        conf = '参考'
    elif n_good < 5 or n_pos == 0 or n_agari == 0:
        conf = '暫定'
    else:
        conf = '傾向あり'
    reason = (f'3着以内 {n_good}/{len(d)}走。好走時の4角位置データ {n_pos}走'
              f'（中央値の頭数比から{pos_label}判定）、上がり順位 {n_agari}走'
              f'（{agari_label}）。好走時のnetkeibaペース内訳：{pace_summary}。'
              f'ラップ特徴は「{short_lap}」〔馬自身ではなくレース全体〕。')
    return {'走りの総合診断': title, '位置取り': pos_label, '末脚': agari_label,
            '展開': short_lap, 'S/M/H': pace_summary, '信頼度': conf, '対象走数': len(d),
            '好走走数': n_good, '位置取得': n_pos, '上がり取得': n_agari, '根拠': reason}



def estimate_field_pace_v19(data, names):
    """序盤位置と、その位置で走った過去レースのnetkeiba S/M/Hを用いる暫定予測。

    出走馬それぞれの直近5走に限定。予測値は独自の未検証ヒューリスティック。
    先頭・先行の同定は最初に記録されたコーナーの位置で、発走直後ではない。
    """
    names = list(dict.fromkeys(names))
    records = []
    for name in names:
        h = data.loc[data['馬名'].eq(name)].copy()
        if '日付' in h.columns:
            h = h.sort_values('日付', ascending=False, na_position='last', kind='stable')
        h = h.head(5)
        p = pd.to_numeric(h['4角順位'], errors='coerce')
        f = pd.to_numeric(h['頭数'], errors='coerce')
        valid = p.ge(1) & f.ge(2) & p.le(f)
        lead = int((valid & p.eq(1)).sum())
        front = int((valid & p.le((f * .35).clip(lower=2))).sum())
        corners = h['通過順'] if '通過順' in h.columns else pd.Series('', index=h.index)
        # 全頭再現では取得直後のnetkeibaペースが最も信頼できる。
        # prepare()前に追加取得した場合、推定ペース列が未更新でも正しく使用する。
        race_paces = (h['netkeibaペース'] if 'netkeibaペース' in h.columns
                      else h['推定ペース'] if '推定ペース' in h.columns
                      else pd.Series('', index=h.index))
        early_lead = early_front = en = 0
        lead_paces = {'S': 0, 'M': 0, 'H': 0}
        front_paces = {'S': 0, 'M': 0, 'H': 0}
        for raw, size, pace in zip(corners, f, race_paces):
            pieces = re.findall(r'\d+', str(raw))
            if not pieces or pd.isna(size) or size < 2:
                continue
            first = int(pieces[0])
            if not (1 <= first <= size):
                continue
            en += 1
            is_lead = first == 1
            is_front = first <= max(2, size * .35)
            early_lead += int(is_lead)
            early_front += int(is_front)
            # 推定ペースはnetkeibaの明示判定のみ（独自暫定値との混用禁止）。
            # 現行パイプラインの「推定ペース」はnetkeiba判定で上書きされる想定。
            tag = {'スロー寄り': 'S', 'ミドル寄り': 'M', 'ハイ寄り': 'H'}.get(str(pace))
            if tag and is_lead:
                lead_paces[tag] += 1
            if tag and is_front:
                front_paces[tag] += 1
        escape_candidate = en >= 2 and early_lead / en >= .4
        forward_candidate = en >= 2 and early_front / en >= .6
        records.append({
            '馬名': name, '初通過取得': en, '初通過先頭': early_lead,
            '初通過前方': early_front, '4角先頭': lead, '4角前方': front,
            '逃げ候補': escape_candidate, '先行候補': forward_candidate,
            '先頭S': lead_paces['S'], '先頭M': lead_paces['M'], '先頭H': lead_paces['H'],
            '前方S': front_paces['S'], '前方M': front_paces['M'], '前方H': front_paces['H'],
            '先頭ペース確認': sum(lead_paces.values()),
            '前方ペース確認': sum(front_paces.values()),
        })
    info = pd.DataFrame(records)
    total = len(info)
    known = int(info['初通過取得'].ge(2).sum()) if total else 0
    leaders = int(info['逃げ候補'].sum()) if total else 0
    forward = int(info['先行候補'].sum()) if total else 0
    coverage = known / total if total else 0
    lead_rows = info.loc[info['逃げ候補']] if total else info
    pace_known = int(lead_rows['先頭ペース確認'].sum()) if total else 0
    h_lead = int(lead_rows['先頭H'].sum()) if total else 0
    s_lead = int(lead_rows['先頭S'].sum()) if total else 0
    m_lead = int(lead_rows['先頭M'].sum()) if total else 0
    front_h = int(info.loc[info['先行候補'], '前方H'].sum()) if total else 0
    front_known = int(info.loc[info['先行候補'], '前方ペース確認'].sum()) if total else 0
    # Hを逃げ候補の頭数だけで出さない。実際のH逃げ記録がなければ保留/M。
    if not total or coverage < .6:
        pred = None
        explanation = f'序盤位置を確認できた馬が{known}/{total}頭。位置取りデータが不足。'
    elif leaders >= 2:
        if pace_known < 2:
            pred = None
            explanation = '逃げ候補は複数いるが、過去に先頭だったレースのペース判定が不足。'
        elif (forward >= max(4, (total+3)//4) and
              ((h_lead >= 2 and h_lead / pace_known >= .4) or
               (h_lead >= 1 and front_known >= 4 and front_h >= 3 and
                front_h / front_known >= .4))):
            pred = 'ハイ（H）'
            explanation = '逃げ候補が複数いて、過去にハイペースで先頭を走った実績と先行圧力がある。'
        elif s_lead >= 2 and s_lead / pace_known >= .6 and h_lead == 0:
            pred = 'スロー（S）'
            explanation = '逃げ候補は複数いるが、先頭実績はスロー中心でH実績を確認できない。'
        else:
            pred = 'ミドル（M）'
            explanation = '逃げ候補は複数いるが、過去の逃げたペースからHを強く支持する材料は限定的。'
    elif leaders == 1 and forward <= max(3, total//4):
        pred = 'スロー（S）'
        explanation = '逃げ候補が1頭、先行圧力も小さいため単騎逃げのSシナリオ。'
    elif leaders == 0 and forward <= max(2, total//6):
        pred = 'スロー（S）'
        explanation = '明確な逃げ候補・先行候補が少ないためSシナリオ。'
    else:
        pred = 'ミドル（M）'
        explanation = '逃げ候補の競合と先行圧力の材料が拮抗。'
    explanation += (f' 逃げ候補が過去に先頭だったレースのペース内訳：'
                    f'S{s_lead}・M{m_lead}・H{h_lead}走（判定取得{pace_known}走）。'
                    f' 先行候補が前方だったHレース：{front_h}/{front_known}走。')
    return pred, explanation, info, known, leaders, forward



def historical_replay_v194(all_data, minimum_known=3, max_races=100):
    """現出走馬の過去成績だけを利用した部分出走馬リプレイ。

    重要: 当時の全出走馬を復元するものではない。対象レースより前の
    走歴だけを利用することで、同一レースの結果の混入を防ぐ。
    """
    empty_cols = ['開催日', 'レース名', 'レースID', '実際のペース', '予測',
                  '一致', '記録済み出走馬', '事前位置データあり', '逃げ候補',
                  '先行候補', '判定状態', '根拠']
    if all_data is None or all_data.empty:
        return pd.DataFrame(columns=empty_cols), {'候補レース': 0, '検証可能': 0}
    if not {'レースID', '馬名', '日付', 'netkeibaペース'}.issubset(all_data.columns):
        return pd.DataFrame(columns=empty_cols), {'候補レース': 0, '検証可能': 0}
    df = all_data.copy()
    df['_test_rid'] = (df['レースID'].fillna('').astype(str)
                      .str.strip().str.replace(r'\.0$', '', regex=True))
    df['_test_date'] = pd.to_datetime(df['日付'], errors='coerce')
    df = df.loc[df['_test_rid'].str.fullmatch(r'\d{12}') & df['_test_date'].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=empty_cols), {'候補レース': 0, '検証可能': 0}
    df = df.sort_values('_test_date', ascending=False)
    target_groups = df.groupby('_test_rid', sort=False)
    rows = []
    inspected = 0
    # 特定のレースが複数の馬に含まれていても1レースとして評価する。
    for rid, race_group in target_groups:
        actuals = race_group['netkeibaペース'].dropna().astype(str).unique().tolist()
        actuals = [x for x in actuals if x in ('スロー寄り', 'ミドル寄り', 'ハイ寄り')]
        if len(set(actuals)) != 1:
            continue
        inspected += 1
        if inspected > max_races:
            break
        race_date = race_group['_test_date'].min()
        # 同じ日付のレース・将来のレースは履歴から除外（時間的な情報漏洩防止）
        past = df.loc[df['_test_date'].lt(race_date)].copy()
        entrants = race_group['馬名'].dropna().astype(str).unique().tolist()
        historical_names = [n for n in entrants if (past['馬名'].eq(n)).any()]
        name = (str(race_group['レース名'].dropna().iloc[0])
                if 'レース名' in race_group and race_group['レース名'].notna().any() else '')
        prediction = None
        reason = ''
        known = leaders = front = 0
        if historical_names:
            try:
                prediction, reason, _, known, leaders, front = estimate_field_pace_v19(past, historical_names)
            except (KeyError, ValueError, TypeError) as exc:
                reason = '計算不可: ' + str(exc)[:90]
        actual = {'スロー寄り': 'S', 'ミドル寄り': 'M', 'ハイ寄り': 'H'}[actuals[0]]
        predicted = {'スロー（S）':'S', 'ミドル（M）':'M', 'ハイ（H）':'H'}.get(prediction)
        eligible = len(historical_names) >= minimum_known and known >= minimum_known
        state = ('比較可（部分出走馬）' if eligible and predicted else
                 '予測保留' if eligible else '事前情報不足')
        rows.append({'開催日':race_date.strftime('%Y-%m-%d'), 'レース名':name,
                     'レースID':rid, '実際のペース':actual,
                     '予測':predicted if eligible and predicted else '—',
                     '一致': ('一致' if predicted == actual else '不一致') if eligible and predicted else '対象外',
                     '記録済み出走馬':len(entrants), '事前位置データあり':known,
                     '逃げ候補':leaders, '先行候補':front,
                     '判定状態':state, '根拠':reason})
    result = pd.DataFrame(rows, columns=empty_cols)
    comparable = result[result['判定状態'].eq('比較可（部分出走馬）')]
    stats = {'候補レース':len(result), '検証可能':len(comparable),
             '一致':int(comparable['一致'].eq('一致').sum()) if len(comparable) else 0,
             '不一致':int(comparable['一致'].eq('不一致').sum()) if len(comparable) else 0}
    return result, stats





def fixed_order_table_v209(frame, columns, labels=None, max_height=620):
    """横スクロール中も馬番＋馬名3文字を固定。元の馬名は欠落させない。"""
    import html as _html
    labels = labels or {}
    cols = [c for c in columns if c in frame.columns]
    if not cols:
        return
    heads = ''.join('<th>' + _html.escape(str(labels.get(c, c))) + '</th>' for c in cols)
    rows = []
    for vals in frame[cols].itertuples(index=False, name=None):
        cells = []
        for idx, v in enumerate(vals):
            raw = '—' if pd.isna(v) else str(v)
            if idx == 0 and cols[0] == '馬名':
                # 既存の馬番表示が「1 クロワデュノール」でも、元の正式馬名だけでも対応。
                m = re.match(r'^([0-9０-９]+)\s*(.+)$', raw)
                short = m.group(1) + ' ' + m.group(2)[:3] if m else raw[:5]
                cells.append('<td class="k-name" title="' + _html.escape(raw, quote=True)
                             + '">' + _html.escape(short) + '</td>')
            else:
                cells.append('<td>' + _html.escape(raw) + '</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    st.markdown(
        '<div class="k-scroll" style="overflow-x:auto;overflow-y:auto;max-height:' + str(max_height) + 'px">'
        '<table class="k-table"><thead><tr>' + heads + '</tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table></div>'
        '<style>.k-scroll{border:1px solid #8885;border-radius:8px;}'
        '.k-table{border-collapse:separate;border-spacing:0;min-width:600px;width:100%;font-size:13px;}'
        '.k-table td,.k-table th{padding:9px 10px;text-align:left;border-bottom:1px solid #8884;'
        'vertical-align:top;white-space:normal;background:#111827;color:#f4f5f8;}'
        '.k-table th{position:sticky;top:0;background:#263040;z-index:1;}'
        '.k-table td:first-child,.k-table th:first-child{position:sticky;left:0;'
        'width:118px;min-width:118px;max-width:118px;white-space:nowrap;'
        'background:#1c2534;z-index:2;box-shadow:2px 0 5px #0007;font-weight:700;}'
        '.k-table th:first-child{background:#263040;z-index:3;'
        'font-size:0;} .k-table th:first-child:after{content:"馬番＋馬名";font-size:13px;}</style>',
        unsafe_allow_html=True)


def corner_projection_v2013(data, names, max_runs=5):
    """実際の通過順を先頭・中団・後方の頭数比に正規化して段階別に集計。
    1角/2角を記録しないレースでは推測で補完しない。
    直線は4角の位置からの参考値で、実際の直線位置は不明。
    """
    from statistics import median
    stages = ('1角','2角','3角','4角')
    output = {}
    for name in names:
        h = data.loc[data['馬名'].eq(name)].copy()
        if '日付' in h:
            h = h.sort_values('日付', ascending=False, na_position='last', kind='stable')
        h = h.head(max_runs)
        observed = {k: [] for k in stages}
        for _, r in h.iterrows():
            n = pd.to_numeric(r.get('頭数'), errors='coerce')
            if pd.isna(n) or n < 2:
                continue
            nums = [int(x) for x in re.findall(r'\d+', str(r.get('通過順', '')))]
            # 2地点なら3角/4角、3地点なら2角/3角/4角、
            # 4地点なら1角～4角。1地点なら4角のみと解釈。
            # 5地点以上や不自然な値は安全のため除外する。
            if not 1 <= len(nums) <= 4 or any(x < 1 or x > n for x in nums):
                continue
            for stage, rank in zip(stages[4-len(nums):], nums):
                observed[stage].append((rank-1)/(n-1))
        labels = {}
        for stage in stages:
            values = observed[stage]
            if not values:
                labels[stage] = ('不明', 0)
            else:
                ratio = median(values)
                label = ('逃げ' if ratio <= .075 else '先行' if ratio <= .35
                         else '中団' if ratio <= .70 else '後方')
                labels[stage] = (label, len(values))
        output[name] = labels
    return output


def movement_profile_v2015(data, names, max_runs=5):
    """同じ過去レース内での3角→4角と4角→ゴールの順位変化。

    値は頭数で正規化した順位の差。ゴールは着順であり直線途中の隊列ではない。
    角の記録が2地点以上のときだけ3角と4角を比較する。
    """
    from statistics import median
    profiles = {}
    for name in names:
        h = data.loc[data['馬名'].eq(name)].copy()
        if '日付' in h.columns:
            h = h.sort_values('日付', ascending=False, na_position='last', kind='stable')
        h = h.head(max_runs)
        changes_corner, changes_finish = [], []
        for _, r in h.iterrows():
            n = pd.to_numeric(r.get('頭数'), errors='coerce')
            if pd.isna(n) or n < 2:
                continue
            n = int(n)
            parts = [int(x) for x in re.findall(r'\d+', str(r.get('通過順', '')))]
            if not (1 <= len(parts) <= 4) or any(x < 1 or x > n for x in parts):
                continue
            last = parts[-1]
            if len(parts) >= 2:
                before = parts[-2]
                changes_corner.append((before-last)/(n-1))
            finish = pd.to_numeric(r.get('着順_数値'), errors='coerce')
            if pd.notna(finish) and 1 <= finish <= n:
                changes_finish.append((last-int(finish))/(n-1))
        def summarize(values, stage):
            if not values:
                return 'データ不足', 0
            # 5%以内は誤差を含むため「ほぼ維持」扱い
            v = median(values)
            if v >= .05:
                return ('位置を上げる傾向' if stage == 'corner' else 'ゴールまで追い上げ'), len(values)
            if v <= -.05:
                return ('位置を下げる傾向' if stage == 'corner' else 'ゴールまで後退'), len(values)
            return 'おおむね位置維持', len(values)
        profiles[name] = (summarize(changes_corner,'corner'), summarize(changes_finish,'finish'))
    return profiles



def movement_audit_v2016(data, horse_names, max_runs=5):
    """判定に使用した各走の元値・計算式・除外理由を監査用に出力。"""
    from statistics import median
    rows = []
    for name in horse_names:
        h = data.loc[data['馬名'].eq(name)].copy()
        if '日付' in h.columns:
            h = h.sort_values('日付', ascending=False, na_position='last', kind='stable')
        h = h.head(max_runs)
        for _, r in h.iterrows():
            n = pd.to_numeric(r.get('頭数'), errors='coerce')
            parts = [int(x) for x in re.findall(r'\d+', str(r.get('通過順', '')))]
            fin = pd.to_numeric(r.get('着順_数値'), errors='coerce')
            valid_n = pd.notna(n) and n >= 2
            valid_pass = valid_n and 1 <= len(parts) <= 4 and all(1 <= p <= n for p in parts)
            valid_fin = valid_pass and pd.notna(fin) and 1 <= fin <= n
            c3 = parts[-2] if valid_pass and len(parts) >= 2 else None
            c4 = parts[-1] if valid_pass else None
            corner_delta = ((c3-c4)/(n-1) if c3 is not None else None)
            finish_delta = ((c4-int(fin))/(n-1) if valid_fin else None)
            reason = ('頭数が不明' if not valid_n else
                      '通過順が不明・形式不正' if not valid_pass else
                      '着順が不明' if not valid_fin else '有効')
            rows.append({'馬名':name, '日付':str(r.get('日付','')),
                         'レース名':str(r.get('レース名','')),
                         '頭数':int(n) if valid_n else None,
                         '通過順_原文':str(r.get('通過順','')),
                         '3角':c3, '4角':c4,
                         '着順':int(fin) if pd.notna(fin) else None,
                         '3角→4角_順位変化':c3-c4 if c3 is not None else None,
                         '3角→4角_補正値':round(corner_delta,4) if corner_delta is not None else None,
                         '4角→着順_順位変化':c4-int(fin) if valid_fin else None,
                         '4角→着順_補正値':round(finish_delta,4) if finish_delta is not None else None,
                         '検証':reason})
    return pd.DataFrame(rows)


def movement_counts_v2017(data, names, max_runs=5):
    """直近最大5走の順位変化を実際の順位差で集計。欠測を維持に含めない。"""
    audit = movement_audit_v2016(data, names, max_runs)
    output = {}
    for name in names:
        subset = audit.loc[audit['馬名'].eq(name)]
        stages = []
        for column in ('3角→4角_順位変化', '4角→着順_順位変化'):
            values = pd.to_numeric(subset[column], errors='coerce').dropna()
            up = int((values > 0).sum())
            same = int((values == 0).sum())
            down = int((values < 0).sum())
            missing = len(subset) - len(values)
            stages.append((f'上昇{up}／維持{same}／後退{down}', len(values), missing))
        output[name] = (stages[0], stages[1], len(subset))
    return output


# ===== Ver.20: 過去レース全出走馬を対象とする事前データ検証 =====
# 現在のレースの出走馬17頭ではなく、検証対象レースの実際の出走馬から集める。
# ただし、事前の走歴不足は必ず明示し、欠測を予測的中扱いしない。
V195_TAG = {'スロー寄り': 'S', 'ミドル寄り': 'M', 'ハイ寄り': 'H'}

# Ver.20: 馬場状態の表記揺れを統一（良・稍重・重・不良は別々）。
# 「良」でも開催日・含水率・芝の傷みで時計は異なるため、完全補正とは扱わない。
def normalize_going_v20(value):
    text = str(value).replace(' ', '').replace('　', '').strip()
    if '不良' in text:
        return '不良'
    if '稍' in text or 'やや重' in text or '稍重' in text:
        return '稍重'
    if '重' in text:
        return '重'
    if '良' in text:
        return '良'
    return '不明'


def benchmark_laps_v20(candidate, pool, minimum=3):
    """未来情報を排除したラップ基準（予測式には未使用）。

    比較対象1走の当日より*前*の独立レースのみを使用。
    コース・距離±200mを保ち、馬場不一致の参考値は低信頼として明示。
    """
    distance = pd.to_numeric(candidate.get('distance'), errors='coerce')
    course = candidate.get('course', '不明')
    venue = candidate.get('venue', '不明')
    going = normalize_going_v20(candidate.get('going', ''))
    rid = str(candidate.get('id', ''))
    day = pd.to_datetime(candidate.get('date'), errors='coerce')
    empty = {'基準状態':'基準不足', '基準信頼度':'算出不可', '基準レース数':0,
             '基準条件':'適用不可', '基準の最終日':'', '未来データ除外数':0,
             '前半600m平均との差(秒)':None, '前半1000m平均との差(秒)':None}
    if pd.isna(distance) or course not in ('芝','ダ') or pd.isna(day):
        return {**empty, '基準状態':'距離・コース・日付不明'}
    eligible = []
    excluded_future = 0
    for key, r in pool.items():
        rday = pd.to_datetime(r.get('date'), errors='coerce')
        if str(key) == rid or r.get('course') != course or abs(r.get('distance', 0)-float(distance)) > 200:
            continue
        if pd.isna(rday) or rday >= day:
            excluded_future += 1
            continue
        eligible.append(r)
    rules = [
        ('競馬場・距離・馬場一致', lambda r: r['venue']==venue and r['going']==going, '高'),
        ('距離・馬場一致（競馬場横断）',lambda r:r['going']==going,'中'),
        ('競馬場・距離一致（馬場混在）',lambda r:r['venue']==venue,'低'),
        ('距離一致（競馬場・馬場混在）',lambda r:True,'低'),
    ]
    for label, rule, confidence in rules:
        matches = [r for r in eligible if rule(r)]
        if len(matches) >= minimum:
            latest = max(pd.to_datetime(r['date']) for r in matches)
            return {'基準状態':'参考（比較可能）', '基準信頼度':confidence,
                    '基準レース数':len(matches), '基準条件':label,
                    '基準の最終日':latest.strftime('%Y-%m-%d'),
                    '未来データ除外数':excluded_future,
                    '前半600m平均との差(秒)':round(candidate['600']-sum(r['600'] for r in matches)/len(matches),3),
                    '前半1000m平均との差(秒)':round(candidate['1000']-sum(r['1000'] for r in matches)/len(matches),3)}
    return {**empty, '基準レース数':len(eligible), '基準条件':'同一芝ダート・±200m（3レース未満）',
            '未来データ除外数':excluded_future}



def historical_field_ids_v195(rid):
    """結果表の馬リンクのみから当時の全出走馬IDと馬名を復元する。"""
    rid = str(rid).strip()
    if not re.fullmatch(r'\d{12}', rid):
        raise ValueError('レースIDが不正です')
    url = f'https://race.netkeiba.com/race/result.html?race_id={rid}'
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.select_one('#All_Result_Table')
    if table is None:
        table = next((t for t in soup.select('table') if
                      len(t.select('a[href*="/horse/"]')) >= 5 and
                      '着' in t.get_text(' ', strip=True)[:250]), None)
    if table is None:
        raise ValueError('結果表から全出走馬を取得できません')
    found = {}
    for tr in table.select('tr'):
        for a in tr.select('a[href*="/horse/"]'):
            match = re.search(r'/horse/(\d{10})(?:/|[?\#]|$)', a.get('href',''))
            if match:
                name = a.get_text(' ', strip=True)
                if name:
                    found[match.group(1)] = name
    if not 5 <= len(found) <= 22:
        raise ValueError(f'結果表の馬ID数が異常: {len(found)}頭（5～22頭のみ対象）')
    return found


def full_field_replay_v195(rid, known_source, progress_callback=None, limit_pace_calls=25, collect_early_laps=False, limit_lap_calls=12):
    """レース当日の結果・S/M/Hを予測入力に混ぜないよう、当日より前だけ採用。"""
    known = known_source.copy()
    rid = str(rid).strip()
    target_rows = known.loc[known['レースID'].fillna('').astype(str).str.replace(r'\.0$', '',regex=True).eq(rid)]
    if target_rows.empty:
        raise ValueError('検証対象のレースが保存データにありません')
    date = pd.to_datetime(target_rows['日付'],errors='coerce').min()
    if pd.isna(date):
        raise ValueError('開催日が分かりません')
    actual_raw = target_rows['netkeibaペース'].dropna().astype(str)
    actual_raw = actual_raw[actual_raw.isin(V195_TAG)]
    if actual_raw.empty or len(actual_raw.unique()) != 1:
        raise ValueError('実際のS/M/Hを一意に確認できません')
    actual = V195_TAG[actual_raw.iloc[0]]
    # レース属性は対象レースの過去成績行から取得（予測特徴量には渡さない）。
    venue_values = target_rows['競馬場'].dropna().astype(str) if '競馬場' in target_rows else pd.Series(dtype=str)
    venue_value = venue_values.iloc[0] if not venue_values.empty else '不明'
    distance_values = pd.to_numeric(target_rows['距離_m'], errors='coerce').dropna() if '距離_m' in target_rows else pd.Series(dtype=float)
    distance_value = int(distance_values.iloc[0]) if not distance_values.empty else None
    distance_group = ('不明' if distance_value is None else
                      '〜1600m' if distance_value <= 1600 else
                      '1601〜2000m' if distance_value <= 2000 else
                      '2001〜2400m' if distance_value <= 2400 else '2401m〜')
    target_goings = target_rows['馬場状態'].dropna().astype(str) if '馬場状態' in target_rows else pd.Series(dtype=str)
    target_going_v20 = normalize_going_v20(target_goings.iloc[0] if not target_goings.empty else '')
    target_courses = target_rows['コース'].dropna().astype(str) if 'コース' in target_rows else pd.Series(dtype=str)
    target_course_v20 = target_courses.iloc[0] if not target_courses.empty else '不明'
    entrants = historical_field_ids_v195(rid)
    frames=[]; failed=[]; pace_calls=0; local_paces={}
    # 再起動後の全件再取得は避け、当日の成績の混入も避ける。
    known_ids = (known['馬ID'].astype(str).str.replace(r'\.0$', '',regex=True).unique().tolist()
                 if '馬ID' in known.columns else [])
    for index,(hid,name) in enumerate(entrants.items(),1):
        try:
            # 保存済みデータが5走以上あれば、既知データを優先。
            if hid in known_ids:
                pre = known.loc[(known['馬ID'].astype(str).str.replace(r'\.0$', '',regex=True)==hid) &
                                (pd.to_datetime(known['日付'],errors='coerce')<date)].copy()
            else:
                pre = pd.DataFrame()
            if len(pre) < 5:
                raw = get_results(hid,name)
                pre = prepare(convert_auto(raw))
                pre = pre.loc[pd.to_datetime(pre['日付'],errors='coerce')<date].copy()
            else:
                pre = prepare(pre)
            pre = pre.sort_values('日付',ascending=False).head(5).copy()
            if pre.empty:
                failed.append(f'{name}: レース前の成績なし')
                continue
            # 既知のS/M/Hを再利用。未知の場合のみ元ページから確認する。
            if 'netkeibaペース' not in pre.columns:
                pre['netkeibaペース']=''
            for idx, rec in pre.iterrows():
                raceid = str(rec.get('レースID','')).strip().removesuffix('.0')
                if pre.at[idx,'netkeibaペース'] in V195_TAG:
                    continue
                if raceid in local_paces:
                    pre.at[idx,'netkeibaペース']=local_paces[raceid]
                    continue
                shared = known.loc[known['レースID'].fillna('').astype(str).str.replace(r'\.0$', '',regex=True).eq(raceid)]
                matches = (shared['netkeibaペース'].dropna().astype(str) if 'netkeibaペース' in shared else pd.Series(dtype=str))
                matches = matches[matches.isin(V195_TAG)]
                if not matches.empty:
                    pre.at[idx,'netkeibaペース']=matches.iloc[0]
                    local_paces[raceid]=matches.iloc[0]
                # 不明なペースは、全頭を揃えた後に逃げ実績を優先して照会する。
                # 馬の取得順だけで照会枠を使い切るのを防ぐ。
            pre['馬名']=name
            frames.append(pre)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            failed.append(f'{name}: {str(exc)[:75]}')
        if progress_callback:
            progress_callback(index,len(entrants),name)
    full = pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    # 全馬の過去走を確認してから、先頭→前方→その他の順でペース照会。
    # 同じレースIDは1回だけ照会し、結果を全該当走に反映する。
    if not full.empty and 'netkeibaペース' in full.columns:
        normalized = full['netkeibaペース'].fillna('').astype(str).str.strip()
        missing = full.loc[~normalized.isin(V195_TAG)].copy()
        def _pace_priority(row):
            pieces = re.findall(r'\d+', str(row.get('通過順', '')))
            size = pd.to_numeric(row.get('頭数'), errors='coerce')
            if not pieces or pd.isna(size) or size < 2:
                return 3
            first = int(pieces[0])
            return 0 if first == 1 else (1 if first <= max(2, size*.35) else 2)
        if not missing.empty:
            missing['_pace_priority'] = missing.apply(_pace_priority, axis=1)
            missing = missing.sort_values('_pace_priority', kind='stable')
            for _, rec in missing.iterrows():
                raceid = str(rec.get('レースID', '')).strip().removesuffix('.0')
                if not re.fullmatch(r'\d{12}', raceid):
                    continue
                if raceid in local_paces:
                    pace_value = local_paces[raceid]
                elif pace_calls < limit_pace_calls:
                    pace_calls += 1
                    try:
                        pace_value = get_netkeiba_pace(raceid)
                        local_paces[raceid] = pace_value
                    except (requests.RequestException, ValueError, KeyError):
                        continue
                else:
                    break
                if pace_value in V195_TAG:
                    same_race = full['レースID'].fillna('').astype(str).str.replace(r'\.0$', '',regex=True).eq(raceid)
                    unknown = ~full['netkeibaペース'].fillna('').astype(str).isin(V195_TAG)
                    full.loc[same_race & unknown, 'netkeibaペース'] = pace_value
    known_horses = (full.groupby('馬名')['通過順'].apply(lambda s: int(s.fillna('').astype(str).str.contains(r'\d').sum()>=2)).sum()
                    if not full.empty and '通過順' in full else 0)
    predicted=None; explanation='事前の位置取り情報不足'; lead=front=0
    if not full.empty:
        names = full['馬名'].unique().tolist()
        # 予測器は全頭が揃った場合のみ実行可能、欠測は別に記録する。
        predicted,explanation,detail,known_count,lead,front=estimate_field_pace_v19(full,names)
    else:
        known_count=0
    # Ver.20: 結果を一切使用しない事前走歴ベースのH見逃し診断。
    # 最初に記録された通過地点の順位であって、実際の発走直後の先手ではない。
    diag197 = {
        '先頭経験馬数': 0, '複数回先頭馬数': 0, '逃げ候補先頭合計': 0,
        '逃げ候補先頭率平均(%)': None, '逃げ候補先頭率最大(%)': None,
        '先行候補以外の前方経験馬数': 0, '非逃げ馬の前方経験馬数': 0,
        '先行圧力延べ前方走': 0, '先行圧力延べ確認走': 0,
        '先行圧力率(%)': None,
        '逃げ候補_先頭S': 0, '逃げ候補_先頭M': 0, '逃げ候補_先頭H': 0,
        '逃げ候補_先頭判定済': 0, '逃げ候補_先頭判定不足': 0,
        '前方H率(%)': None, 'H実績なしの逃げ候補数': 0,
    }
    if not full.empty and not detail.empty:
        d197 = detail.copy()
        has_early197 = d197['初通過取得'].ge(2)
        d197 = d197.loc[has_early197].copy()
        d197['_lead_ratio197'] = d197['初通過先頭'] / d197['初通過取得']
        d197['_front_ratio197'] = d197['初通過前方'] / d197['初通過取得']
        leaders197 = d197.loc[d197['逃げ候補']]
        nonleaders197 = d197.loc[~d197['逃げ候補']]
        front197 = d197.loc[d197['先行候補']]
        pressure197 = d197.loc[~d197['逃げ候補']]
        diag197['先頭経験馬数'] = int(d197['初通過先頭'].ge(1).sum())
        diag197['複数回先頭馬数'] = int(d197['初通過先頭'].ge(2).sum())
        diag197['逃げ候補先頭合計'] = int(leaders197['初通過先頭'].sum())
        if not leaders197.empty:
            diag197['逃げ候補先頭率平均(%)'] = round(float(leaders197['_lead_ratio197'].mean()*100), 1)
            diag197['逃げ候補先頭率最大(%)'] = round(float(leaders197['_lead_ratio197'].max()*100), 1)
        diag197['先行候補以外の前方経験馬数'] = int(d197.loc[~d197['先行候補'], '初通過前方'].gt(0).sum())
        diag197['非逃げ馬の前方経験馬数'] = int(nonleaders197['初通過前方'].gt(0).sum())
        diag197['先行圧力延べ前方走'] = int(pressure197['初通過前方'].sum())
        diag197['先行圧力延べ確認走'] = int(pressure197['初通過取得'].sum())
        if diag197['先行圧力延べ確認走']:
            diag197['先行圧力率(%)'] = round(100*diag197['先行圧力延べ前方走']/diag197['先行圧力延べ確認走'],1)
        for tag197 in ('S','M','H'):
            diag197['逃げ候補_先頭'+tag197] = int(leaders197['先頭'+tag197].sum())
        diag197['逃げ候補_先頭判定済'] = int(leaders197['先頭ペース確認'].sum())
        diag197['逃げ候補_先頭判定不足'] = max(0,diag197['逃げ候補先頭合計']-diag197['逃げ候補_先頭判定済'])
        diag197['H実績なしの逃げ候補数'] = int(leaders197['先頭H'].eq(0).sum())
        knownfront197 = int(front197['前方ペース確認'].sum())
        if knownfront197:
            diag197['前方H率(%)'] = round(100*front197['前方H'].sum()/knownfront197,1)
    # Ver.20: 予測には一切使わず「検証対象レースより前」のラップを別途照会。
    # 同じ過去レースのラップは1度だけ取得。先頭・前方だった走を優先。
    # 前半600/1000mは【レース全体】のタイムであり、当該馬の個別タイムではない。
    diag198 = {'前半ラップ照会レース数': 0, '前半ラップ取得OKレース数': 0,
               '先頭時ラップ対象走数': 0, '先頭時ラップ確認走数': 0,
               '先頭時前半600m平均(秒)': None, '先頭時前半1000m平均(秒)': None,
               '前方時ラップ対象走数': 0, '前方時ラップ確認走数': 0,
               '前方時前半600m平均(秒)': None, '前方時前半1000m平均(秒)': None,
               '先頭時ラップ詳細': '', 'ラップ取得エラー': '',
               '先頭時前後半600m差平均(秒)': None, '前方時前後半600m差平均(秒)': None,
               '先頭時前半1000m相対差平均(秒/200m)': None, '前方時前半1000m相対差平均(秒/200m)': None,
               '同競馬場・近距離先頭時走数': 0, '同競馬場・近距離先頭時前後半600m差平均(秒)': None,
               '大逃げの隊列差': '未取得（通過順位やレースラップのみでは判定不可）',
               '対象レース馬場状態':target_going_v20,
               '先頭時馬場一致ラップ走数':0,'先頭時馬場一致前半1000m平均(秒)':None,
               '前半ラップ相対基準作成走数':0,'基準不足走数':0,
               '先頭時基準差600m平均(秒)':None,'先頭時基準差1000m平均(秒)':None,
               '前方時基準差1000m平均(秒)':None,'相対ラップ条件詳細':'',
               '基準信頼度_高':0,'基準信頼度_中':0,'基準信頼度_低':0,'基準信頼度_算出不可':0,
               '未来基準データ除外延べ件数':0,'基準に使った最終日':'',
               '馬場状態別先頭ラップ':'', '馬場状態別前方ラップ':''}
    if collect_early_laps and not full.empty and not detail.empty:
        roles = detail.set_index('馬名')[['逃げ候補', '先行候補']].to_dict('index')
        candidates198 = []
        # 予測対象のレース当日や未来のラップは参照しない。
        for _, record in full.iterrows():
            hname = str(record.get('馬名', ''))
            role = roles.get(hname, {})
            if not (role.get('逃げ候補') or role.get('先行候補')):
                continue
            past_date = pd.to_datetime(record.get('日付'), errors='coerce')
            if pd.isna(past_date) or not past_date < date:
                continue
            parts = re.findall(r'\d+', str(record.get('通過順', '')))
            size = pd.to_numeric(record.get('頭数'), errors='coerce')
            if not parts or pd.isna(size) or size < 2:
                continue
            first = int(parts[0])
            is_leader = first == 1 and bool(role.get('逃げ候補'))
            is_forward = first <= max(2, size * .35) and bool(role.get('先行候補'))
            if not (is_leader or is_forward):
                continue
            past_rid = str(record.get('レースID', '')).strip().removesuffix('.0')
            if not re.fullmatch(r'\d{12}', past_rid):
                continue
            candidates198.append({'id':past_rid, 'horse':hname,
                                  'lead':is_leader, 'front':is_forward,
                                  'date':past_date, 'distance':record.get('距離_m'),
                                  'venue':record.get('競馬場', ''),
                                  'course':record.get('コース',''),
                                  'going':normalize_going_v20(record.get('馬場状態', '')),
                                  'pace':record.get('netkeibaペース', '')})
        # 何走ラップを照会したかったのかを先に数える（取得上限に影響されない）。
        diag198['先頭時ラップ対象走数'] = sum(x['lead'] for x in candidates198)
        diag198['前方時ラップ対象走数'] = sum(x['front'] for x in candidates198)
        candidates198.sort(key=lambda x:(not x['lead'], not x['front'], -x['date'].timestamp()))
        chosen_ids198 = list(dict.fromkeys(x['id'] for x in candidates198))[:limit_lap_calls]
        lap_by_id198 = {}
        errors198=[]
        for past_rid in chosen_ids198:
            diag198['前半ラップ照会レース数'] += 1
            try:
                lap_data = get_lap_table_test_v1594(past_rid)
                if lap_data.get('状態') != '検証OK' or lap_data.get('検証') != 'OK':
                    continue
                distance = pd.to_numeric(lap_data.get('レース距離'), errors='coerce')
                laps = [float(v.strip()) for v in str(lap_data.get('200mラップ','')).split(' - ')]
                if pd.isna(distance) or distance % 200 or len(laps) != int(distance)//200 or len(laps) < 5:
                    continue
                if not all(8 <= v <= 25 for v in laps):
                    continue
                # 前後600m比較は同一レース内の形状指標。符号が負なら前半の方が速い。
                front600 = sum(laps[:3]); last600 = sum(laps[-3:])
                # 前半1000mの200m平均と、残り区間の200m平均を比較。
                # 距離が1200m以上（6区間以上）のときだけ計算。
                second_mean = sum(laps[5:]) / len(laps[5:]) if len(laps)>5 else None
                early1000diff = (sum(laps[:5])/5 - second_mean) if second_mean is not None else None
                lap_by_id198[past_rid] = {'600':front600, '1000':sum(laps[:5]),
                                          '前後600差':front600-last600,
                                          '1000相対差':early1000diff}
                diag198['前半ラップ取得OKレース数'] += 1
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                errors198.append(f'{past_rid}: {str(exc)[:55]}')
        valid_lead=[]; valid_front=[]
        for item in candidates198:
            metric = lap_by_id198.get(item['id'])
            if metric is None:
                continue
            if item['lead']:
                valid_lead.append((item,metric))
            if item['front']:
                valid_front.append((item,metric))
        for label, group in [('先頭時',valid_lead),('前方時',valid_front)]:
            diag198[label+'ラップ確認走数'] = len(group)
            if group:
                for dist in ['600','1000']:
                    diag198[label+f'前半{dist}m平均(秒)'] = round(sum(m[dist] for _,m in group)/len(group),2)
                diag198[label+'前後半600m差平均(秒)'] = round(sum(m['前後600差'] for _,m in group)/len(group),2)
                comparative = [m['1000相対差'] for _,m in group if m['1000相対差'] is not None]
                if comparative:
                    diag198[label+'前半1000m相対差平均(秒/200m)'] = round(sum(comparative)/len(comparative),3)
        # Ver.20: 取得した過去ラップをレースID単位でプール化して基準を作る。
        # 12件上限かつ逃げ優先の便宜抽出なので、全レースの標準値ではない。
        pool_v20={}
        for item in candidates198:
            lap=lap_by_id198.get(item['id'])
            if lap is None or item['id'] in pool_v20:
                continue
            dd=pd.to_numeric(item.get('distance'),errors='coerce')
            if pd.isna(dd):
                continue
            pool_v20[item['id']]={**lap, 'distance':float(dd), 'venue':item['venue'],
                                  'course':item['course'],'going':item['going'],'date':item['date']}
        benchmark_v20=[]
        for item in candidates198:
            lap=lap_by_id198.get(item['id'])
            if lap is None:
                continue
            b=benchmark_laps_v20({**item,**lap},pool_v20)
            benchmark_v20.append((item,lap,b))
        for _,_,b in benchmark_v20:
            key='基準信頼度_'+b['基準信頼度']
            diag198[key] = diag198.get(key,0)+1
            diag198['未来基準データ除外延べ件数'] += b['未来データ除外数']
        dates201=[b['基準の最終日'] for _,_,b in benchmark_v20 if b['基準の最終日']]
        diag198['基準に使った最終日']=max(dates201) if dates201 else ''
        diag198['前半ラップ相対基準作成走数']=sum(b['基準状態']=='参考（比較可能）' for _,_,b in benchmark_v20)
        diag198['基準不足走数']=sum(b['基準状態']!='参考（比較可能）' for _,_,b in benchmark_v20)
        for label,flag in [('先頭時','lead'),('前方時','front')]:
            group=[(i,l,b) for i,l,b in benchmark_v20 if i[flag]]
            valid=[(i,l,b) for i,l,b in group if b['基準状態']=='参考（比較可能）']
            if valid:
                diag198[label+'基準差1000m平均(秒)']=round(sum(b['前半1000m平均との差(秒)'] for _,_,b in valid)/len(valid),3)
                if label=='先頭時':
                    diag198['先頭時基準差600m平均(秒)']=round(sum(b['前半600m平均との差(秒)'] for _,_,b in valid)/len(valid),3)
            going_breakdown=[]
            for going in ['良','稍重','重','不良','不明']:
                matches=[l for i,l,b in group if i['going']==going]
                if matches:
                    going_breakdown.append(f"{going}:{len(matches)}走/前半1000m{sum(m['1000'] for m in matches)/len(matches):.2f}秒")
            diag198['馬場状態別'+label+'ラップ']=' / '.join(going_breakdown)
        same_going=[lap for item,lap in valid_lead if item['going']==target_going_v20]
        diag198['先頭時馬場一致ラップ走数']=len(same_going)
        if same_going:
            diag198['先頭時馬場一致前半1000m平均(秒)']=round(sum(x['1000'] for x in same_going)/len(same_going),2)
        diag198['相対ラップ条件詳細']=' / '.join(
            f"{i['horse']}({i['id']},{i['going']}):{b['基準条件']}/n={b['基準レース数']}/信頼度={b['基準信頼度']}/差={b['前半1000m平均との差(秒)']}"
            for i,l,b in benchmark_v20 if i['lead'])[:1800]
        # 検証対象と同じ競馬場、距離差200m以内だけの前半・後半差を別集計。
        # サンプルが少なければ欠測のままにする。対象レース当日の結果は使わない。
        relevant199=[]
        for item,metric in valid_lead:
            item_dist=pd.to_numeric(item.get('distance'),errors='coerce')
            if (pd.notna(item_dist) and distance_value is not None
                and abs(item_dist-distance_value)<=200
                and str(item.get('venue',''))==str(venue_value)):
                relevant199.append(metric['前後600差'])
        diag198['同競馬場・近距離先頭時走数']=len(relevant199)
        if relevant199:
            diag198['同競馬場・近距離先頭時前後半600m差平均(秒)']=round(sum(relevant199)/len(relevant199),2)
        # 同じレースに複数頭出走した場合は【馬の延べ走数】として集計。
        diag198['先頭時ラップ詳細'] = ' / '.join(
            f"{x['horse']}({x['id']}・{x['pace']}):600m{metric['600']:.1f}秒/1000m{metric['1000']:.1f}秒"
            for x,metric in valid_lead)[:1600]
        diag198['ラップ取得エラー'] = ' / '.join(errors198)[:400]
    # 結果の診断: 実際の逃げ候補でS/M/Hの記録が不足しているか可視化。
    audit_lead_known = audit_lead_missing = 0
    audit_lead_names = ''
    if not full.empty:
        candidates = detail.loc[detail['逃げ候補'], '馬名'].tolist()
        audit_lead_names = ' / '.join(candidates)
        for horse in candidates:
            past = full.loc[full['馬名'].eq(horse)].sort_values('日付',ascending=False).head(5)
            for _, record in past.iterrows():
                parts = re.findall(r'\d+', str(record.get('通過順', '')))
                if parts and int(parts[0]) == 1:
                    if str(record.get('netkeibaペース', '')) in V195_TAG:
                        audit_lead_known += 1
                    else:
                        audit_lead_missing += 1
    # Ver.20.3: audit the *actual prediction input rows* before exporting.
    # Never treat missing dates as proof that the temporal check passed.
    audit_dates = (pd.to_datetime(full['日付'], errors='coerce')
                   if not full.empty and '日付' in full.columns else pd.Series(dtype='datetime64[ns]'))
    audit_invalid = int(audit_dates.isna().sum())
    audit_future = int((audit_dates >= date).sum())
    audit_latest = (audit_dates.max().strftime('%Y-%m-%d')
                    if not audit_dates.dropna().empty else '')
    audit_lap_latest = str(diag198.get('基準に使った最終日', ''))
    audit_lap_date = pd.to_datetime(audit_lap_latest, errors='coerce')
    audit_lap_invalid = bool(audit_lap_latest and (pd.isna(audit_lap_date) or audit_lap_date >= date))
    audit_status = ('要確認' if audit_future or audit_invalid or audit_lap_invalid
                    else ('確認済（取得範囲）' if not full.empty else '走歴なし'))
    # 実際の全出走馬に対して予測器を再生。計算に欠けた馬はカバレッジに反映。
    pred_tag = {'スロー（S）':'S','ミドル（M）':'M','ハイ（H）':'H'}.get(predicted)
    status = ('全頭・事前走歴確認' if known_count==len(entrants) and not failed else
              '一部欠測（参考）')
    return {'レースID':rid,'開催日':date.strftime('%Y-%m-%d'),
            'レース名':str(target_rows['レース名'].dropna().iloc[0]) if 'レース名' in target_rows and target_rows['レース名'].notna().any() else '',
            '競馬場':venue_value,'距離(m)':distance_value,'距離区分':distance_group,
            'コース':target_course_v20,'馬場状態':target_going_v20,
            '全出走頭数':len(entrants),'事前走歴取得頭数':len(frames),
            '序盤位置2走以上':int(known_count),'実際':actual,
            '予測':pred_tag or '保留','一致':('一致' if pred_tag==actual else '不一致') if pred_tag else '対象外',
            '取得状況':status,'逃げ候補':lead,'先行候補':front,
            'S/M/H追加照会':pace_calls,
            '過去走ペース判定あり':int(full['netkeibaペース'].isin(V195_TAG).sum()) if not full.empty and 'netkeibaペース' in full.columns else 0,
            '過去走対象':len(full),
            '逃げ候補馬名':audit_lead_names,
            '逃げ先頭時ペース取得':audit_lead_known,
            '逃げ先頭時ペース未取得':audit_lead_missing,
            '取得失敗詳細':' / '.join(failed)[:500],
            '予測根拠':explanation,
            '時系列監査':audit_status,
            '事前走歴最新日':audit_latest,
            '事前走歴日付不明走数':audit_invalid,
            '事前走歴当日以降混入走数':audit_future,
            '基準最終日_時系列違反':int(audit_lap_invalid),
            '監査対象の事前走歴数':len(full),
            **diag197, **diag198}




def lap_score_v203(record):
    """Exploratory lap score; never changes any pace prediction.

    Score is the negative of the mean 1000m difference relative to eligible
    historical reference races. Positive means faster than its reference.
    This is descriptive, NOT a probability or a calibrated pace classification.
    """
    high = pd.to_numeric(record.get('基準信頼度_高'), errors='coerce')
    medium = pd.to_numeric(record.get('基準信頼度_中'), errors='coerce')
    good = (int(high) if pd.notna(high) else 0) + (int(medium) if pd.notna(medium) else 0)
    value = pd.to_numeric(record.get('前方時基準差1000m平均(秒)'), errors='coerce')
    result = {'ラップ試験スコア(秒速さ)': None,
              'ラップスコア状態': '基準不足',
              'ラップ参考基準数(延べ走)': good,
              'ラップ参考説明': '現行予測に未反映。高・中信頼度の比較が3走未満か基準差が欠測。',
              '試験モデル予測': record.get('予測', '保留'),
              'ラップ補正適用': '変更しない',
              'ラップ補正理由': 'Ver.20.3は予測変更を停止。ラップは参考スコアのみ。',
              '比較用基準品質': f'高・中 延べ{good}走'}
    if good >= 3 and pd.notna(value):
        result['ラップ試験スコア(秒速さ)'] = round(-float(value), 3)
        result['ラップスコア状態'] = '探索用・参考'
        result['ラップ参考説明'] = ('プラスほど過去に前方だったレースの前半1000mが基準より速い。'
                                 '馬個別のラップやH確率ではない。基準の独立性・偏りに注意。')
    # Ver.20.5: score reliability is separate from pace prediction.
    # Low coverage or non-independent benchmarks are not promoted to strong evidence.
    h = int(high) if pd.notna(high) else 0
    m = int(medium) if pd.notna(medium) else 0
    invalid_date = pd.to_numeric(record.get('事前走歴当日以降混入走数', 0), errors='coerce')
    invalid_base = pd.to_numeric(record.get('基準最終日_時系列違反', 0), errors='coerce')
    unknown_date = pd.to_numeric(record.get('事前走歴日付不明走数', 0), errors='coerce')
    invalid_date = int(invalid_date) if pd.notna(invalid_date) else 0
    invalid_base = int(invalid_base) if pd.notna(invalid_base) else 0
    unknown_date = int(unknown_date) if pd.notna(unknown_date) else 0
    total = pd.to_numeric(record.get('前方時ラップ対象走数', 0), errors='coerce')
    available = pd.to_numeric(record.get('前方時ラップ確認走数', 0), errors='coerce')
    total = int(total) if pd.notna(total) else 0
    available = int(available) if pd.notna(available) else 0
    coverage = round(available / total * 100, 1) if total else None
    issues = []
    if invalid_date or invalid_base:
        grade = '要確認'
        issues.append('時系列監査に違反あり')
        result['ラップ試験スコア(秒速さ)'] = None
        result['ラップスコア状態'] = '監査要確認'
    elif unknown_date:
        grade = '低'
        issues.append('日付不明の走歴あり')
    elif result['ラップ試験スコア(秒速さ)'] is None:
        grade = '算出不可'
        issues.append('高・中信頼度の比較が3走未満、または基準差なし')
    elif h >= 5 and good >= 8 and (coverage is not None and coverage >= 70):
        grade = '高（暫定）'
    elif h >= 3 and good >= 5:
        grade = '中'
    else:
        grade = '低'
        if h == 0:
            issues.append('同競馬場・同馬場の高信頼度比較なし')
        if good < 5:
            issues.append('比較基準の件数が少ない')
        if coverage is None or coverage < 70:
            issues.append('前方時ラップ取得率70%未満または不明')
    result['ラップスコア信頼度'] = grade
    result['高信頼度比較走数'] = h
    result['中信頼度比較走数'] = m
    result['前方時ラップ取得率(%)'] = coverage
    result['スコア信頼度の注意'] = '／'.join(issues) if issues else '算出条件を満たすが、標本の独立性・分布は未検証'
    return result


def attach_comparison_v202(frame):
    """Keep previous CSV column names for compatibility, but disable changes."""
    result = frame.copy()
    if result.empty:
        return result
    detail = pd.DataFrame([lap_score_v203(r) for r in result.to_dict('records')], index=result.index)
    for column in detail.columns:
        result[column] = detail[column]
    result['試験モデル一致'] = result.apply(
        lambda r: ('一致' if r['試験モデル予測'] == r.get('実際') else '不一致')
        if r['試験モデル予測'] in ('S','M','H') and r.get('実際') in ('S','M','H') else '対象外', axis=1)
    return result


def tap_choice(label, options, key, format_func=None):
    """iPhone向け：検索欄のないポップオーバー内ラジオ選択。"""
    choices = list(options)
    if not choices:
        return None
    if key not in st.session_state or st.session_state[key] not in choices:
        st.session_state[key] = choices[0]
    current = st.session_state[key]
    shown = str(format_func(current) if format_func else current)
    with st.popover(f'{label}：{shown}', width='stretch'):
        # 選択はラジオボタン。iPhoneの文字入力キーボードは開かない。
        radio_args = {'key': key, 'label_visibility': 'collapsed'}
        if format_func is not None:
            radio_args['format_func'] = format_func
        st.radio(label, choices, **radio_args)
    return st.session_state[key]


def tap_horses(label, options, key, limit=3):
    """比較馬も検索入力を使わずチェックボックスで選択する。"""
    choices = list(options)
    if key not in st.session_state:
        st.session_state[key] = choices[:2]
    prior = [x for x in st.session_state[key] if x in choices][:limit]
    with st.expander(f'{label}：' + ('、'.join(prior) if prior else '未選択')):
        selected = []
        for i, horse in enumerate(choices):
            checked = st.checkbox(horse, value=horse in prior, key=f'{key}_check_{i}')
            if checked:
                selected.append(horse)
        if len(selected) > limit:
            st.warning(f'{limit}頭まで選べます。先頭の{limit}頭を使用します。')
        selected = selected[:limit]
        st.session_state[key] = selected
    return st.session_state[key]


# サーバー上の簡易キャッシュ。Streamlit Cloudの再起動・再デプロイで消える場合があります。
CACHE_DIR = Path('/tmp/keiba_analysis_v141')
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_paths(rid):
    if not re.fullmatch(r'\d{12}', str(rid)):
        raise ValueError('不正なレースIDです')
    return CACHE_DIR / f'{rid}.csv.gz', CACHE_DIR / f'{rid}.json'


def save_race_cache(rid, horses, data):
    if not rid or data is None or data.empty:
        return
    csv_path, meta_path = cache_paths(rid)
    temp_csv = csv_path.with_suffix('.tmp')
    temp_meta = meta_path.with_suffix('.tmp')
    data.to_csv(temp_csv, index=False, compression='gzip')
    temp_csv.replace(csv_path)
    temp_meta.write_text(json.dumps(horses, ensure_ascii=False), encoding='utf-8')
    temp_meta.replace(meta_path)


def load_race_cache(rid):
    csv_path, meta_path = cache_paths(rid)
    if not (csv_path.exists() and meta_path.exists()):
        return None
    horses = json.loads(meta_path.read_text(encoding='utf-8'))
    data = pd.read_csv(csv_path, compression='gzip', dtype={'馬ID': str, 'レースID': str})
    return horses, prepare(data)


def saved_race_ids():
    return sorted([p.stem for p in CACHE_DIR.glob('*.json') if re.fullmatch(r'\d{12}', p.stem) and (CACHE_DIR / f'{p.stem}.csv.gz').exists()], reverse=True)




def get_saved_race_label(rid):
    """レース名を表示用に取得。元の保存済み成績は変更しない。"""
    label_file = CACHE_DIR / f'{rid}.label.json'
    if label_file.exists():
        try:
            label = json.loads(label_file.read_text(encoding='utf-8')).get('label', '')
            if label:
                return label
        except (ValueError, OSError, TypeError):
            pass

    # 古い保存データにも対応。取得できなければIDを併記して識別可能にする。
    year = rid[:4]
    fallback = f'{year}年のレース（{rid}）'
    try:
        res = requests.get(
            f'https://race.netkeiba.com/race/shutuba.html?race_id={rid}',
            headers=HEADERS, timeout=8
        )
        res.raise_for_status()
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        # 例：「天皇賞(秋)(GI) 出馬表 | 2026年11月1日 ...」
        race_name = re.split(r'\s*[|｜]\s*|\s+出馬表|\s+結果|\s+予想', title, maxsplit=1)[0].strip()
        race_name = re.sub(r'\s*[-－–]\s*netkeiba.*$', '', race_name, flags=re.I).strip()
        if race_name and not any(x in race_name.lower() for x in ['netkeiba', '競馬情報', '競馬データベース', '404', 'エラー']):
            date_match = re.search(r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日', title)
            if date_match:
                y, m, d = (int(v) for v in date_match.groups())
                date_label = f'{y}年{m}月{d}日'
            else:
                date_label = f'{year}年'
            label = f'{race_name}｜{date_label}（{rid}）'
            label_file.write_text(json.dumps({'label': label}, ensure_ascii=False), encoding='utf-8')
            return label
    except (requests.RequestException, OSError):
        pass
    return fallback


def classify_grade(text):
    """レース名とHTML上の明示的な格付け情報だけから判定。推測しない。"""
    value = str(text or '')
    normalized = (value.upper().replace('Ｇ', 'G').replace('Ⅰ', 'I').replace('Ⅱ', 'II').replace('Ⅲ', 'III')
                  .replace('１', '1').replace('２', '2').replace('３', '3'))
    # G1, GI, ＧⅠ, Jpn1, JpnI, [G2] などに対応
    hit = re.search(r'(?<![A-Z0-9])(?:G|JPN)\s*[-(（\[]?\s*(III|II|I|[123])(?=[^A-Z0-9]|$)', normalized)
    if hit:
        val = hit.group(1)
        return {'I': 'G1', '1': 'G1', 'II': 'G2', '2': 'G2', 'III': 'G3', '3': 'G3'}[val]
    # 明示的に重賞以外とわかる表記のみをその他とする
    if re.search(r'(?:\bOP\b|\bL\b|リステッド|オープン|\d勝クラス|[１２３]勝クラス|未勝利|新馬|特別|障害未勝利|500万下|1000万下|1600万下)', normalized):
        return 'その他'
    return '不明'


def parse_race_id(raw):
    value = raw.strip()
    if value.isdigit() and len(value) == 12:
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    if host == 'netkeiba.com' or host.endswith('.netkeiba.com'):
        rid = parse_qs(parsed.query).get('race_id', [''])[0]
        if rid.isdigit() and len(rid) == 12:
            return rid
    return ''


def clean_horse_name(raw):
    """馬番が混入したリンク文字列を正規化。省略形は採用しない。"""
    raw = re.sub(r'\s+', '', str(raw or ''))
    return re.sub(r'^[0-9０-９]{1,2}(?:番)?', '', raw).strip()


def horse_name_candidate(a):
    """出馬表の正式表記（リンクのtitleなど）を優先。"""
    names = [a.get('title', ''), a.get('aria-label', ''), a.get_text(' ', strip=True)]
    for el in [a.find('span', class_=re.compile('HorseName', re.I)), a.find('img')]:
        if el is not None:
            names.extend([el.get('title', ''), el.get('alt', '')])
    names = [clean_horse_name(x) for x in names]
    names = [x for x in names if re.fullmatch(r'[\u3040-\u30ff\u3400-\u9fffー・Ａ-Ｚａ-ｚA-Za-z]+', x)]
    return max(names, key=len, default='')


@st.cache_data(ttl=1800, show_spinner=False)
def get_horses(race_id):
    """馬名は正式表記の最良候補を採用し、同じ馬IDの後続短縮リンクで上書きしない。"""
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    horses, numbers = {}, {}
    # 出馬表の各馬の行を優先（馬番は枠番ではなく「馬番」欄から取得）。
    for row in soup.select('tr.HorseList, tr[class*="HorseList"]'):
        links = row.select('td.HorseInfo a[href*="/horse/"], td[class*="HorseInfo"] a[href*="/horse/"]')
        if not links:
            links = row.select('a[href*="/horse/"]')
        for a in links:
            hit = re.search(r'/horse/(\d{10})', a.get('href', ''))
            if not hit:
                continue
            hid = hit.group(1)
            name = horse_name_candidate(a)
            if name and (hid not in horses or len(name) > len(horses[hid])):
                horses[hid] = name
            cell = row.select_one('td.Umaban, td[class*="Umaban"], .Umaban')
            if cell is not None:
                txt = cell.get_text(' ', strip=True)
                num = re.search(r'(?<!\d)(1[0-9]|[1-9]|2[0-9])(?!\d)', txt)
                if num:
                    numbers[hid] = int(num.group(1))
            break
    # HTML構造が変わっても最低限取得できるようにする。
    for a in soup.select('a[href*="/horse/"]'):
        hit = re.search(r'/horse/(\d{10})', a.get('href', ''))
        if not hit:
            continue
        hid = hit.group(1)
        name = horse_name_candidate(a)
        if name and (hid not in horses or len(name) > len(horses[hid])):
            horses[hid] = name
    if not horses:
        raise ValueError('出走馬を取得できませんでした。ページ構造やアクセス制限を確認してください。')
    return horses, numbers


def update_cached_names(data, horses):
    """既存キャッシュに短縮馬名があっても馬IDを使って正式名へ復元。"""
    if data is None or '馬ID' not in data.columns or '馬名' not in data.columns:
        return data
    result = data.copy()
    ids = result['馬ID'].astype(str).str.replace(r'\.0$', '', regex=True)
    fixed = ids.map(horses)
    result['馬名'] = fixed.fillna(result['馬名'])
    return result


def horse_display(name, horses, numbers):
    hid = next((hid for hid, official in horses.items() if official == name), None)
    num = numbers.get(hid) if hid else None
    return f'{num} {name}' if num else name


def display_horse_column(df, horses, numbers):
    if '馬名' not in df.columns or not horses:
        return df
    shown = df.copy()
    shown['馬名'] = shown['馬名'].map(lambda n: horse_display(n, horses, numbers))
    return shown


@st.cache_data(ttl=3600, show_spinner=False)
def get_results(horse_id, horse_name):
    url = f'https://db.netkeiba.com/horse/result/{horse_id}/'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = 'EUC-JP'
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one('table.db_h_race_results')
    if table is None:
        raise ValueError('成績表が見つかりません')
    rows = table.select('tr')
    if len(rows) < 2:
        raise ValueError('成績データがありません')
    columns = [c.get_text(' ', strip=True) for c in rows[0].select('th, td')]
    records = []
    for row in rows[1:]:
        values = [c.get_text(' ', strip=True) for c in row.select('th, td')]
        if len(values) == len(columns):
            record = dict(zip(columns, values))
            record['馬名'] = horse_name
            record['馬ID'] = horse_id
            links = row.select("a[href*='race_id='], a[href*='/race/']")
            race_id = ''
            for link in links:
                href = link.get('href', '')
                match = re.search(r'(?:race_id=|/race/)(\d{12})', href)
                if match:
                    race_id = match.group(1)
                    break
            record['レースID'] = race_id
            # レース名の文字列だけでなく、アイコン等のtitle/altも参照する。
            race_cell = None
            if 'レース名' in columns:
                cells = row.select('th, td')
                idx = columns.index('レース名')
                if idx < len(cells):
                    race_cell = cells[idx]
            grade_text = record.get('レース名', '')
            if race_cell is not None:
                grade_text += ' ' + ' '.join(
                    str(tag.get(attr, '')) for tag in race_cell.select('[title], [alt]')
                    for attr in ('title', 'alt') if tag.get(attr)
                )
                # アイコンのCSS classに grade1/grade2 などが明示されていれば利用。
                grade_text += ' ' + ' '.join(
                    'G' + match.group(1)
                    for tag in race_cell.find_all(True)
                    for match in [re.search(r'(?:gradetype|grade)[_-]?([123])\b', ' '.join(tag.get('class', [])), re.I)]
                    if match
                )
            record['格付け根拠'] = grade_text.strip()
            record['レース格'] = classify_grade(grade_text)
            records.append(record)
    if not records:
        raise ValueError('成績の抽出件数が0件です')
    return pd.DataFrame(records)


@st.cache_data(ttl=86400, show_spinner=False)
def get_race_agari(race_id):
    """Return race-wide 3F ranks, keyed by horse ID or exact horse name.

    Uses the result table whose headers include 馬名 and 後3F.
    Missing / nonnumeric 3F times do not receive a rank.
    """
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one('#All_Result_Table')
    if table is None:
        raise ValueError('結果テーブルが見つかりません')

    # Header text sometimes contains spaces/newlines; normalize these.
    header_rows = table.select('thead tr')
    if not header_rows:
        header_rows = table.select('tr')[:2]
    header = None
    for tr in header_rows:
        labels = [re.sub(r'\s+', '', cell.get_text(' ', strip=True))
                  for cell in tr.find_all(['th', 'td'], recursive=False)]
        if '馬名' in labels and ('後3F' in labels or '上り' in labels):
            header = labels
            break
    if header is None:
        raise ValueError('馬名・後3Fの列が見つかりません')
    name_index = header.index('馬名')
    speed_index = header.index('後3F') if '後3F' in header else header.index('上り')

    rows = table.select('tbody tr')
    if not rows:
        rows = table.select('tr')[1:]
    times = []
    for tr in rows:
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) <= max(name_index, speed_index):
            continue
        name_cell = cells[name_index]
        name = name_cell.get_text(' ', strip=True)
        if not name:
            continue
        raw_time = cells[speed_index].get_text(' ', strip=True)
        raw_time = re.sub(r'\s+', '', raw_time)
        # Accept plain numeric seconds only; skip --, empty values, etc.
        if not re.fullmatch(r'\d{2}\.\d', raw_time):
            continue
        speed = float(raw_time)
        if not 20 <= speed <= 60:
            continue
        horse_link = name_cell.select_one('a[href*="/horse/"]')
        match = re.search(r'/horse/(\d{10})', horse_link.get('href', '')) if horse_link else None
        horse_id = match.group(1) if match else None
        times.append((horse_id, name, speed))

    if len(times) < 2:
        raise ValueError('後3Fの数値を2頭以上取得できません')
    result = {}
    for horse_id, name, speed in times:
        rank = 1 + sum(other_speed < speed for _, _, other_speed in times)
        result[f'name:{name}'] = rank
        if horse_id:
            result[horse_id] = rank
    return result


def add_recent_agari_ranks(data, max_per_horse=5):
    data = data.copy()
    if 'レースID' not in data.columns or '馬ID' not in data.columns:
        raise ValueError('レースID・馬IDがありません。Ver.13で成績を再取得してください。')
    data['上がり順位'] = pd.to_numeric(data['上がり順位'], errors='coerce')
    work = data.sort_values('日付', ascending=False, kind='stable').groupby('馬名', sort=False).head(max_per_horse)
    work = work[work['上がり順位'].isna() & work['レースID'].astype(str).str.fullmatch(r'\d{12}')]
    race_ids = work['レースID'].astype(str).drop_duplicates().tolist()
    if not race_ids:
        return data, 0, []
    progress = st.progress(0)
    status = st.empty()
    ranks_by_race = {}
    errors = []
    for i, rid in enumerate(race_ids, 1):
        status.write(f'上がり順位を照合中：{i}/{len(race_ids)}レース')
        try:
            ranks_by_race[rid] = get_race_agari(rid)
        except Exception as e:
            errors.append(f'{rid}：{e}')
        progress.progress(i / len(race_ids))
        if i < len(race_ids):
            time.sleep(0.5)
    status.empty()
    for idx, row in work.iterrows():
        race_ranks = ranks_by_race.get(str(row['レースID']), {})
        rank = race_ranks.get(str(row['馬ID']))
        if rank is None:
            rank = race_ranks.get(f"name:{row['馬名']}")
        if rank is not None:
            data.at[idx, '上がり順位'] = rank
    return data, len(race_ids), errors


@st.cache_data(ttl=86400, show_spinner=False)
def get_netkeiba_pace(race_id):
    """netkeibaの結果ページに明示されたS/M/Hだけを採用。独自推定しない。"""
    if not re.fullmatch(r"\d{12}", str(race_id)):
        raise ValueError('レースIDの形式が不正')
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    res = requests.get(url, headers=HEADERS, timeout=18)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    # 結果ページ内には走行データのS表示もあるため、ラップタイム節に限定。
    heading = next((x for x in soup.find_all(['h2', 'h3', 'h4'])
                    if 'ラップタイム' in x.get_text(' ', strip=True)), None)
    if heading is None:
        raise ValueError('ラップタイム欄が見つかりません')
    excerpts = []
    node = heading
    for _ in range(14):
        node = node.find_next_sibling()
        if node is None:
            break
        if node.name in ('h2', 'h3'):
            break
        excerpts.append(node.get_text(' ', strip=True))
        if sum(map(len, excerpts)) > 2400:
            break
    section = ' '.join(excerpts)
    m = re.search(r'ペース\s*[:：]\s*([SMH])(?=\s|$|[（(])', section, re.I)
    if not m:
        # HTML構造によって見出しの親要素に含まれる場合を想定。
        container = heading.parent
        parent_text = container.get_text(' ', strip=True) if container else ''
        start = parent_text.find('ラップタイム')
        if start >= 0:
            m = re.search(r'ペース\s*[:：]\s*([SMH])(?=\s|$|[（(])', parent_text[start:start+1400], re.I)
    if not m:
        raise ValueError('ラップ欄にS/M/Hの明示表記が見つかりません')
    return {'S': 'スロー寄り', 'M': 'ミドル寄り', 'H': 'ハイ寄り'}[m.group(1).upper()]


def parse_lap_html_v1591(html, race_id):
    """4列ごとの繰り返し見出しを含むSP版ラップ表を、区間距離で結合・照合。"""
    soup = BeautifulSoup(html, 'html.parser')
    heading = next((h for h in soup.find_all(['h2', 'h3', 'h4'])
                    if 'ラップタイム' in h.get_text(' ', strip=True)), None)
    if heading is None:
        raise ValueError('ラップタイム見出しが見つかりません')

    number_pattern = r'(?<![0-9])(?:[0-9]{1,2}:)?[0-9]{1,3}\.[0-9](?![0-9])'
    def seconds(t):
        t = str(t)
        if ':' in t:
            minute, second = t.split(':', 1)
            return int(minute)*60 + float(second)
        return float(t)

    def numbers(el):
        return [seconds(x) for x in re.findall(number_pattern, el.get_text(' ', strip=True))]

    # レースの距離はページ内の「芝2000m」「ダート1800m」等から取得する。
    page_text = soup.get_text(' ', strip=True)
    dist_match = re.search(r'(?:芝|ダート|ダ\.?|障害|障)\s*[:：]?\s*(\d{3,4})\s*m', page_text, re.I)
    race_distance = int(dist_match.group(1)) if dist_match else None

    # 部分的に壊れた表があっても、区間距離をキーにして結合する。
    segments = {}
    samples = []
    tables = []
    for el in heading.next_elements:
        if getattr(el, 'name', None) in ('h2', 'h3') and el is not heading:
            break
        if getattr(el, 'name', None) == 'table':
            tables.append(el)
        if len(tables) >= 20:
            break

    for table in tables:
        rows = table.find_all('tr')
        if not rows:
            continue
        blocks = []
        for idx, tr in enumerate(rows):
            cells = tr.find_all(['td', 'th'], recursive=False)
            vals = []
            for cell in cells:
                m = re.fullmatch(r'\s*(\d{3,4})\s*m\s*', cell.get_text(' ', strip=True), re.I)
                if m:
                    vals.append(int(m.group(1)))
            if len(vals) >= 1 and all(v % 200 == 0 for v in vals):
                blocks.append((idx, vals))
        if not blocks:
            continue
        samples.append(table.get_text(' ', strip=True)[:900])
        for block_idx, (header_index, distances) in enumerate(blocks):
            end = blocks[block_idx+1][0] if block_idx+1 < len(blocks) else len(rows)
            width = len(distances)
            lines = []
            for tr in rows[header_index+1:end]:
                cells = tr.find_all(['th','td'], recursive=False)
                if len(cells) >= 1:
                    vals = [numbers(x) for x in cells]
                    if any(vals):
                        lines.append(vals)
            if not lines:
                continue
            # 最終ブロックが「1800m」の1列だけになる場合を処理。
            # 累積と区間ラップが同一セル、別セル、別行のいずれでも拾う。
            if width == 1:
                flat = [v for line in lines for cell in line for v in cell]
                if len(flat) >= 2:
                    cv, lv = flat[0], flat[1]
                    if 8 <= lv <= 25 and cv >= lv:
                        segments[distances[0]] = (cv, lv)
                        continue
            # ① 通過タイムの横1行＋ラップの横1行
            if len(lines) >= 2 and all(len(v)==1 for v in lines[0]) and all(len(v)==1 for v in lines[1]) and len(lines[0]) >= width and len(lines[1]) >= width and all(lines[1][i][0] < 30 for i in range(width)):
                for d, cv, lv in zip(distances, lines[0], lines[1]):
                    segments[d] = (cv[0], lv[0])
                continue
            # ② 1セルの中に累積・200mの2数値が入る横長行
            combined = next((line for line in lines if len(line)>=width and all(len(v)>=2 for v in line[:width])), None)
            if combined:
                for d, v in zip(distances, combined):
                    segments[d] = (v[0], v[-1])
                continue
            # ③ 4区間を縦4行で保持 (各行に累積・ラップ)
            if len(lines) >= width and all(len(line)>=2 and len(line[0])==1 and len(line[1])==1 for line in lines[:width]):
                for d, line in zip(distances, lines[:width]):
                    segments[d] = (line[0][0], line[1][0])

    # 最終区間の補完：CSVにも残るラップ表全文を第2経路で解析する。
    # 最後の見出しが1列だけになっている場合のHTML構造差を吸収する。
    lap_recovery = '不要'
    if race_distance and race_distance % 200 == 0 and race_distance not in segments:
        last = race_distance
        prev = last - 200
        if prev in segments:
            # 先に各テーブルの文字列を採取する。構造を解析できない最終1列表も対象。
            all_table_text = [t.get_text(' ', strip=True) for t in tables]
            # 「1800m 1:45.5 11.6」のような3点組を解析。
            # タイム差による検算なしの補完は絶対にしない。
            distance_expr = r'(?<![0-9])' + str(last) + r'\s*m'
            time_expr = r'((?:[0-9]{1,2}:)?[0-9]{1,3}\.[0-9])'
            pattern = re.compile(distance_expr + r'\s+' + time_expr + r'\s+' + time_expr + r'(?![0-9])', re.I)
            for raw in list(samples) + all_table_text:
                normalized = re.sub(r'\s+', ' ', raw.replace('：', ':')).strip()
                for match in pattern.finditer(normalized):
                    cumulative = seconds(match.group(1))
                    lap = seconds(match.group(2))
                    if 8 <= lap <= 25 and abs(cumulative - segments[prev][0] - lap) < 0.21:
                        segments[last] = (cumulative, lap)
                        lap_recovery = '末尾3点組・累積差一致'
                        break
                if last in segments:
                    break
            if last not in segments:
                lap_recovery = '末尾候補なし／累積差不一致'

    observed = sorted(segments)
    laps = [segments[d][1] for d in observed]
    cumul = [segments[d][0] for d in observed]
    checks = []
    if not observed:
        checks.append('ラップ表を解釈できません')
    else:
        if observed != list(range(200, observed[-1]+1, 200)):
            checks.append('途中の200m区間が欠けています')
        if race_distance is None:
            checks.append('レース距離を特定できません')
        elif race_distance % 200 != 0:
            checks.append('200m単位でないレース距離です')
        elif observed[-1] != race_distance or len(observed) != race_distance//200:
            checks.append(f'区間不足：{len(observed)}/{race_distance//200}区間')
        if any(not (8 <= v <= 25) for v in laps):
            checks.append('200mとして不自然なタイムあり')
        total = 0
        for d in observed:
            cv, lv = segments[d]
            total += lv
            if abs(total-cv) > 0.65:
                checks.append(f'{d}m通過タイムとラップ合計が不一致')
                break
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    return {'状態':'検証OK' if not checks else '要確認',
            'レース距離':race_distance if race_distance else '',
            '200mラップ':' - '.join(f'{v:.1f}' for v in laps),
            '区間数':len(laps), '期待区間数':race_distance//200 if race_distance else '',
            '合計秒':round(sum(laps),1) if laps else '',
            '検証':'OK' if not checks else ' / '.join(checks),
            '末尾補完':lap_recovery,
            '表の一部':' / '.join(samples)[:1200], 'ページURL':url}


@st.cache_data(ttl=86400, show_spinner=False)
def get_lap_table_test_v1594(race_id):
    """netkeibaの200mラップを検証用に取得。ランキングは更新しない。"""
    if not re.fullmatch(r'\d{12}', str(race_id)):
        raise ValueError('レースID不正')
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    response = requests.get(url, headers=HEADERS, timeout=18)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return parse_lap_html_v1591(response.text, race_id)




def classify_lap_shape_v16(lap_text, distance, validation='OK'):
    """200mラップ試験分類。持続性と絶対的な速さを分けて評価する（暫定）。"""
    if str(validation) != 'OK':
        return {'展開タイプ': '判定不可', '判定根拠': 'ラップ検証がOKではありません'}
    try:
        distance = int(distance)
        laps = [float(x.strip()) for x in str(lap_text).split(' - ')]
        if distance < 1200 or distance % 200 or len(laps) != distance // 200:
            raise ValueError('区間数不一致')
        if any(not (8 <= x <= 25) for x in laps):
            raise ValueError('ラップ値異常')
    except (ValueError, TypeError):
        return {'展開タイプ': '判定不可', '判定根拠': '距離とラップ数が不一致・不正'}

    n = len(laps)
    early = laps[1:max(3, n // 2)]  # スタートの200mは除く
    early_avg = sum(early) / len(early)
    late = sum(laps[-3:]) / 3
    before = sum(laps[-6:-3]) / 3
    accel = before - late
    fade = late - early_avg
    tail5 = laps[-5:]
    threshold = early_avg - 0.20
    quick = [x <= threshold for x in tail5]
    longest = run = 0
    for hit in quick:
        run = run + 1 if hit else 0
        longest = max(longest, run)
    sustained_count = sum(quick)
    tail_spread = max(tail5) - min(tail5)

    # 「速さ」と「持続」を切り離す。距離の違いに対する最適化は未実施。
    tail_mean = sum(tail5) / len(tail5)
    # 残り1000m直前の200m×3（1600mは最初の加速区間を除いた2区間）。
    baseline = laps[-8:-5] if n >= 9 else laps[1:-5]
    baseline_mean = sum(baseline) / len(baseline)
    relative_gain = baseline_mean - tail_mean  # 正なら後半1000mが速い
    early_tail_mean = sum(tail5[:2]) / 2  # 残1000〜600m
    late_tail_mean = sum(tail5[-3:]) / 3
    evenness = tail_spread <= 0.45
    # 残1000〜600mの時点ですでに速く、後半1000m全体で持続しているか。
    early_activation = early_tail_mean <= baseline_mean - 0.15
    relative_sustained = (evenness and relative_gain >= 0.20 and
                          early_activation and fade < 0.70)
    # 旧方式の「高速ラップ」判定も根拠として残す。
    absolute_sustained = (sustained_count >= 4 and longest >= 3 and
                          sum(quick[:2]) >= 1 and tail_spread <= 1.2 and fade < 0.7)
    long_spurt = relative_sustained or absolute_sustained
    sharp = accel >= 0.70 and not long_spurt
    exhausting = fade >= 0.70 and accel < 0.35
    if long_spurt:
        kind = 'ロングスパート戦寄り'
    elif sharp:
        kind = '瞬発戦寄り'
    elif exhausting:
        kind = '消耗戦寄り'
    else:
        kind = '混合・判定保留'
    if long_spurt:
        approach = '持続性＋相対加速' if relative_sustained else '高速区間連続（旧基準）'
    else:
        approach = '非該当'
    reason = (f'残1000m平均{tail_mean:.2f}s／直前基準{baseline_mean:.2f}s／相対加速{relative_gain:+.2f}s。'
              f'残1000〜600m平均{early_tail_mean:.2f}s／ラップ幅{tail_spread:.2f}s。'
              f'高速区間{sustained_count}/5・連続最長{longest}。'
              f'終盤600m加速{accel:+.2f}s／序盤比減速{fade:+.2f}s。'
              f'ロンスパ根拠：{approach}。※独自の暫定分類であり各馬の脚質判定ではありません')
    return {'展開タイプ': kind, '判定根拠': reason,
            '序盤平均_秒': round(early_avg, 2),
            '終盤600m平均_秒': round(late, 2),
            '終盤加速幅_秒': round(accel, 2),
            '終盤減速幅_秒': round(fade, 2),
            '高速区間数_残1000m': sustained_count,
            '最長高速連続区間数': longest,
            '残1000mラップ幅_秒': round(tail_spread, 2),
            '残1000m平均_秒': round(tail_mean, 2),
            '残1000m直前基準_秒': round(baseline_mean, 2),
            '残1000m相対加速_秒': round(relative_gain, 2),
            '残1000〜600m平均_秒': round(early_tail_mean, 2),
            '残600m平均_秒': round(late_tail_mean, 2),
            '持続性_ラップ幅045以内': '該当' if evenness else '非該当',
            '早めの加速': '該当' if early_activation else '非該当',
            'ロンスパ判定方法': approach,
            '持続判定': '該当' if long_spurt else '非該当'}


def get_lap_race_title(rid, sample):
    """保存済みの馬成績のレース名を使う。不足時のみ結果ページを参照。"""
    race_name = ''
    race_day = ''
    if 'レースID' in sample.columns:
        rows = sample[sample['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True).eq(str(rid))]
        if not rows.empty:
            for col, var in [('レース名', 'race_name'), ('日付', 'race_day')]:
                if col in rows:
                    vals = rows[col].dropna().astype(str).str.strip()
                    vals = vals[~vals.isin(['', 'nan', 'None'])]
                    if not vals.empty:
                        if col == 'レース名': race_name = vals.iloc[0]
                        else: race_day = vals.iloc[0]
    if not race_name:
        try:
            res = requests.get('https://race.netkeiba.com/race/result.html?race_id=' + str(rid),
                               headers=HEADERS, timeout=9)
            res.raise_for_status()
            res.encoding = res.apparent_encoding
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.title.get_text(' ', strip=True) if soup.title else ''
            name = re.split(r'\s+結果|\s*[|｜]\s*', title, maxsplit=1)[0].strip()
            if name and 'netkeiba' not in name.lower() and 'エラー' not in name:
                race_name = name
        except (requests.RequestException, ValueError):
            pass
    return race_name or 'レース名未取得', race_day


def lap_v16_cache_path(rid):
    return CACHE_DIR / f'lap_v16_{rid}.json'


def load_lap_v16(rid):
    path = lap_v16_cache_path(rid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def save_lap_v16(rid, payload):
    path = lap_v16_cache_path(rid)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def lap_full_progress_path(rid):
    """レース単位の一時保存。デプロイやサーバー停止で消える場合があります。"""
    if not re.fullmatch(r'\d{12}', str(rid)):
        return None
    return CACHE_DIR / f'lap_full_v165_{rid}.csv.gz'


def load_lap_full_progress(rid):
    path = lap_full_progress_path(rid)
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, compression='gzip', dtype={'レースID':str}).fillna('')
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def save_lap_full_progress(rid, frame):
    path = lap_full_progress_path(rid)
    if path is None or frame is None or frame.empty:
        return
    tmp = path.with_suffix('.tmp')
    frame.to_csv(tmp, index=False, compression='gzip')
    tmp.replace(path)


def apply_netkeiba_pace(data, pace_by_race):
    result = data.copy()
    ids = result['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    if 'netkeibaペース' not in result.columns:
        result['netkeibaペース'] = ''
    for rid, pace in pace_by_race.items():
        result.loc[ids.eq(str(rid)), 'netkeibaペース'] = pace
    return prepare(result)


def venue_name(value):
    value = str(value)
    return next((v for v in VENUES if v in value), value)


def convert_auto(raw):
    data = raw.copy()
    required = ['距離', '馬 場', '開催', '着 順', '上り', '通過', '日付']
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError('取得データに必要な列がありません: ' + '、'.join(missing))
    dist = data['距離'].astype(str)
    data['距離_m'] = pd.to_numeric(dist.str.extract(r'(\d{3,4})')[0], errors='coerce')
    data['コース'] = dist.str.extract(r'(芝|ダ|障)')[0]
    data['馬場状態'] = data['馬 場'].astype(str).str.strip()
    data['競馬場'] = data['開催'].apply(venue_name)
    data['着順_数値'] = pd.to_numeric(data['着 順'], errors='coerce')
    data['上がり3F_秒'] = pd.to_numeric(data['上り'], errors='coerce')
    data['通過順'] = data['通過'].fillna('').astype(str)
    data['4角順位'] = pd.to_numeric(data['通過'].astype(str).str.extract(r'(\d+)\s*$')[0], errors='coerce')
    data['上がり順位'] = float('nan')
    data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
    data['頭数'] = pd.to_numeric(data.get('頭 数'), errors='coerce')
    # netkeibaの「ペース」は前半3F-後半3Fの表記。距離差があるため目安として扱う。
    pace_pair = data['ペース'].astype(str).str.extract(r'(\d{2}\.\d)\s*[-－ー〜～]\s*(\d{2}\.\d)') if 'ペース' in data.columns else pd.DataFrame(index=data.index, columns=[0, 1])
    data['前半3F'] = pd.to_numeric(pace_pair[0], errors='coerce')
    data['後半3F'] = pd.to_numeric(pace_pair[1], errors='coerce')
    data['前後半3F差'] = data['前半3F'] - data['後半3F']
    return data[['馬名', '馬ID', 'レースID', '日付', '競馬場', 'コース', '距離_m', '馬場状態', '着順_数値', '4角順位', '上がり3F_秒', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差', 'レース名', '格付け根拠', 'レース格', '通過順']].copy()


def prepare(data):
    data = data.copy()
    data.columns = data.columns.astype(str).str.strip()
    for target, candidates in ALIASES.items():
        if target not in data.columns:
            for candidate in candidates:
                if candidate in data.columns:
                    data = data.rename(columns={candidate: target})
                    break
    missing = [c for c in ['馬名', '着順_数値'] if c not in data.columns]
    if missing:
        raise ValueError('必要な列がありません: ' + '、'.join(missing))
    for c in NUMERIC:
        if c not in data.columns:
            data[c] = float('nan')
        data[c] = pd.to_numeric(data[c], errors='coerce')
    for c in ['競馬場', '馬場状態']:
        if c not in data.columns:
            data[c] = '不明'
        data[c] = data[c].fillna('不明').astype(str).str.strip()
    # 旧CSVはレース格未収録。明示的なレース名があれば判定し、不明なものをその他に混ぜない。
    if 'レース格' not in data.columns:
        if 'レース名' in data.columns:
            data['レース格'] = data['レース名'].apply(classify_grade)
        else:
            data['レース格'] = '不明'
    else:
        valid_grades = ['G1', 'G2', 'G3', 'その他', '不明']
        grades = data['レース格'].fillna('').astype(str).str.strip()
        data['レース格'] = grades.where(grades.isin(valid_grades), grades.apply(classify_grade))
    for audit_col in ['レース名', '格付け根拠']:
        if audit_col not in data.columns:
            data[audit_col] = ''
        data[audit_col] = data[audit_col].fillna('').astype(str).replace('nan', '')
    if '通過順' not in data.columns:
        data['通過順'] = ''  # 旧保存データでも起動できるようにする
    data['通過順'] = data['通過順'].fillna('').astype(str)
    data['馬名'] = data['馬名'].astype(str).str.strip()
    data = data[data['馬名'].ne('') & data['馬名'].ne('nan')].copy()
    if '日付' in data.columns:
        data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
    # 手元Excelにも同じ分類を付け、数字の根拠がない場合は「不明」にする。
    # 前後半3Fの数値が妥当な範囲にあり、距離が1200m以上の時だけ分類する。
    # 最初と最後の600mの比較であり、道中のペースは評価できない。
    valid_pace = (data['前半3F'].between(29, 45) & data['後半3F'].between(29, 45)
                  & data['距離_m'].ge(1200))
    diff = (data['前半3F'] - data['後半3F']).where(valid_pace)
    data['前後半3F差'] = diff
    data['暫定ペース'] = '不明'
    data.loc[diff.notna() & diff.lt(-1.0), '暫定ペース'] = 'ハイ寄り'
    data.loc[diff.notna() & diff.between(-1.0, 1.0), '暫定ペース'] = 'ミドル寄り'
    data.loc[diff.notna() & diff.gt(1.0), '暫定ペース'] = 'スロー寄り'
    if 'netkeibaペース' not in data.columns:
        data['netkeibaペース'] = ''
    net_pace = data['netkeibaペース'].fillna('').astype(str).str.strip()
    valid_net = net_pace.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])
    # 実戦のペースランキングにはnetkeibaの明示判定のみ使用する。
    # 独自計算は暫定ペースに別保存し、混ぜない。
    data['推定ペース'] = '不明'
    data['ペース判定元'] = '未取得・不明'
    data.loc[valid_net, '推定ペース'] = net_pace[valid_net]
    data.loc[valid_net, 'ペース判定元'] = 'netkeiba表示'
    pos = data['4角順位']
    field = data['頭数']
    # 4角位置を頭数で割った便宜的な分類。逃げの確定判定ではない。
    valid = pos.ge(1) & field.ge(2) & pos.le(field)
    data['推定脚質'] = '不明'
    data.loc[valid & pos.eq(1), '推定脚質'] = '先頭（4角）'
    data.loc[valid & pos.gt(1) & pos.le((field * 0.35).clip(lower=2)), '推定脚質'] = '先行寄り'
    data.loc[valid & pos.gt((field * 0.35).clip(lower=2)) & pos.le(field * 0.7), '推定脚質'] = '中団寄り'
    data.loc[valid & pos.gt(field * 0.7), '推定脚質'] = '後方寄り'
    return data


def read_upload(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        try:
            data = pd.read_csv(file, encoding='utf-8-sig')
        except UnicodeDecodeError:
            file.seek(0)
            data = pd.read_csv(file, encoding='cp932')
    else:
        book = pd.ExcelFile(file)
        sheet = next((n for n in ['分析データ', '分析用296走'] if n in book.sheet_names), book.sheet_names[0])
        data = pd.read_excel(book, sheet_name=sheet)
    return prepare(data)


def make_excel(data):
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        data.to_excel(writer, sheet_name='分析データ', index=False)
    return out.getvalue()


from keiba_stats import sample_count_for_metric, sample_label, summarize






_keiba_setup_t = time.perf_counter()
# Ver.20.8.4: 出走データがない場合は取得メニューを開く。取得済みなら閉じる。
# 中の処理は展開状態にかかわらず実行されるため既存の取得ロジックは維持する。
_has_prepared_2084 = (st.session_state.get('auto_df') is not None)
with st.expander('📂 レース選択・データ取得（変更するときだけ）', expanded=not _has_prepared_2084):
    st.subheader('レースを選ぶ')
    # 復旧ガード: 保存済みレース一覧の問題だけで画面全体を停止させない。
    try:
        known_ids = saved_race_ids()
    except (NameError, OSError, ValueError, TypeError) as exc:
        st.warning(f'保存済みレース一覧を読み込めませんでした（{type(exc).__name__}: {exc}）。新しいレースIDで取得できます。')
        known_ids = []
    if 'race_input_v141' not in st.session_state:
        remembered = st.query_params.get('race_id', '')
        st.session_state.race_input_v141 = remembered if parse_race_id(remembered) else ''
    if known_ids:
        cached_labels = {rid: get_saved_race_label(rid) for rid in known_ids}
        cached_choice = tap_choice('保存済みレース', ['選択しない'] + known_ids, 'saved_race_choice_v153', format_func=lambda rid: cached_labels.get(rid, rid) if rid != '選択しない' else rid)
        if st.button('保存済みレースを開く', disabled=cached_choice == '選択しない', width='stretch'):
            st.session_state.race_input_v141 = cached_choice
            st.rerun()
    race_input = st.text_input('netkeiba出馬表URL または12桁のレースID', key='race_input_v141', placeholder='例：202605041111')
    race_id = parse_race_id(race_input) if race_input.strip() else ''
    if race_id:
        st.query_params['race_id'] = race_id
    if race_input.strip():
        if race_id:
            st.caption(f'レースID：{race_id}')
        else:
            st.warning('正しいURLまたは12桁のレースIDを入力してください。')

    st.caption('初回は一括取得、保存済みなら自動復元します。')
    if 'auto_race_id' not in st.session_state:
        st.session_state.auto_race_id = ''
    if 'auto_horses' not in st.session_state:
        st.session_state.auto_horses = {}
    if 'auto_numbers' not in st.session_state:
        st.session_state.auto_numbers = {}
    if 'auto_df' not in st.session_state:
        st.session_state.auto_df = None

    if race_id and st.session_state.auto_race_id != race_id:
        st.session_state['analysis_visible_v1581'] = False
        st.session_state.auto_race_id = race_id
        st.session_state.auto_horses = {}
        st.session_state.auto_numbers = {}
        st.session_state.auto_df = None
        try:
            restored = load_race_cache(race_id)
            if restored is not None:
                st.session_state.auto_horses, st.session_state.auto_df = restored
                # 保存済み成績には馬番が含まれないため、出馬表の馬番だけを再照合する。
                # 出馬表取得が失敗しても保存済み成績と診断はそのまま利用する。
                try:
                    latest_horses_182, latest_numbers_182 = get_horses(race_id)
                    if latest_horses_182:
                        st.session_state.auto_horses = latest_horses_182
                        st.session_state.auto_numbers = latest_numbers_182
                        st.session_state.auto_df = update_cached_names(st.session_state.auto_df, latest_horses_182)
                except Exception:
                    pass
        except Exception as e:
            st.warning(f'保存済みデータの復元に失敗しました：{e}')
    if race_id and st.session_state.auto_df is not None:
        prepared_data = st.session_state.auto_df
        st.caption(f'取得済み：{len(prepared_data)}走。分析画面は左上の ☰ から選択できます。')
    if st.session_state.get('fetch_issues_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
        st.warning('一部のデータを取得できませんでした。詳細は「管理・検証」→「運用品質チェック」で確認できます。')
    if st.session_state.get('fetch_fatal_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
        st.error('一括取得を完了できませんでした。詳細は「管理・検証」を確認してください。')


    # URL入力から1タップで、出走馬・過去成績・上がり順位まで取得する。
    # 保存済みの成績は再利用し、上がり順位がまだない場合は追加照合する。
    if st.button('出走馬・成績・上がり・S/M/Hを一括取得', type='primary',
                 disabled=not race_id, width='stretch'):
        _fetch_t0_v2083 = time.perf_counter()
        _fetch_marks_v2083 = {}
        st.session_state.pop('fetch_fatal_v174', None)
        transient_progress_v174 = st.empty()
        with transient_progress_v174.container():
            st.caption('取得中だけ進捗と処理結果を表示します。完了後は自動で閉じます。')
            st.session_state['fetch_issues_v174'] = []
            st.session_state['fetch_issues_race_v174'] = race_id
            horses_now = st.session_state.auto_horses.copy()
            data_now = st.session_state.auto_df
            try:
                # 出馬表の表記が変わった場合も正式馬名・馬番を更新。既存成績は再取得しない。
                try:
                    latest_horses, latest_numbers = get_horses(race_id)
                    horses_now = latest_horses
                    st.session_state.auto_horses = latest_horses
                    st.session_state.auto_numbers = latest_numbers
                    if data_now is not None:
                        data_now = update_cached_names(data_now, latest_horses)
                        st.session_state.auto_df = data_now
                        save_race_cache(race_id, latest_horses, data_now)
                except Exception as e:
                    if not horses_now:
                        raise
                    st.session_state['fetch_issues_v174'].append(f'出馬表更新情報：{e}')
                    st.warning(f'出馬表の更新情報は取得できませんでした：{e}')
                if not horses_now:
                    with st.spinner('① 出馬表から出走馬を取得中…'):
                        horses_now, nums_now = get_horses(race_id)
                        st.session_state.auto_horses = horses_now
                        st.session_state.auto_numbers = nums_now
                _fetch_marks_v2083['出馬表・キャッシュ更新'] = time.perf_counter() - _fetch_t0_v2083
                _fetch_stage_v2083 = time.perf_counter()
                if data_now is None:
                    datasets = []
                    horse_errors = []
                    progress = st.progress(0)
                    status = st.empty()
                    for i, (horse_id, name) in enumerate(horses_now.items(), 1):
                        status.write(f'② 過去成績を取得中：{name}（{i}/{len(horses_now)}）')
                        try:
                            datasets.append(get_results(horse_id, name))
                        except Exception as e:
                            horse_errors.append(f'{name}：{e}')
                        progress.progress(i / len(horses_now))
                        if i < len(horses_now):
                            time.sleep(1)
                    status.empty()
                    progress.empty()
                    if not datasets:
                        raise ValueError('過去成績を1件も取得できませんでした')
                    data_now = prepare(convert_auto(pd.concat(datasets, ignore_index=True)))
                    st.session_state.auto_df = data_now
                    save_race_cache(race_id, horses_now, data_now)
                    st.success(f'過去成績：{len(data_now)}走を取得・保存しました')
                    if horse_errors:
                        st.session_state['fetch_issues_v174'].append('過去成績の取得失敗：' + ' / '.join(horse_errors))
                        st.warning('取得できなかった馬：' + ' / '.join(horse_errors))
                else:
                    st.info(f'② 保存済みの過去成績 {len(data_now)}走を再利用します')

                _fetch_marks_v2083['過去成績の取得・保存'] = time.perf_counter() - _fetch_stage_v2083
                _fetch_stage_v2083 = time.perf_counter()
                # 1レースにつき最大1回の照合。失敗があっても、取得済みデータを保持する。
                try:
                    with st.spinner('③ 直近5走の上がり順位を照合しています…'):
                        updated, checked, agari_errors = add_recent_agari_ranks(data_now)
                    st.session_state.auto_df = updated
                    save_race_cache(race_id, horses_now, updated)
                    count = int(updated['上がり順位'].notna().sum())
                    st.success(f'取得完了！ 出走馬{len(horses_now)}頭・過去成績{len(updated)}走・上がり順位{count}走（今回照合{checked}レース）')
                    if agari_errors:
                        st.session_state['fetch_issues_v174'].append(f'上がり順位取得不可：{len(agari_errors)}レース')
                        st.warning(f'上がり順位を取得できなかったレース：{len(agari_errors)}件。例：{agari_errors[0]}')
                except Exception as e:
                    st.session_state['fetch_issues_v174'].append(f'上がり順位の照合失敗：{e}')
                    st.warning(f'過去成績は保存済みですが、上がり順位の照合は失敗しました：{e}。同じボタンで再試行できます。')

                _fetch_marks_v2083['上がり順位の照合'] = time.perf_counter() - _fetch_stage_v2083
                _fetch_stage_v2083 = time.perf_counter()
                # Ver.17.3: netkeibaのペースも同じボタンで差分取得。途中10件ごとに保存する。
                pace_work = st.session_state.auto_df
                if pace_work is not None and 'レースID' in pace_work.columns:
                    pids = pace_work['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
                    valid_pids = pids[pids.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
                    old_pace = pace_work['netkeibaペース'].fillna('').astype(str).str.strip() if 'netkeibaペース' in pace_work.columns else pd.Series('', index=pace_work.index)
                    acquired = set(pids[old_pace.isin(['スロー寄り','ミドル寄り','ハイ寄り'])])
                    pending_pids = [r for r in valid_pids if r not in acquired]
                    if pending_pids:
                        st.caption(f'S/M/Hの未取得分 {len(pending_pids)}レースを追加取得中（10件ごと保存）')
                        pace_progress = st.progress(0)
                        new_paces, errors = {}, []
                        for j, pr in enumerate(pending_pids, 1):
                            try:
                                new_paces[pr] = get_netkeiba_pace(pr)
                            except Exception as ex:
                                errors.append({'レースID':pr,'状態':'取得不可','理由':str(ex)[:180]})
                            if j % 10 == 0 or j == len(pending_pids):
                                if new_paces:
                                    pace_work = apply_netkeiba_pace(pace_work, new_paces)
                                    st.session_state.auto_df = pace_work
                                    save_race_cache(race_id, horses_now, pace_work)
                                    new_paces.clear()
                                st.session_state['pace_v157_failed'] = errors.copy()
                            pace_progress.progress(j/len(pending_pids))
                            if j < len(pending_pids):
                                time.sleep(0.35)
                        pace_progress.empty()
                        st.success(f'S/M/Hの差分取得完了：今回 {len(pending_pids)-len(errors)}/{len(pending_pids)} レース')
                        if errors:
                            st.session_state['fetch_issues_v174'].append(f'S/M/H取得不可：{len(errors)}レース')
                            st.warning(f'S/M/Hを取得できなかったレース：{len(errors)}件。管理・検証で確認できます。')
                    else:
                        st.caption('S/M/H判定は保存済みデータを再利用しました。')
                _fetch_marks_v2083['S/M/Hの差分取得'] = time.perf_counter() - _fetch_stage_v2083
            except Exception as e:
                st.session_state['fetch_fatal_v174'] = str(e)
                st.error(f'まとめて取得できませんでした：{e}')
            finally:
                # 最後の成功・失敗にかかわらず、測定値を残す（通信や待機時間込み）。
                st.session_state['last_fetch_profile_v2083'] = {
                    'race_id': race_id,
                    'total': round(time.perf_counter() - _fetch_t0_v2083, 2),
                    'stages': {k: round(v, 2) for k, v in _fetch_marks_v2083.items()},
                }
        transient_progress_v174.empty()
        st.rerun()

    def render_pace_check_and_retry():
        st.subheader('netkeibaのS/M/Hを全レース取得（Ver.15.8）')
        st.caption('保存済みの全過去成績から重複を除いたレースIDを対象にします。取得済みは再取得せず、10件ごとに結果を一時保存します。')
        if st.session_state.auto_df is not None and 'レースID' in st.session_state.auto_df.columns:
            source_df = st.session_state.auto_df.copy()
            ids = source_df['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
            valid_ids = ids[ids.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
            # リストはレース日順にして、通信に失敗しても何が残ったか確認しやすくする。
            if '日付' in source_df.columns:
                ordering = source_df.assign(_rid=ids, _date=pd.to_datetime(source_df['日付'], errors='coerce'))
                ordered = ordering.sort_values('_date', ascending=False, na_position='last')['_rid']
                valid_ids = ordered[ordered.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
            known = source_df['netkeibaペース'].fillna('').astype(str).str.strip() if 'netkeibaペース' in source_df.columns else pd.Series('', index=source_df.index)
            confirmed_ids = set(ids[known.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])])
            pending = [rid for rid in valid_ids if rid not in confirmed_ids]
            st.write(f'対象：**{len(valid_ids)}レース** ／ netkeiba判定取得済み：**{len(valid_ids)-len(pending)}** ／ 未取得：**{len(pending)}**')
            st.caption('レース単位で集計するため、同じレースに複数の出走馬がいても1回だけアクセスします。')
            if st.button('未取得レースのS/M/Hをまとめて取得', disabled=not pending, type='primary', width='stretch'):
                working = st.session_state.auto_df.copy()
                success, failed = {}, []
                progress = st.progress(0)
                status = st.empty()
                for i, rid in enumerate(pending, 1):
                    status.caption(f'取得中：{i}/{len(pending)}レース｜成功 {len(success)}件・取得不可 {len(failed)}件')
                    try:
                        success[rid] = get_netkeiba_pace(rid)
                    except Exception as e:
                        failed.append({'レースID': rid, '状態': '取得不可', '理由': str(e)[:180],
                                       '結果ページ': f'https://db.sp.netkeiba.com/race/{rid}/'})
                    if i % 10 == 0 or i == len(pending):
                        if success:
                            working = apply_netkeiba_pace(working, success)
                            st.session_state.auto_df = working
                            save_race_cache(st.session_state.auto_race_id, st.session_state.auto_horses, working)
                            success.clear()
                        st.session_state['pace_v157_failed'] = failed.copy()
                    progress.progress(i / len(pending))
                    if i < len(pending):
                        time.sleep(0.35)
                progress.empty()
                status.empty()
                st.session_state.auto_df = working
                st.session_state['pace_v157_failed'] = failed
                received = int(working['netkeibaペース'].fillna('').astype(str).isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り']).groupby(working['レースID'].fillna('').astype(str)).any().sum())
                st.success(f'取得処理が完了！netkeiba判定取得済み：{received}/{len(valid_ids)}レース。今回取得不可：{len(failed)}レース。')
                if failed:
                    st.warning('取得不可は「不明」として扱います。再実行すると取得済みを飛ばして再試行できます。')
            if st.session_state.get('pace_v157_failed'):
                fails_df = pd.DataFrame(st.session_state['pace_v157_failed'])
                with st.popover(f'取得不可のレース（{len(fails_df)}件）'):
                    st.dataframe(fails_df, hide_index=True, width='stretch')
                    st.download_button('取得不可一覧をCSVで保存', fails_df.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_netkeiba_pace_fail_v157.csv', mime='text/csv', width='stretch')
            st.caption('ペース別成績はnetkeiba明示判定だけを使用。以前の±1秒の推定値は「暫定ペース」として検証表に残します。')
        else:
            st.caption('過去成績を取得すると、全レースのS/M/H取得ができます。')


# Ver.19.2: データ修復はメイン画面を散らかさないように折りたたみに配置。
# 成功した場合のみ差し替え、古い保存データを消さずに残す。
if st.session_state.get('nav_page_v208') == '🧰 管理・検証':
    with st.expander('序盤通過順の確認・過去成績の強制再取得（Ver.19.2）', expanded=False):
        current_v192 = st.session_state.auto_df
        if current_v192 is not None and not current_v192.empty:
            total_v192 = len(current_v192)
            corners_v192 = (current_v192['通過順'].fillna('').astype(str)
                            if '通過順' in current_v192.columns else pd.Series('', index=current_v192.index))
            valid_v192 = corners_v192.apply(lambda x: bool(re.search(r'\d+', x)))
            st.write(f'保存済み過去成績：**{total_v192}走**／通過順の数値あり：**{int(valid_v192.sum())}走**')
            bad_v192 = current_v192.loc[~valid_v192].copy()
            if not bad_v192.empty:
                st.warning(f'通過順が確認できない成績が{len(bad_v192)}走あります。旧キャッシュの可能性があります。')
            else:
                st.info('通過順の数値は保存されています。予測が判定保留なら、初通過地点の取得走数・頭数も確認してください。')
            with st.expander('保存済み通過順のサンプルを確認', expanded=False):
                show_cols_v192 = [c for c in ['馬名','レース名','日付','通過順','4角順位','頭数'] if c in current_v192.columns]
                st.dataframe(current_v192[show_cols_v192].head(15), width='stretch', hide_index=True)
        else:
            st.caption('まだ過去成績がありません。先に出馬表URLから一括取得してください。')
        st.caption('再取得は全馬の過去成績だけを再読込し、上がり順位・ペースなど取得済みの値はレースIDを使ってできるだけ引き継ぎます。元データは取得が成功するまで保持します。')
        confirm_v192 = st.checkbox('現在のレースの全馬過去成績を再取得する（通信が発生します）', key='confirm_force_v192')
        if st.button('過去成績を強制再取得・通過順を更新', type='secondary',
                     disabled=(not confirm_v192 or not race_id), width='stretch'):
            current_horses_v192 = st.session_state.auto_horses
            if not current_horses_v192:
                st.error('出走馬一覧がありません。先に一括取得を実行してください。')
            else:
                try:
                    get_results.clear()  # @st.cache_data の旧データを再利用しない
                    fetched_v192, errors_v192 = [], []
                    prg_v192 = st.progress(0)
                    for idx_v192, (hid_v192, name_v192) in enumerate(current_horses_v192.items(), 1):
                        try:
                            raw_v192 = get_results(hid_v192, name_v192)
                            fetched_v192.append(raw_v192)
                        except Exception as exc_v192:
                            errors_v192.append(f'{name_v192}: {str(exc_v192)[:100]}')
                        prg_v192.progress(idx_v192 / len(current_horses_v192))
                    prg_v192.empty()
                    if errors_v192:
                        st.error(f'{len(errors_v192)}頭で取得失敗したので、保存済みデータを維持しました。')
                        with st.expander('失敗した馬を見る'):
                            st.write(' / '.join(errors_v192))
                    else:
                        rebuilt_v192 = prepare(convert_auto(pd.concat(fetched_v192, ignore_index=True)))
                        old_v192 = st.session_state.auto_df
                        if old_v192 is not None and not old_v192.empty:
                            # 元データと同一馬・同一レースであると確認できた行だけ補助情報を復元する。
                            keep_v192 = [c for c in ['上がり順位','netkeibaペース'] if c in old_v192.columns]
                            if keep_v192 and all(c in old_v192.columns for c in ['馬ID','レースID']):
                                previous_v192 = old_v192[['馬ID','レースID'] + keep_v192].copy()
                                previous_v192['馬ID'] = previous_v192['馬ID'].astype(str)
                                previous_v192['レースID'] = previous_v192['レースID'].astype(str)
                                previous_v192 = previous_v192.drop_duplicates(['馬ID','レースID'])
                                rebuilt_v192['馬ID'] = rebuilt_v192['馬ID'].astype(str)
                                rebuilt_v192['レースID'] = rebuilt_v192['レースID'].astype(str)
                                rebuilt_v192 = rebuilt_v192.drop(columns=keep_v192, errors='ignore').merge(
                                    previous_v192, on=['馬ID','レースID'], how='left', validate='many_to_one')
                                rebuilt_v192 = prepare(rebuilt_v192)
                        new_valid_v192 = int(rebuilt_v192['通過順'].fillna('').astype(str).str.contains(r'\d+',regex=True).sum())
                        if new_valid_v192 == 0:
                            st.error('再取得データでも通過順が0走でした。旧データを保持します。取得元のHTML列名を調べる必要があります。')
                        else:
                            st.session_state.auto_df = rebuilt_v192
                            save_race_cache(race_id, current_horses_v192, rebuilt_v192)
                            st.success(f'通過順を{new_valid_v192}/{len(rebuilt_v192)}走で確認し、データを更新しました。')
                            st.rerun()
                except Exception as exc_v192:
                    st.error(f'再取得を完了できませんでした。以前の保存データは維持しています：{exc_v192}')

# Ver.20.8: iPhoneでは左上☰から縦型メニューを選択。
# ペース予測式、データ取得、各馬の分析内容はVer.20.7から変更しない。
with st.sidebar:
    st.markdown('## 🏇 メニュー')
    st.caption('予想に使う画面を上にまとめています。')
    selected_page208 = st.radio(
        'メインメニュー',
        ['🏇 総合診断', '🌊 ペース分析', '🐎 各馬の特徴', '⚙️ 分析条件', '🧰 管理・検証'],
        key='nav_page_v208', label_visibility='collapsed',
    )
    st.caption('管理・検証は通常の予想では使わないオプションです。')
    st.divider()
    st.caption('競馬分析ラボ Ver.20.18.0')

# サイドバーにより、iPhoneは標準の☰ボタンで開閉する。
# 詳細分析が総合診断の計算結果を参照するため、計算自体は維持。
# 非選択画面だけ描画後に消去し、既存の計算・操作を壊さないようにする。
st.markdown("""
<style>
/* iPhoneの縦メニュー項目を大きく押しやすくする */
section[data-testid="stSidebar"] div[role="radiogroup"] label {
    min-height: 64px !important;
    padding: 13px 14px !important;
    border: 1px solid rgba(125,135,150,.32) !important;
    border-radius: 12px !important;
    margin-bottom: 8px !important;
    font-size: 18px !important;
    font-weight: 650 !important;
    background: rgba(125,135,150,.08) !important;
}
section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
    border-color: #e45565 !important;
    background: rgba(228,85,101,.14) !important;
}
</style>
""", unsafe_allow_html=True)

if selected_page208 == '🧰 管理・検証':
    st.caption('管理・検証（オプション）：本番用の品質確認と開発中の実験を分離しています。')
else:
    st.caption('左上の ☰ から画面を切り替えられます。')


horses = st.session_state.auto_horses
# バックアップの注意書きはデータ管理を開いたときだけ案内。
manual = ''
uploads = []
if selected_page208 == '🧰 管理・検証':
    with st.expander('📥 手入力・Excel/CSV（必要なときだけ）', expanded=False):
        st.caption('サーバーの保存データは再起動で消える可能性があります。必要な場合はExcelへバックアップしてください。')
        if horses:
            st.write(f'出走馬：{len(horses)}頭')
            st.write('、'.join(horse_display(n, horses, st.session_state.auto_numbers) for n in horses.values()))
        if st.session_state.auto_df is not None:
            available = int(st.session_state.auto_df['上がり順位'].notna().sum())
            st.caption(f'保存済みの過去成績：{len(st.session_state.auto_df)}走／上がり順位取得済み：{available}走')
        manual = st.text_area('手入力する出走馬名（任意・1行に1頭）', height=95)
        st.caption('自動取得に失敗したときや、外部のExcel/CSVを使うときだけ設定します。')
        uploads = st.file_uploader('分析データを追加（任意）', type=['xlsx', 'csv'], accept_multiple_files=True)

target_horses = list(dict.fromkeys([n.strip() for n in manual.splitlines() if n.strip()] or list(horses.values())))
datasets = []
if st.session_state.auto_df is not None:
    datasets.append(st.session_state.auto_df.copy())
for file in uploads or []:
    try:
        datasets.append(read_upload(file))
    except Exception as e:
        st.error(f'{file.name} の読み込みに失敗：{e}')
        st.stop()
if not datasets:
    st.info('レースURLを入力して「一括取得」を押すか、「データ管理」からExcel/CSVを追加してください。')
    st.stop()
df = pd.concat(datasets, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)
if selected_page208 == '🧰 管理・検証':
    with st.expander('取得データをExcelでバックアップ', expanded=False):
        st.caption(f'{len(df)}走・{df["馬名"].nunique()}頭を読み込み済み。')
        st.download_button('Excelを保存', data=make_excel(df), file_name='競馬分析_統合データ.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', width='stretch')

analysis_df = df.copy()
if target_horses:
    names_available = set(analysis_df['馬名'].unique())
    missing = [n for n in target_horses if n not in names_available]
    analysis_df = analysis_df[analysis_df['馬名'].isin(target_horses)].copy()
    if missing:
        st.warning('データが見つからない馬：' + '、'.join(missing))
if analysis_df.empty:
    st.error('分析対象の馬の成績がありません。')
    st.stop()
st.caption(f'分析対象：{analysis_df["馬名"].nunique()}頭・{len(analysis_df)}走')

_keiba_data_t = time.perf_counter()
if selected_page208 == '🧰 管理・検証':
    quality_holder208 = st.empty()
    with quality_holder208.container():
        tab_quality_live206, tab_quality172 = st.tabs(['✅ 運用品質チェック', '🧪 開発・実験ラボ'])

    with tab_quality_live206:
        st.subheader('運用品質チェック')
        st.caption('本番運用でも必要なデータ取得・欠測・エラーの確認だけを表示します。')
        if st.session_state.get('fetch_issues_race_v174') == race_id and st.session_state.get('fetch_issues_v174'):
            with st.expander('直近の取得時の注意事項', expanded=False):
                for issue in st.session_state['fetch_issues_v174']:
                    st.write('・' + str(issue))
        if st.session_state.get('fetch_fatal_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
            st.error(str(st.session_state['fetch_fatal_v174']))
        with st.expander('S/M/Hデータの取得状況・未取得分の再取得', expanded=False):
            render_pace_check_and_retry()
        with st.expander('データの欠測を確認', expanded=False):
            st.write(f'分析対象：{analysis_df["馬名"].nunique()}頭／{len(analysis_df)}走')
            for label, field in [('序盤通過順','通過'), ('馬場状態','馬場状態'), ('レース格','レース格')]:
                if field in analysis_df.columns:
                    good = int(analysis_df[field].notna().sum())
                    st.caption(f'{label}：値あり {good}/{len(analysis_df)}走（値の妥当性は別途確認が必要）')

if selected_page208 in ('🏇 総合診断', '🌊 ペース分析', '🐎 各馬の特徴'):
    main_holder208 = st.empty()
    with main_holder208.container():
        st.subheader('ペース予測・対応馬' if selected_page208 == '🌊 ペース分析' else '出走馬の総合診断')
        st.caption('集計条件：各馬の直近10走に含まれるG1・G2・G3。条件変更は「分析条件」へ。')
        quick171 = analysis_df.copy()
        if '日付' in quick171.columns:
            quick171 = quick171.sort_values('日付', ascending=False, na_position='last', kind='stable')
        quick171 = quick171.groupby('馬名', sort=False).head(10).copy()
        quick171 = quick171.loc[quick171['レース格'].isin(['G1','G2','G3'])].copy()
        quick_race171 = str(st.session_state.get('auto_race_id', ''))
        quick_shapes171 = load_lap_full_progress(quick_race171)
        if quick_shapes171.empty:
            maybe171 = st.session_state.get('lap_shapes_v163')
            if (isinstance(maybe171, pd.DataFrame) and not maybe171.empty
                    and st.session_state.get('lap_shapes_v163_race') == quick_race171):
                quick_shapes171 = maybe171.copy()
        metric_names171 = ['終盤加速幅_秒', '残1000m相対加速_秒', '残1000mラップ幅_秒']
        quick_lapped171 = pd.DataFrame()
        if not quick171.empty and 'レースID' in quick171.columns and not quick_shapes171.empty and 'レースID' in quick_shapes171.columns:
            qbase171 = quick171.copy()
            qbase171['_quick_rid171'] = qbase171['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
            qshape171 = quick_shapes171.copy()
            qshape171['_quick_rid171'] = qshape171['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
            if '検証' in qshape171.columns:
                qshape171 = qshape171[qshape171['検証'].eq('OK')].copy()
            for m171 in metric_names171:
                if m171 not in qshape171.columns:
                    qshape171[m171] = float('nan')
                qshape171[m171] = pd.to_numeric(qshape171[m171], errors='coerce')
            qshape171 = qshape171.drop_duplicates('_quick_rid171')
            quick_lapped171 = qbase171.merge(qshape171[['_quick_rid171'] + metric_names171], on='_quick_rid171', how='inner')
            quick_lapped171['着順_数値'] = pd.to_numeric(quick_lapped171['着順_数値'], errors='coerce')
            quick_lapped171 = quick_lapped171.drop_duplicates(['馬名','_quick_rid171'])
        quick_names171 = list(dict.fromkeys(list(horses.values()) if horses else []))
        quick_names171 += [nm for nm in quick171['馬名'].dropna().unique() if nm not in quick_names171]
        records171 = []
        for name171 in quick_names171:
            allhorse171 = quick171.loc[quick171['馬名'].eq(name171)]
            lapgroup171 = (quick_lapped171.loc[quick_lapped171['馬名'].eq(name171)]
                           if not quick_lapped171.empty else pd.DataFrame())
            if lapgroup171.empty:
                label171 = '不明'
                # ラップが未取得でも、馬自身の位置と末脚の診断は表示する。
                hdiagnose171 = allhorse171
            else:
                hdiagnose171 = lapgroup171
                good171 = lapgroup171['着順_数値'].between(1,3)
                bad171 = lapgroup171['着順_数値'].ge(4)
                comp171 = {'好走走数': int(good171.sum()), '凡走走数': int(bad171.sum())}
                for col171, lab171 in zip(metric_names171, ['終盤加速', '残1000m加速', '残1000mラップ幅']):
                    va171 = pd.to_numeric(lapgroup171.loc[good171, col171], errors='coerce').dropna()
                    vb171 = pd.to_numeric(lapgroup171.loc[bad171, col171], errors='coerce').dropna()
                    comp171[lab171+'_差'] = va171.mean()-vb171.mean() if len(va171) and len(vb171) else float('nan')
                label171, _, _ = interpret_running_pattern(comp171)
            diag171 = diagnose_horse_v17(hdiagnose171, label171)
            diag171['馬名'] = name171
            diag171['ラップ確認走数'] = len(lapgroup171)
            records171.append(diag171)
        quick_result171 = pd.DataFrame(records171)
        if not quick_result171.empty:
            quick_display171 = display_horse_column(quick_result171, horses, st.session_state.auto_numbers)
            if selected_page208 == '🏇 総合診断':
                st.markdown('#### 全馬の特徴を比較')
                st.caption('過去走から分かる走り方・好走展開の比較。能力順位や今回の勝率ではありません。')
                fixed_order_table_v209(
                    quick_display171, ['馬名','走りの総合診断','展開','信頼度','好走走数'],
                    {'馬名':'馬番＋正式馬名','走りの総合診断':'強み・走り方',
                     '展開':'好走展開','信頼度':'根拠の信頼度','好走走数':'好走走数'})
                st.caption('信頼度はデータの充実度の目安で、予測的中率ではありません。')
                # Ver.20.10: 計算結果を変えず、根拠となる有効データと欠測を監査する。
                with st.expander('分析の信頼性チェック（全馬）', expanded=False):
                    audit210 = quick_result171[['馬名','対象走数','好走走数','ラップ確認走数',
                                                 '位置取得','上がり取得','信頼度']].copy()
                    count_cols210 = ['対象走数','好走走数','ラップ確認走数','位置取得','上がり取得']
                    for col210 in count_cols210:
                        audit210[col210] = pd.to_numeric(audit210[col210], errors='coerce').fillna(0).astype(int)
                    audit210['検証結果'] = audit210.apply(
                        lambda r: '要確認：走数不整合'
                        if (r['好走走数'] > r['対象走数']
                            or r['ラップ確認走数'] > r['対象走数']
                            or r['位置取得'] > r['対象走数']
                            or r['上がり取得'] > r['対象走数'])
                        else ('参考：対象2走以下' if r['対象走数'] <= 2
                              else '注意：ラップ確認0走' if r['ラップ確認走数'] == 0
                              else '注意：ラップ確認少数' if r['ラップ確認走数'] <= 2
                              else 'データ量を確認'), axis=1)
                    audit_display210 = display_horse_column(audit210, horses, st.session_state.auto_numbers)
                    fixed_order_table_v209(
                        audit_display210,
                        ['馬名','対象走数','好走走数','ラップ確認走数','位置取得','上がり取得','信頼度','検証結果'],
                        {'馬名':'馬番＋正式馬名','対象走数':'重賞対象','好走走数':'3着内',
                         'ラップ確認走数':'ラップ有効','位置取得':'4角有効',
                         '上がり取得':'上がり有効','信頼度':'既存の信頼度','検証結果':'監査メモ'},
                        max_height=560)
                    issues210 = int(audit210['検証結果'].eq('要確認：走数不整合').sum())
                    st.caption(f'走数不整合：{issues210}頭。対象走数が少ない馬やラップ未確認の馬は評価を参考扱いにしてください。')
                    st.caption('これはデータ量と整合性の監査です。S/M/H判定自体の正確性や、実際のレースでの予測精度は検証していません。')
                    st.download_button('信頼性チェックをCSV保存',
                                       audit_display210.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_reliability_audit_v2010.csv',
                                       mime='text/csv', width='stretch')

            if selected_page208 == '🌊 ペース分析':
                # Ver.18: 実際のnetkeiba S/M/H実績から、選択した展開での好走率を比較。
                # レース全体のペースであり、将来の勝率を予測した値ではない。
                st.markdown('#### 想定ペース × 相性・実績')
                # Ver.19: 出走馬の位置取りから暫定の想定ペースを表示。
                # レースの真のS/M/H判定ではない。発走前の展開は不確実である。
                predicted_v19, reason_v19, pressure_v19, known_v19, lead_v19, front_v19 = estimate_field_pace_v19(analysis_df, quick_names171)
                if predicted_v19:
                    st.write(f'**暫定の想定ペース：{predicted_v19}**')
                    st.caption(f'{reason_v19} 初通過位置を確認：{known_v19}/{len(quick_names171)}頭／逃げ候補：{lead_v19}頭／先行候補：{front_v19}頭。')
                else:
                    st.write('**暫定の想定ペース：判定保留**')
                    st.caption(reason_v19)
                st.caption('レース全体のS/M/Hと馬が作ったペースは同一ではありません。先頭で走った過去レースのS/M/Hは補助材料です。騎手の作戦・馬場・今回の前半ラップは未反映。予測ロジックは未検証です。')
                with st.expander('ペース予測の根拠（各馬の位置取り）', expanded=False):
                    pressure_show_v19 = display_horse_column(pressure_v19[['馬名','初通過取得','初通過先頭','初通過前方','逃げ候補','先行候補','先頭S','先頭M','先頭H','前方S','前方M','前方H']], horses, st.session_state.auto_numbers)
                    st.dataframe(pressure_show_v19, hide_index=True, width='stretch')
                    st.caption('直近5走の初通過地点を使用。先頭S/M/H＝その位置で走ったレースのnetkeibaペース別件数。前方S/M/Hも同様。取得できないペースは除外。初通過地点は発走直後とは限りません。')
                # ユーザーによる切替を尊重。初めて表示するときだけ暫定予測を初期選択。
                def _mark_manual_v193():
                    st.session_state['scenario_manual_v193'] = True
                pace_signature_v193 = (quick_race171, predicted_v19, known_v19,
                                       int(pressure_v19['先頭ペース確認'].sum()))
                if st.session_state.get('scenario_signature_v193') != pace_signature_v193:
                    if st.session_state.get('scenario_race_v193') != quick_race171:
                        st.session_state['scenario_manual_v193'] = False
                    if not st.session_state.get('scenario_manual_v193', False):
                        st.session_state['scenario_pace_v18'] = predicted_v19 or 'ミドル（M）'
                    st.session_state['scenario_race_v193'] = quick_race171
                    st.session_state['scenario_signature_v193'] = pace_signature_v193
                selected_pace18 = st.radio('今回想定するペース',
                                           ['スロー（S）', 'ミドル（M）', 'ハイ（H）'],
                                           horizontal=True, key='scenario_pace_v18', on_change=_mark_manual_v193)
                pace_key18 = {'スロー（S）': 'スロー寄り', 'ミドル（M）': 'ミドル寄り',
                              'ハイ（H）': 'ハイ寄り'}[selected_pace18]
                results18 = []
                details18 = {}
                pace_options18 = ['スロー寄り', 'ミドル寄り', 'ハイ寄り']
                for horse18 in quick_names171:
                    raw18 = quick171.loc[quick171['馬名'].eq(horse18)].copy()
                    if raw18.empty:
                        continue
                    raw18['着順_数値'] = pd.to_numeric(raw18['着順_数値'], errors='coerce')
                    raw18 = raw18.loc[raw18['着順_数値'].ge(1)].copy()
                    unknown18 = int((~raw18['推定ペース'].isin(pace_options18)).sum())
                    known18 = raw18.loc[raw18['推定ペース'].isin(pace_options18)].copy()
                    selected18 = known18.loc[known18['推定ペース'].eq(pace_key18)].copy()
                    others18 = known18.loc[~known18['推定ペース'].eq(pace_key18)].copy()
                    n18, other_n18 = len(selected18), len(others18)
                    good18 = int(selected18['着順_数値'].le(3).sum())
                    other_good18 = int(others18['着順_数値'].le(3).sum())
                    pct18 = 100 * good18 / n18 if n18 else None
                    other_pct18 = 100 * other_good18 / other_n18 if other_n18 else None
                    diff18 = pct18 - other_pct18 if pct18 is not None and other_pct18 is not None else None
                    if n18 == 0:
                        if len(known18) == 0:
                            status18 = '判定データ不足'
                            reason18 = '着順付きのS/M/H判定済みデータがありません。'
                        elif unknown18:
                            status18 = '該当0走※'
                            reason18 = f'取得済みのS/M/Hには該当0走。ただしペース未判定が{unknown18}走あるため未経験とは断定できません。'
                        else:
                            status18 = '該当0走'
                            reason18 = '対象条件のペース判定済みレースには該当なし。苦手という意味ではありません。'
                    elif n18 < 2 or other_n18 < 2:
                        status18 = '比較不足'
                        reason18 = '想定ペースと他ペースの両方が2走以上必要なため、優劣は判定しません。'
                    elif diff18 >= 25:
                        status18 = '他より高め' if min(n18,other_n18) >= 4 else '他より高め※'
                        reason18 = '他ペースより3着内率が25ポイント以上高い。統計的な有意差や未来の勝率を意味しません。'
                    elif diff18 <= -25:
                        status18 = '他より低め' if min(n18,other_n18) >= 4 else '他より低め※'
                        reason18 = '他ペースより3着内率が25ポイント以上低いという相対比較です。このペースの絶対的な成績が悪いとは限りません。'
                    else:
                        status18 = '同程度' if min(n18,other_n18) >= 4 else '同程度※'
                        reason18 = '他ペースとの3着内率差が25ポイント未満です。'
                    good_rows18 = selected18.loc[selected18['着順_数値'].le(3)]
                    corners18 = pd.to_numeric(good_rows18['4角順位'], errors='coerce').dropna()
                    agari18 = pd.to_numeric(good_rows18['上がり順位'], errors='coerce').dropna()
                    pos_note18 = f'4角平均{corners18.mean():.1f}番手' if len(corners18) else '4角の有効データなし'
                    if len(agari18):
                        pos_note18 += f'／上がり3位内{int(agari18.le(3).sum())}/{len(agari18)}走'
                    results18.append({'馬名':horse18, '相性':status18,
                                      '実績': f'{good18}/{n18}走' if n18 else '—',
                                      '3着内率':f'{pct18:.1f}%' if pct18 is not None else '—',
                                      '想定ペース対象走数':n18, '他ペース対象走数':other_n18,
                                      '他ペース実績':f'{other_good18}/{other_n18}走' if other_n18 else '—',
                                      'ペース判定不能走数':unknown18})
                    details18[horse18] = (f'想定ペース：{pace_key18}／3着以内{good18}/{n18}走'
                                          + (f'（{pct18:.1f}%）' if pct18 is not None else '')
                                          + f'。他ペース：{other_good18}/{other_n18}走'
                                          + (f'（{other_pct18:.1f}%）' if other_pct18 is not None else '')
                                          + f'。未判定：{unknown18}走。{reason18} 好走時：{pos_note18}。')
                pace_result18 = pd.DataFrame(results18)
                if not pace_result18.empty:
                    pace_display18 = display_horse_column(pace_result18, horses, st.session_state.auto_numbers)
                    fixed_order_table_v209(
                        pace_display18, ['馬名','相性','実績'],
                        {'馬名':'馬番＋正式馬名','相性':'他ペース比','実績':'3着内／出走'})
                    with st.expander('S/M/Hの判定データ監査', expanded=False):
                        pace_audit210 = pace_result18[['馬名','想定ペース対象走数',
                            '他ペース対象走数','ペース判定不能走数','相性','実績']].copy()
                        pace_audit210['判定済み走数'] = (pace_audit210['想定ペース対象走数']
                                                       + pace_audit210['他ペース対象走数'])
                        pace_audit210['判定確認'] = pace_audit210.apply(
                            lambda r: '要確認：合計が10走超'
                            if r['判定済み走数'] + r['ペース判定不能走数'] > 10
                            else '参考：該当1走以下'
                            if r['想定ペース対象走数'] <= 1
                            else '参考：他ペース1走以下'
                            if r['他ペース対象走数'] <= 1
                            else '少数例' if min(r['想定ペース対象走数'],r['他ペース対象走数']) < 4
                            else '件数確認済み', axis=1)
                        fixed_order_table_v209(
                            display_horse_column(pace_audit210, horses, st.session_state.auto_numbers),
                            ['馬名','想定ペース対象走数','他ペース対象走数','ペース判定不能走数','判定確認'],
                            {'馬名':'馬番＋正式馬名','想定ペース対象走数':'選択S/M/H',
                             '他ペース対象走数':'他ペース','ペース判定不能走数':'未判定',
                             '判定確認':'監査メモ'})
                        st.caption('3着内率の差は標本数が少ないと大きく変動します。ここでは判定の根拠件数を確認し、予測精度は主張しません。')
                    st.caption('相性はその馬自身の他ペースと比較した傾向。実績は選択ペースでの3着以内回数／出走回数。※は少数走数の参考判定。'
                               ' 該当0走と、ペース判定データ不足は区別しています。将来の勝率予測ではありません。')
                    with st.expander('各馬の根拠・対象走数・他ペースを確認',expanded=False):
                        chosen_pace18 = tap_choice('確認する馬', pace_display18['馬名'].tolist(), 'scenario_horse_v18')
                        chosen_index18 = pace_display18['馬名'].tolist().index(chosen_pace18)
                        chosen_raw18 = pace_result18.iloc[chosen_index18]['馬名']
                        st.write(details18[chosen_raw18])
                        st.caption('集計対象：各馬の直近10走に含まれる重賞。netkeibaのS/M/H判定済みデータのみ。')
                    st.download_button('想定ペース別比較をCSVで保存',
                                       pace_display18.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_scenario_v182.csv',mime='text/csv',
                                       width='stretch')
                # Ver.20.14: 横長の5画面ではなく、1地点だけ表示して縦スクロールを削減。
                with st.expander('隊列予想（地点を切り替え）', expanded=False):
                    stage214 = st.radio('表示する地点',
                        ['1角','2角','3角','4角','最後の直線'],
                        horizontal=True, key='corner_stage_v214')
                    proj214 = corner_projection_v2013(analysis_df, quick_names171)
                    label_df214 = display_horse_column(pd.DataFrame({'馬名':quick_names171}),
                        horses, st.session_state.auto_numbers)
                    label_map214 = dict(zip(quick_names171,label_df214['馬名'].tolist()))
                    groups214 = {k: [] for k in ('逃げ','先行','中団','後方','不明')}
                    for name214 in quick_names171:
                        # 最後の直線は4角の位置を「直線入口」として表示。
                        # 直線途中の隊列や追い抜きは通過順データだけでは不明。
                        use_stage214 = '4角' if stage214 == '最後の直線' else stage214
                        group214, count214 = proj214[name214][use_stage214]
                        full214 = str(label_map214.get(name214,name214))
                        match214 = re.match(r'^([0-9０-９]+)\s*(.+)$',full214)
                        short214 = (match214.group(1)+' '+match214.group(2)[:2]
                                    if match214 else full214[:4])
                        import html as _html214
                        groups214[group214].append(
                            '<span title="'+_html214.escape(full214,quote=True)+'" '
                            'style="display:inline-block;background:white;color:#182b1b;'
                            'border-radius:18px;margin:3px;padding:6px 8px;font-weight:700;">'
                            +_html214.escape(short214)+' <small>'+str(count214)+'走</small></span>')
                    rows214 = ''.join(
                        '<div style="border-bottom:1px dashed #ffffff80;padding:8px 4px">'
                        '<b>'+g214+'</b><div>'+(''.join(groups214[g214]) or '—')+'</div></div>'
                        for g214 in ('逃げ','先行','中団','後方','不明'))
                    title214 = '直線入口（4角位置を使用）' if stage214=='最後の直線' else stage214
                    st.markdown('<div style="background:#b5db69;color:#182b1b;padding:12px;'
                                'border-radius:12px"><h4 style="text-align:center">'
                                +title214+'</h4>'+rows214+'</div>',unsafe_allow_html=True)
                    if stage214 == '最後の直線':
                        st.caption('直線入口の位置のみ。直線での追い抜き・加速を予測したものではありません。'
                                   '上がり3Fと4角順位を合わせた直線途中の予測は今後の検証課題です。')
                    else:
                        st.caption('過去5走のコーナー通過順を頭数比で分類。該当地点の記録がなければ不明。'
                                   '馬名横の数字は有効走数。今回の隊列を確定するものではありません。')
                with st.expander('仕掛けどころ・直線の追い上げ傾向（直近5走）', expanded=False):
                    profiles215 = movement_profile_v2015(analysis_df, quick_names171)
                    rows215 = []
                    for name215 in quick_names171:
                        (c215, nc215), (f215, nf215) = profiles215[name215]
                        rows215.append({'馬名': name215,
                            '3角→4角': c215, '角の有効走数': nc215,
                            '4角→ゴール': f215, '直線の有効走数': nf215})
                    movement215 = display_horse_column(
                        pd.DataFrame(rows215), horses, st.session_state.auto_numbers)
                    fixed_order_table_v209(movement215,
                        ['馬名','3角→4角','角の有効走数','4角→ゴール','直線の有効走数'],
                        {'馬名':'馬番＋馬名','3角→4角':'コーナーでの動き',
                         '角の有効走数':'対象走','4角→ゴール':'直線の追い上げ',
                         '直線の有効走数':'対象走'}, max_height=540)
                    st.markdown('**全頭の位置変化回数（条件別・直近最大5走）**')
                    st.caption('ここでの絞り込みは位置変化回数の表とCSVだけに適用。総合診断の予測計算は変更しません。')
                    filter_mode218 = st.selectbox('比較する過去走',
                        ['全条件（従来どおり）', '芝・1800〜2200m', '芝・2000m前後（±200m）',
                         '東京・芝・1800〜2200m', '芝・良馬場・1800〜2200m'],
                        key='movement_filter_v2018')
                    move_data218 = analysis_df.copy()
                    if filter_mode218 != '全条件（従来どおり）':
                        course218 = move_data218['コース'].astype(str).str.strip()
                        dist218 = pd.to_numeric(move_data218['距離_m'], errors='coerce')
                        move_data218 = move_data218.loc[course218.eq('芝') & dist218.between(1800,2200)].copy()
                        if filter_mode218.startswith('東京'):
                            move_data218 = move_data218.loc[move_data218['競馬場'].astype(str).str.contains('東京', na=False)].copy()
                        if '良馬場' in filter_mode218:
                            move_data218 = move_data218.loc[move_data218['馬場状態'].astype(str).str.strip().eq('良')].copy()
                    st.caption('各馬について、条件に合うレースのうち日付が新しい最大5走を集計。'
                               '対象0走は「データなし」で、0回の位置維持とは異なります。')

                    counts217 = movement_counts_v2017(move_data218, quick_names171)
                    count_rows217 = []
                    for name217 in quick_names171:
                        (cc217, cn217, cm217), (fc217, fn217, fm217), total217 = counts217[name217]
                        count_rows217.append({
                            '馬名': name217,
                            '3角→4角': cc217.replace('上昇','↑').replace('維持','→').replace('後退','↓').replace('／','  '),
                            '角の有効': f'{cn217}/{total217}',
                            '角の欠損': cm217,
                            '4角→ゴール': fc217.replace('上昇','↑').replace('維持','→').replace('後退','↓').replace('／','  '),
                            'ゴールの有効': f'{fn217}/{total217}',
                            'ゴールの欠損': fm217})
                    count_df217 = display_horse_column(
                        pd.DataFrame(count_rows217), horses, st.session_state.auto_numbers)
                    fixed_order_table_v209(count_df217,
                        ['馬名','3角→4角','角の有効','角の欠損','4角→ゴール','ゴールの有効','ゴールの欠損'],
                        {'馬名':'馬番＋馬名','3角→4角':'コーナーの回数',
                         '角の有効':'有効走','角の欠損':'欠損走',
                         '4角→ゴール':'ゴールまでの回数',
                         'ゴールの有効':'有効走','ゴールの欠損':'欠損走'}, max_height=540)
                    st.caption('上昇・維持・後退は同一レース内の実際の順位差で分類（1つでも順位を上げれば上昇）。'
                               '上の「傾向」は頭数補正後の中央値による別の判定です。'
                               '欠損走は通過順などが足りず計算できない走数で、維持には含めません。'
                               '海外レースなどでは通過順が欠損する場合があります。')
                    st.download_button('全頭の位置変化回数をCSV保存',
                        pd.DataFrame(count_rows217).to_csv(index=False).encode('utf-8-sig'),
                        file_name='keiba_movement_counts_v2017.csv',
                        mime='text/csv', width='stretch')
                    audit_all218 = movement_audit_v2016(move_data218, quick_names171)
                    st.download_button('全頭の過去走・検算明細をCSV保存（こちらを送ってね）',
                        audit_all218.to_csv(index=False).encode('utf-8-sig'),
                        file_name='keiba_movement_all_runs_v2018.csv',
                        mime='text/csv', width='stretch', key='download_movement_all_v2018')
                    st.caption('この明細CSVには全頭のレース別通過順・3角/4角・着順・順位変化・欠損理由が入っています。'
                               'ChatGPTへの検証依頼はスクショではなく、このCSVを添付してください。')
                    with st.expander('クロワ・レガレイラ・マスカレードの過去5走を検算', expanded=False):
                        audit_names216 = [n for n in quick_names171 if any(
                            key in n for key in ('クロワデュノール','レガレイラ','マスカレードボール'))]
                        if not audit_names216:
                            st.info('検証対象3頭のデータがありません。')
                        else:
                            audit216 = movement_audit_v2016(analysis_df, audit_names216)
                            st.caption('各走の3角・4角・着順を原文から再計算。'
                                       '正の値＝順位上昇、負の値＝順位低下。'
                                       '補正値＝順位変化÷(頭数−1)。'
                                       '現行の判定は各馬の補正値の中央値が±0.05以上かで分類。')
                            st.dataframe(audit216, hide_index=True, width='stretch')
                            for name216 in audit_names216:
                                subset216 = audit216[audit216['馬名'].eq(name216)]
                                st.markdown('**' + name216 + '**')
                                for col216, title216 in [
                                    ('3角→4角_補正値','3角→4角'),
                                    ('4角→着順_補正値','4角→ゴール')]:
                                    nums216 = pd.to_numeric(subset216[col216], errors='coerce').dropna()
                                    if len(nums216):
                                        median216 = float(nums216.median())
                                        result216 = ('上昇' if median216 >= .05 else
                                                     '低下' if median216 <= -.05 else '維持')
                                        st.write(f'{title216}：有効{len(nums216)}走／'
                                                 f'補正値中央値 {median216:+.3f}／{result216}')
                                    else:
                                        st.write(f'{title216}：有効0走（判定不能）')
                            st.download_button(
                                '3頭の過去走検算データをCSV保存',
                                audit216.to_csv(index=False).encode('utf-8-sig'),
                                file_name='keiba_movement_audit_v2016.csv',
                                mime='text/csv', width='stretch')
                            st.caption('※3角→4角は通過順位の比較。'
                                       '4角→ゴールは着順との比較であり、'
                                       '実際の直線での加速地点や追い抜きの瞬間は分かりません。'
                                       'レースごとの通過順を公式記録と照合して精度を検証してください。')
                    st.caption('同一過去レース内の順位変化を頭数で補正し、中央値で分類。'
                               '4角→ゴールは着順との比較であり、直線途中の位置を再現したものではありません。'
                               '少数走数は参考扱い。S/M/H・競馬場・距離を区別した将来予測ではありません。')
            if selected_page208 == '🏇 総合診断':
                if quick_shapes171.empty:
                    st.info('保存済みのラップ分析結果がないため、現時点では位置取り・末脚を中心に表示しています。分析条件からラップを取得すると展開診断も加わります。')
                st.caption('「参考」「暫定」は少数データの目印。レース全体のラップを馬自身の個別ラップとして扱いません。')
                with st.expander('気になる馬の診断根拠を見る', expanded=False):
                    chosen171 = tap_choice('馬を選ぶ', quick_display171['馬名'].tolist(), 'quick_diagnosis_horse_v171')
                    row171 = quick_display171.loc[quick_display171['馬名'].eq(chosen171)].iloc[0]
                    st.markdown(f"**{row171['馬名']}：{row171['走りの総合診断']}**　{row171['信頼度']}")
                    st.write(row171['根拠'])
                    st.caption(f"好走 {row171['好走走数']}/{row171['対象走数']}走、ラップ確認 {row171['ラップ確認走数']}走、4角取得 {row171['位置取得']}走、上がり取得 {row171['上がり取得']}走")
                st.download_button('総合診断をCSVで保存', quick_display171.to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_quick_diagnosis_v171.csv', mime='text/csv', width='stretch')
        else:
            st.info('診断に使える出走馬データがありません。')

# 各馬の特徴では総合診断の計算結果だけを再利用し、総合診断画面は表示しない。
if selected_page208 == '🐎 各馬の特徴':
    main_holder208.empty()

if selected_page208 == '🐎 各馬の特徴':
    detail_holder208 = st.empty()
    with detail_holder208.container():
        st.subheader('各馬の特徴・根拠')
        st.caption('普段の比較はここだけでOK。複雑な絞り込みや旧式の集計は「分析条件」にあります。')
        if 'quick_display171' in locals() and isinstance(quick_display171, pd.DataFrame) and not quick_display171.empty:
            summary_cols205 = [c for c in ['馬名','走りの総合診断','展開','信頼度'] if c in quick_display171.columns]
            fixed_order_table_v209(
                quick_display171, summary_cols205,
                {'馬名':'馬番＋正式馬名','走りの総合診断':'走りの特徴',
                 '展開':'好走展開','信頼度':'信頼度'}, max_height=510)
            with st.expander('気になる馬だけ根拠を表示', expanded=False):
                choice205 = tap_choice('馬を選ぶ', quick_display171['馬名'].tolist(), 'quick_detail_horse_205')
                rec205 = quick_display171.loc[quick_display171['馬名'].eq(choice205)].iloc[0]
                st.write(rec205.get('根拠','根拠データなし'))
                st.caption(f"対象 {rec205.get('対象走数','—')}走／ラップ {rec205.get('ラップ確認走数','—')}走")
        else:
            st.info('先に出走馬の成績を取得してください。')

if selected_page208 == '🧰 管理・検証':
    with tab_quality172:
        st.subheader('開発・実験ラボ')
        st.caption('以下は開発中の検証用です。正式な予測画面には出さず、必要なときだけ開いて確認します。')
        with st.expander('旧式・部分データ再現テスト（開発用）', expanded=False):
            st.caption('保存済みの馬だけで、当時のレース前までの履歴から予測を再計算します。'
                       ' **過去レースの全出走馬は含まれないため、表示する一致率は本番の予測的中率ではありません。**')
            st.caption('当日の成績は予測に使用しません。実際のS/M/Hはnetkeiba表示を正解ラベルとして比較します。')
            if st.button('過去レースの再現テストを実行', key='run_historical_v194', width='stretch'):
                with st.spinner('過去レースを照合中（外部通信なし）…'):
                    result194, summary194 = historical_replay_v194(analysis_df, minimum_known=3)
                    st.session_state['replay_results_v194'] = result194
                    st.session_state['replay_summary_v194'] = summary194
                    st.session_state['replay_race_v194'] = race_id
            if st.session_state.get('replay_race_v194') == race_id:
                result194 = st.session_state.get('replay_results_v194')
                summary194 = st.session_state.get('replay_summary_v194', {})
                if isinstance(result194, pd.DataFrame):
                    st.write(f"対象候補 **{summary194.get('候補レース', 0)}レース**／"
                             f"部分データで比較可能 **{summary194.get('検証可能', 0)}レース**／"
                             f"一致 **{summary194.get('一致', 0)}件**")
                    if summary194.get('検証可能', 0) < 10:
                        st.warning('比較可能なレースが少ないため、予測精度の結論は出せません。'
                                   '今後、過去レースの実際の全出走馬を取得する検証が必要です。')
                    if not result194.empty:
                        cols194 = ['開催日','レース名','実際のペース','予測','一致',
                                   '記録済み出走馬','事前位置データあり','判定状態']
                        st.dataframe(result194[cols194], hide_index=True, width='stretch')
                        with st.expander('レース別の予測根拠とIDを見る', expanded=False):
                            st.dataframe(result194, hide_index=True, width='stretch')
                        st.download_button('ペース予測の再現テスト結果をCSVで保存',
                                           result194.to_csv(index=False).encode('utf-8-sig'),
                                           file_name='keiba_pace_replay_v194.csv', mime='text/csv',
                                           width='stretch')
                    else:
                        st.info('照合可能なレースがありません。レースID・開催日・netkeibaペースの取得状況を確認してください。')
            st.caption('このテストは現出走馬の保存済み履歴だけを使う暫定検証。全出走馬での事前予測検証は別途必要です。')


        with st.expander('全出走馬での再現テスト（開発用）',expanded=False):
            st.caption('全出走馬の当該レース以前の成績だけでS/M/Hを再現。レース選定時には正解ラベルを利用しますが、予測器には渡しません。')
            st.caption('Hを指定して検証した結果は、意図的に抽出したサンプルです。全体の的中率とは混ぜて評価しないでください。')
            candidates196 = analysis_df.copy()
            candidates196['_rid196'] = candidates196['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
            candidates196 = candidates196.loc[candidates196['_rid196'].str.fullmatch(r'\d{12}')].copy()
            candidates196 = candidates196.loc[candidates196['netkeibaペース'].isin(V195_TAG)].copy()
            candidates196['_date196'] = pd.to_datetime(candidates196['日付'],errors='coerce')
            candidates196 = candidates196.sort_values('_date196',ascending=False).drop_duplicates('_rid196')
            c196 = st.radio('検証するレースの選び方', ['直近から選ぶ','H（ハイ）だけ選ぶ','S・M・Hを均等に選ぶ'],
                             horizontal=False, key='v196_selection')
            n196_label = st.radio('検証レース数',['1レース','3レース','5レース','10レース','20レース'],
                                  horizontal=True,key='v196_count',index=1)
            n196 = int(n196_label.replace('レース',''))
            # 検証に使えるHレースが手元にない場合は、件数を水増ししない。
            h196 = candidates196.loc[candidates196['netkeibaペース'].eq('ハイ寄り')]
            st.caption(f'保存済みの検証候補：{len(candidates196)}レース／うちH：{len(h196)}レース')
            if c196 == 'H（ハイ）だけ選ぶ':
                chosen196 = h196.head(n196)
            elif c196 == 'S・M・Hを均等に選ぶ':
                base196 = n196 // 3
                extra196 = n196 % 3
                selected196=[]
                for i196,label196 in enumerate(['スロー寄り','ミドル寄り','ハイ寄り']):
                    count196 = base196 + (1 if i196 < extra196 else 0)
                    selected196.extend(candidates196.loc[candidates196['netkeibaペース'].eq(label196)].head(count196)['_rid196'].tolist())
                chosen196 = candidates196.loc[candidates196['_rid196'].isin(selected196)].sort_values('_date196',ascending=False)
            else:
                chosen196 = candidates196.head(n196)
            id_list196 = chosen196['_rid196'].tolist()
            if len(id_list196) < n196:
                st.info(f'指定条件に一致した対象は{len(id_list196)}レースです。存在しないレースは補いません。')
            if not chosen196.empty:
                preview196 = chosen196[['_date196','レース名','競馬場','距離_m','netkeibaペース']].copy()
                preview196['_date196']=preview196['_date196'].dt.strftime('%Y-%m-%d')
                preview196.columns=['開催日','レース名','競馬場','距離(m)','実際のS/M/H']
                with st.expander('検証予定レースを確認',expanded=False):
                    st.dataframe(preview196,hide_index=True,width='stretch')
            st.caption('過去の逃げ・先行時の前半ラップを検証できます。ラップ取得は追加通信が多いため、まず1〜3レースがおすすめ。')
            st.caption('同じ前半1000mでも後半が速ければS寄り、後半が落ちればH寄りになることがあります。馬場状態（良・稍重・重・不良）と芝/ダート、距離・開催場も区別して参考比較します。')
            do_laps198 = st.checkbox('前半ラップ＋前後半差＋馬場別基準を追加取得（Ver.20.5）', value=False, key='v198_laps')
            st.caption('チェック時は検証1レースにつき最大12件の過去レースのラップ表を追加照会。対象レース当日のラップは予測にも診断値にも使いません。')
            if st.button('全出走馬の事前データで検証を実行',key='v196_run',width='stretch',disabled=not bool(id_list196)):
                results196=[]
                progress196=st.progress(0,text='検証を開始します')
                for ri196,rid196 in enumerate(id_list196):
                    try:
                        def cb196(done,total,name):
                            progress196.progress((ri196+done/max(1,total))/len(id_list196),
                                                 text=f'{ri196+1}/{len(id_list196)}レース・{done}/{total}頭：{name}')
                        result196=full_field_replay_v195(rid196,analysis_df,progress_callback=cb196,collect_early_laps=do_laps198)
                        results196.append(result196)
                    except Exception as exc:
                        results196.append({'レースID':rid196,'取得状況':'検証エラー','取得失敗詳細':str(exc)[:250]})
                    progress196.progress((ri196+1)/len(id_list196))
                progress196.empty()
                st.session_state['full_replay_v196']=attach_comparison_v202(pd.DataFrame(results196))
                st.session_state['full_replay_race_v196']=race_id
                st.session_state['full_replay_selection_v196']=c196
                st.session_state['full_replay_lap_flag_v198']=do_laps198
            if st.session_state.get('full_replay_race_v196') == race_id:
                out196=st.session_state.get('full_replay_v196')
                if isinstance(out196,pd.DataFrame) and not out196.empty:
                    matched205=out196.loc[out196.get('予測',pd.Series(dtype=str)).isin(['S','M','H']) & out196.get('実際',pd.Series(dtype=str)).isin(['S','M','H'])] if {'予測','実際'}.issubset(out196.columns) else pd.DataFrame()
                    scored205=int(pd.to_numeric(out196.get('ラップ試験スコア(秒速さ)',pd.Series(dtype=float)),errors='coerce').notna().sum())
                    col_a205,col_b205,col_c205=st.columns(3)
                    col_a205.metric('検証件数',len(out196))
                    col_b205.metric('予測一致',f"{int(matched205['一致'].eq('一致').sum())}/{len(matched205)}" if len(matched205) else '—')
                    col_c205.metric('スコア算出',f'{scored205}/{len(out196)}')
                    st.caption('スコアは参考情報。現行S/M/H予測を変更しません。')
                    with st.expander('全項目の検証結果を見る',expanded=False):
                        st.dataframe(out196,hide_index=True,width='stretch')
                    if 'ラップスコア信頼度' in out196.columns:
                        grade205=out196['ラップスコア信頼度'].fillna('算出不可').value_counts()
                        st.caption('参考スコアの信頼度：' + ' ／ '.join(f'{k} {v}件' for k,v in grade205.items()))
                    st.caption('信頼度は参考基準の一致度・標本数・監査から付けた暫定評価。高でも精度が実証されたわけではありません。')
                    with st.expander('現行予測とラップ参考スコア（Ver.20.5）', expanded=False):
                        st.caption('ラップの秒速さは参考スコアです。現行予測は一切変更しません。実際のペースはスコア算出に使用しません。')
                        cols202=[c for c in ['レース名','競馬場','距離(m)','実際','予測','試験モデル予測',
                                    '一致','ラップ試験スコア(秒速さ)','ラップスコア状態','ラップ参考基準数(延べ走)',
                                    '比較用基準品質','ラップスコア信頼度','高信頼度比較走数','中信頼度比較走数','前方時ラップ取得率(%)','スコア信頼度の注意','前方時基準差1000m平均(秒)','ラップ参考説明']
                                 if c in out196.columns]
                        if '試験モデル予測' in out196.columns:
                            st.dataframe(out196[cols202],hide_index=True,width='stretch')
                            matched202=out196.loc[out196['予測'].isin(['S','M','H']) & out196['実際'].isin(['S','M','H'])]
                            if not matched202.empty:
                                base202=int(matched202['一致'].eq('一致').sum())
                                scored202=int(pd.to_numeric(matched202.get('ラップ試験スコア(秒速さ)'),errors='coerce').notna().sum())
                                st.write(f'比較可能 **{len(matched202)}レース**：現行 **{base202}件一致**。ラップ参考スコア算出 **{scored202}レース**。予測変更は **0件**。')
                            st.caption('参考スコア＝前方時のレース全体1000mラップが基準より何秒速いか。プラスは速め、マイナスは遅め。的中確率ではありません。高・中信頼度が延べ3走未満なら算出しません。')
                        else:
                            st.info('Ver.20.3で「ラップを追加取得」にチェックして再実行すると比較結果が表示されます。')
                    with st.expander('ラップスコアのペース別分布・算出率（Ver.20.5）', expanded=False):
                        st.caption('実際のS/M/Hは検証結果のグループ分けにのみ使用し、予測や参考スコアの算出には使いません。通常抽出とH限定抽出の結果は別々に比較してください。')
                        if {'実際','ラップ試験スコア(秒速さ)'}.issubset(out196.columns):
                            compare204=out196.loc[out196['実際'].isin(['S','M','H'])].copy()
                            compare204['_score204']=pd.to_numeric(compare204['ラップ試験スコア(秒速さ)'],errors='coerce')
                            compare204['_audit204']=pd.to_numeric(compare204.get('事前走歴当日以降混入走数',pd.Series(0,index=compare204.index)),errors='coerce').fillna(0)
                            compare204['_baseline_audit204']=pd.to_numeric(compare204.get('基準最終日_時系列違反',pd.Series(0,index=compare204.index)),errors='coerce').fillna(0)
                            sumrows204=[]
                            for pace204 in ['S','M','H']:
                                s204=compare204.loc[compare204['実際'].eq(pace204)]
                                valid204=s204['_score204'].dropna()
                                n204=len(s204)
                                sumrows204.append({'実際':pace204,'検証レース':n204,'スコア算出':len(valid204),
                                    '算出率(%)':round(100*len(valid204)/n204,1) if n204 else None,
                                    '平均スコア(秒)':round(float(valid204.mean()),3) if len(valid204) else None,
                                    '中央値(秒)':round(float(valid204.median()),3) if len(valid204) else None,
                                    '最小(秒)':round(float(valid204.min()),3) if len(valid204) else None,
                                    '最大(秒)':round(float(valid204.max()),3) if len(valid204) else None})
                            table204=pd.DataFrame(sumrows204)
                            st.dataframe(table204,hide_index=True,width='stretch')
                            score_ok204=compare204.dropna(subset=['_score204'])
                            if not score_ok204.empty:
                                st.markdown('**各レースのスコア（比較できたものだけ）**')
                                display204=[c for c in ['レース名','実際','予測','ラップ試験スコア(秒速さ)',
                                    'ラップ参考基準数(延べ走)','ラップスコア信頼度','スコア信頼度の注意','時系列監査'] if c in score_ok204.columns]
                                st.dataframe(score_ok204[display204],hide_index=True,width='stretch')
                            failed204=int((compare204['_audit204']>0).sum()+(compare204['_baseline_audit204']>0).sum())
                            unknown204=int(pd.to_numeric(compare204.get('事前走歴日付不明走数',pd.Series(0,index=compare204.index)),errors='coerce').fillna(0).sum())
                            st.caption(f'今回の確認：走歴・基準日の監査違反を含むレース延べ件数 {failed204}、事前走歴の日付不明 {unknown204}走。監査は取得したデータの範囲内です。')
                            if st.session_state.get('full_replay_selection_v196') != '直近から選ぶ':
                                st.warning('この抽出は実際のペースを利用してレースを選んでいます。通常の一致率やスコア分布と単純合算しないでください。')
                            if len(score_ok204)<10:
                                st.info('スコア算出レースが10件未満。平均値の差だけで、スコアが予測に有効とは判断できません。')
                            st.caption('「基準不足」のレースは算出率の分母に含み、スコアの平均には含みません。偏った欠測による見かけの差に注意してください。')
                        else:
                            st.info('前半ラップ取得をONにして検証を再実行すると、ペース別の分布が表示されます。')
                    with st.expander('時系列監査（Ver.20.5）', expanded=False):
                        audit_cols203=[c for c in ['レース名','開催日','時系列監査','監査対象の事前走歴数',
                                   '事前走歴最新日','事前走歴日付不明走数',
                                   '事前走歴当日以降混入走数','基準に使った最終日',
                                   '基準最終日_時系列違反','未来基準データ除外延べ件数'] if c in out196.columns]
                        st.dataframe(out196[audit_cols203],hide_index=True,width='stretch')
                        st.caption('監査は今回の再現処理で予測入力に渡した走歴の日付と、ラップ基準の記録された最終日を確認します。走歴の出典原票やペース取得サイトの公開日時まで照合する完全監査ではありません。')
                    with st.expander('H見逃しの要因を比較（Ver.20.3）', expanded=False):
                        needed197 = ['レース名','実際','予測','逃げ候補','先頭経験馬数',
                                     '複数回先頭馬数','逃げ候補先頭率平均(%)',
                                     '先行圧力率(%)','逃げ候補_先頭S','逃げ候補_先頭M',
                                     '逃げ候補_先頭H','逃げ候補_先頭判定不足','前方H率(%)']
                        available197 = [c for c in needed197 if c in out196.columns]
                        if len(available197) > 3:
                            st.dataframe(out196[available197],hide_index=True,width='stretch')
                            st.caption('先頭・前方は過去5走の最初に記録された通過地点。先行圧力率は逃げ候補以外の前方走数÷確認走数。結果は診断指標に使わず、比較のためだけに表示。')
                        else:
                            st.info('Ver.20で再検証すると診断指標を表示できます。')
                    if st.session_state.get('full_replay_lap_flag_v198'):
                        with st.expander('逃げ・先行時のラップ形状を比較（Ver.20.5）', expanded=False):
                            cols198=['レース名','実際','予測','競馬場','距離(m)',
                                     '前半ラップ照会レース数','前半ラップ取得OKレース数',
                                     '先頭時ラップ対象走数','先頭時ラップ確認走数',
                                     '先頭時前半600m平均(秒)','先頭時前半1000m平均(秒)',
                                     '前方時ラップ対象走数','前方時ラップ確認走数',
                                     '前方時前半600m平均(秒)','前方時前半1000m平均(秒)',
                                     '先頭時前後半600m差平均(秒)','前方時前後半600m差平均(秒)',
                                     '先頭時前半1000m相対差平均(秒/200m)','前方時前半1000m相対差平均(秒/200m)',
                                     '同競馬場・近距離先頭時走数','同競馬場・近距離先頭時前後半600m差平均(秒)',
                                     '馬場状態','先頭時馬場一致ラップ走数','先頭時馬場一致前半1000m平均(秒)',
                                     '前半ラップ相対基準作成走数','基準不足走数',
                                     '基準信頼度_高','基準信頼度_中','基準信頼度_低','基準信頼度_算出不可',
                                     '未来基準データ除外延べ件数','基準に使った最終日',
                                     '先頭時基準差600m平均(秒)','先頭時基準差1000m平均(秒)',
                                     '前方時基準差1000m平均(秒)']
                            cols198=[x for x in cols198 if x in out196.columns]
                            st.dataframe(out196[cols198],hide_index=True,width='stretch')
                            st.caption('前後半600m差＝前半600m−後半600m。負なら前半が速く、正なら後半が速い。前半1000m相対差は最初の1000mと残区間の200m平均の差。いずれもレース全体のラップで、馬自身の個別時計ではありません。')
                            st.info('大逃げ注意：1頭が後続を大きく離すと、先頭馬のペースと後続集団の体感ペースが異なります。現在の通過順位とレースラップだけでは先頭・後続の秒差を検証できないため、今回のモデルでは大逃げを確定判定しません。')
                            st.caption('Ver.20.1：比較する各過去走より前の独立レースだけで基準を作成。芝/ダート・距離±200mを前提に、同競馬場・同馬場を優先。高＝同場・同馬場、中＝同馬場・他場、低＝馬場混在の参考値。比較先3レース未満なら基準不足とし、条件を緩めた場合は名称を明記。差が負なら基準より前半が速い。参考基準は取得した最大12レースだけで作成するため統計的な標準値ではありません。良馬場内の時計差、含水率、芝の傷み、風、大逃げは未補正。')
                            if '馬場状態別先頭ラップ' in out196.columns:
                                with st.expander('馬場状態別・相対ラップの詳細（Ver.20.3）',expanded=False):
                                    st.dataframe(out196[[c for c in ['レース名','馬場状態','馬場状態別先頭ラップ','馬場状態別前方ラップ','相対ラップ条件詳細'] if c in out196.columns]],hide_index=True,width='stretch')
                            if {'実際','先頭時前半600m平均(秒)'}.issubset(out196.columns):
                                tmp198=out196.copy()
                                tmp198['先頭時前半600m平均(秒)']=pd.to_numeric(tmp198['先頭時前半600m平均(秒)'],errors='coerce')
                                tmp198=tmp198.dropna(subset=['先頭時前半600m平均(秒)'])
                                if not tmp198.empty:
                                    summary198=tmp198.groupby('実際',sort=False)['先頭時前半600m平均(秒)'].agg(['count','mean']).reset_index()
                                    summary198.columns=['実際のペース','比較可能レース','逃げ候補・先頭時の平均前半600m(秒)']
                                    summary198['逃げ候補・先頭時の平均前半600m(秒)']=summary198['逃げ候補・先頭時の平均前半600m(秒)'].round(2)
                                    st.dataframe(summary198,hide_index=True,width='stretch')
                                    st.caption('この集計は抽出された検証対象だけの平均。S/M/H間の優劣や予測能力を証明する数値ではありません。')
                            if '先頭時ラップ詳細' in out196.columns:
                                with st.expander('先頭時の過去走ラップ詳細', expanded=False):
                                    st.dataframe(out196[['レース名','先頭時ラップ詳細','ラップ取得エラー']],hide_index=True,width='stretch')
                    st.download_button('Ver.20.5 信頼度・監査付き全頭再現テストCSVを保存',
                                       out196.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_full_field_replay_v205.csv',mime='text/csv',width='stretch')
                    if {'予測','実際','一致'}.issubset(out196.columns):
                        verified196=out196.loc[out196['予測'].isin(['S','M','H']) & out196['実際'].isin(['S','M','H'])].copy()
                        if not verified196.empty:
                            correct196=int(verified196['一致'].eq('一致').sum())
                            st.write(f'比較可能 **{len(verified196)}レース**／一致 **{correct196}件**（{correct196/len(verified196):.1%}）')
                            if st.session_state.get('full_replay_selection_v196') != '直近から選ぶ':
                                st.warning('S/M/Hの正解を使って検証対象を選んだため、この一致率は通常のレース全体の予測的中率ではありません。')
                            st.markdown('**実際のペース別**')
                            by_actual196=(verified196.groupby('実際',sort=False).agg(
                                検証数=('一致','size'),一致数=('一致',lambda s:int(s.eq('一致').sum()))).reset_index())
                            by_actual196['一致率(%)']=(by_actual196['一致数']/by_actual196['検証数']*100).round(1)
                            st.dataframe(by_actual196,hide_index=True,width='stretch')
                            for grouping196 in ['距離区分','競馬場','馬場状態']:
                                if grouping196 in verified196.columns:
                                    st.markdown(f'**{grouping196}別**')
                                    by_group196=(verified196.groupby(grouping196,dropna=False).agg(
                                        検証数=('一致','size'),一致数=('一致',lambda s:int(s.eq('一致').sum()))).reset_index())
                                    by_group196['一致率(%)']=(by_group196['一致数']/by_group196['検証数']*100).round(1)
                                    st.dataframe(by_group196,hide_index=True,width='stretch')
                            st.caption('各グループの試行数が少ない場合、割合の差を予測能力の差と断定できません。')
                        else:
                            st.info('比較可能な予測がありません。取得失敗詳細・判定保留を確認してください。')

        st.subheader('旧式・レース格の詳細監査（開発用）')
        st.caption('元のレース名と格付け根拠を見ながら、誤判定・判定不明を確認できます。ここで表示するのはフィルター適用前のデータです。')
        audit = analysis_df.copy()
        if '日付' in audit.columns:
            audit = audit.sort_values(['馬名', '日付'], ascending=[True, False], kind='stable')
        audit_horses = ['全馬'] + sorted(audit['馬名'].unique().tolist())
        with st.expander('過去レースの格付け一覧を確認', expanded=False):
            audit_horse = tap_choice('確認する馬', audit_horses, 'audit_horse_v153')
            audit_grade = tap_choice('表示する格付け', ['すべて', 'G1', 'G2', 'G3', 'その他', '不明'], 'audit_grade_v153')
            if audit_horse != '全馬':
                audit = audit[audit['馬名'] == audit_horse]
            if audit_grade != 'すべて':
                audit = audit[audit['レース格'] == audit_grade]
            audit_cols = ['馬名', '日付', 'レース名', 'レース格', '格付け根拠', '着順_数値', 'レースID']
            audit_view = audit[[c for c in audit_cols if c in audit.columns]].copy()
            if '日付' in audit_view.columns:
                audit_view['日付'] = pd.to_datetime(audit_view['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
            if 'レースID' in audit_view.columns:
                ids = audit_view['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
                audit_view['レース結果URL'] = ids.apply(lambda v: f'https://race.netkeiba.com/race/result.html?race_id={v}' if re.fullmatch(r'\d{12}', v) else '')
                audit_view = audit_view.drop(columns=['レースID'])
            st.write(f'表示：{len(audit_view)}走（元データ：{len(analysis_df)}走）')
            st.dataframe(display_horse_column(audit_view, horses, st.session_state.auto_numbers), hide_index=True, width='stretch',
                         column_config={'馬名': st.column_config.TextColumn('馬名', width='medium'),
                                        'レース名': st.column_config.TextColumn('レース名', width='medium'),
                                        '格付け根拠': st.column_config.TextColumn('格付け根拠', width='large'),
                                        'レース結果URL': st.column_config.LinkColumn('レース結果を確認', display_text='結果を見る')})
            st.download_button('格付けチェック表をCSVで保存', display_horse_column(audit_view, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_grade_audit.csv', mime='text/csv', width='stretch')
            missing_names = int(analysis_df['レース名'].fillna('').eq('').sum())
            if missing_names:
                st.warning(f'レース名がないデータが{missing_names}走あります。旧バージョンの保存データは、レース格を検証するために再取得が必要です。')
            st.caption('格付け判定は取得ページの表記に依存します。G1・G2・G3と表示されても、根拠欄や実際のレース名と一致しているか確認してください。')


if selected_page208 == '⚙️ 分析条件':
    settings_holder208 = st.empty()
    with settings_holder208.container():
        st.subheader('分析条件')
        mode = st.radio('分析モード', ['ランキング', '馬を比較'], horizontal=True)
        venue = tap_choice('競馬場', ['すべて'] + sorted(analysis_df['競馬場'].unique().tolist()), 'tap_競馬場_v153')
        track = tap_choice('馬場状態', ['すべて'] + sorted(analysis_df['馬場状態'].unique().tolist()), 'tap_馬場状態_v153')
        course_options = ['すべて'] + sorted(analysis_df['コース'].dropna().astype(str).unique().tolist()) if 'コース' in analysis_df.columns else ['すべて']
        course = tap_choice('コース', course_options, 'tap_コース_v153')
        distance = tap_choice('距離', ['すべて', '1200m以下', '1201〜1600m', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'], 'tap_距離_v153')
        recent = tap_choice('対象レース', ['直近5走', '直近10走', '直近15走', '全走'], 'tap_対象レース_v153')
        grade_filter = tap_choice('レース格', ['全レース', '重賞のみ（G1・G2・G3）', 'G1のみ', 'その他（L・OP・条件戦など）'], 'tap_レース格_v153')
        st.caption('格付けが確認できない走は「不明」として保持。重賞・G1・その他の絞り込みから除外します。')
        pace_filter = tap_choice('推定ペース', ['すべて', 'スロー寄り', 'ミドル寄り', 'ハイ寄り', '不明'], 'tap_推定ペース_v153')
        st.caption('判定は前半600mと後半600mの差のみ（前半−後半）：−1秒未満＝前傾（ハイ寄り）、±1秒以内＝均衡（ミドル寄り）、＋1秒超＝後傾（スロー寄り）。道中のラップ、コース差、距離差は反映しない暫定分類です。')
        st.caption('脚質は4角位置と頭数から便宜的に推定。先頭（4角）＝逃げ確定ではなく、差し追込率は後方位置の割合です。')
        st.caption('集計順序：各馬の全成績から直近○走を確定 → 競馬場・馬場・距離・レース格・ペースで絞り込み。例：直近5走＋G1のみ＝直近5走の中にあるG1だけ。')
        counts = analysis_df['レース格'].value_counts()
        st.caption('判定済み：' + ' / '.join(f'{g} {int(counts.get(g, 0))}走' for g in ['G1', 'G2', 'G3', 'その他']) + f' / 不明 {int(counts.get("不明", 0))}走')
        if int(counts.get('不明', 0)):
            st.info('レース格が不明な成績があります。以前のバージョンで取得したデータは格付け未保存の場合があります。必要に応じて新しく過去成績を取得し直してください。')
        if mode == 'ランキング':
            metric = tap_choice('ランキング指標', METRICS, 'tap_ランキング指標_v153')
        else:
            options = sorted(analysis_df['馬名'].unique().tolist())
            selected = tap_horses('比較する馬（2〜3頭）', options, 'compare_horses_v153')
        view = st.radio('表示方法', ['横並び表', 'スマホ用カード'], horizontal=True)

        st.header('⑦ 分析する')
        # Streamlitはどのボタンでも再実行されるので、分析済み状態を保持する。
        # ラップ取得・CSVダウンロード等を押しても分析結果を消さない。
        if 'analysis_visible_v1581' not in st.session_state:
            st.session_state['analysis_visible_v1581'] = False
        if st.button('分析する', type='primary', width='stretch'):
            st.session_state['analysis_visible_v1581'] = True
            st.session_state['analysis_clicked_v2083'] = True
        if st.session_state['analysis_visible_v1581']:
            _analysis_t0_v2083 = time.perf_counter()
            # Ver.14.6：先に各馬の「直近N走」を確定し、その範囲内で条件を適用する。
            # 日付が取得できない走は末尾に置き、元の並びを保持する。
            base = analysis_df.copy()
            if '日付' in base.columns:
                base = base.sort_values('日付', ascending=False, na_position='last', kind='stable')
            if recent != '全走':
                n = int(recent.replace('直近', '').replace('走', ''))
                base = base.groupby('馬名', sort=False).head(n).copy()
            base_counts = base.groupby('馬名').size().rename('直近対象走数')

            filtered = base.copy()
            if venue != 'すべて':
                filtered = filtered[filtered['競馬場'] == venue]
            if track != 'すべて':
                filtered = filtered[filtered['馬場状態'] == track]
            if course != 'すべて' and 'コース' in filtered.columns:
                filtered = filtered[filtered['コース'] == course]
            if grade_filter != '全レース':
                grade_targets = {'重賞のみ（G1・G2・G3）': ['G1', 'G2', 'G3'],
                                 'G1のみ': ['G1'], 'その他（L・OP・条件戦など）': ['その他']}
                filtered = filtered[filtered['レース格'].isin(grade_targets[grade_filter])]
            # 比較用データはペース条件を掛ける直前に確保。距離条件はこの後両方に適用。
            pace_compare_base = filtered.copy()
            if pace_filter != 'すべて':
                filtered = filtered[filtered['推定ペース'] == pace_filter]
            ranges = {'1200m以下': (0, 1200), '1201〜1600m': (1201, 1600), '1601〜1800m': (1601, 1800), '1801〜2200m': (1801, 2200), '2201〜2400m': (2201, 2400), '2401m以上': (2401, float('inf'))}
            if distance in ranges:
                filtered = filtered[filtered['距離_m'].between(*ranges[distance])]
                pace_compare_base = pace_compare_base[pace_compare_base['距離_m'].between(*ranges[distance])]
            if mode == '馬を比較':
                if len(selected) < 2:
                    st.warning('比較する馬を2頭以上選んでください。')
                    st.stop()
                filtered = filtered[filtered['馬名'].isin(selected)]
                pace_compare_base = pace_compare_base[pace_compare_base['馬名'].isin(selected)]
            st.caption(f'直近走数を先に適用：{len(base)}走 → 条件絞り込み後：{len(filtered)}走（対象 {filtered["馬名"].nunique()}頭）')
            if filtered.empty:
                st.warning('指定した直近走数の範囲内に、条件に一致するレースがありません。対象レースを直近10走・全走に広げて確認してください。')
                st.stop()
            # この内訳はランキングと同じ filtered を使うため、表示・CSVの出走数と一致する。
            matched_counts = filtered.groupby('馬名').size().rename('条件一致走数')
            count_check = pd.concat([base_counts, matched_counts], axis=1).fillna(0).astype(int).reset_index()
            count_check = count_check.sort_values(['条件一致走数', '馬名'], ascending=[False, True])
            with st.expander('集計対象走数の確認（全馬）', expanded=False):
                st.caption('「直近対象走数」から、選んだレース格などに一致した「条件一致走数」だけをランキングに使用しています。0走の馬はランキングに出ません。')
                st.dataframe(display_horse_column(count_check, horses, st.session_state.auto_numbers), hide_index=True, width='stretch')
            rows = []
            for name, group in filtered.groupby('馬名'):
                row = summarize(group)
                row['馬名'] = name
                if mode == 'ランキング':
                    count = sample_count_for_metric(group, metric)
                    row['指標対象走数'] = count
                    row['データ量'] = sample_label(count)
                rows.append(row)
            result = pd.DataFrame(rows)
            if mode == 'ランキング':
                ascending = metric in ['平均着順', '平均4角', '平均上がり', '平均上がり順位']
                result = result.sort_values(metric, ascending=ascending, na_position='last', kind='stable').reset_index(drop=True)
                result.insert(0, '順位', result[metric].rank(method='min', ascending=ascending, na_option='bottom').astype(int))
                st.subheader(f'🏆 {metric} ランキング')
                st.caption('指標対象走数＝選択したランキング指標を計算できた走数。1〜2走は参考値、3〜4走は少数例です。順位にはサンプル数による補正を掛けていません。')
                if int((result['指標対象走数'] <= 2).sum()):
                    st.info(f'今回、{int((result["指標対象走数"] <= 2).sum())}頭が指標対象2走以下。高順位でも参考値として見てください。')
            else:
                result['表示順'] = result['馬名'].apply(selected.index)
                result = result.sort_values('表示順').drop(columns='表示順')
                st.subheader('🐎 馬の成績比較')
            if st.session_state.pop('analysis_clicked_v2083', False):
                st.session_state['last_analysis_profile_v2083'] = {
                    'race_id': race_id,
                    'compute': round(time.perf_counter() - _analysis_t0_v2083, 2),
                    'rows': int(len(result)),
                }
            result = result.round(2)
            if mode == 'ランキング':
                # 順位と馬名を常に先頭に。重要指標を続ける。
                front = ['順位', '馬名', '出走数', '指標対象走数', 'データ量', metric]
                result = result[[c for c in dict.fromkeys(front) if c in result.columns]
                                + [c for c in result.columns if c not in front]]
            else:
                result = result[['馬名'] + [c for c in result.columns if c != '馬名']]
            if view == '横並び表':
                if mode == 'ランキング':
                    full_table = st.checkbox('詳細指標をすべて表示', value=False)
                    key_cols = ['順位', '馬名', '出走数', '指標対象走数', 'データ量', metric, '複勝率(%)', '平均4角', '上がり最速率(%)']
                    compact_cols = [c for c in dict.fromkeys(key_cols) if c in result.columns]
                    show_table = result if full_table else result[compact_cols]
                    st.dataframe(display_horse_column(show_table, horses, st.session_state.auto_numbers), hide_index=True, width='stretch',
                                 column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
                else:
                    st.dataframe(display_horse_column(result, horses, st.session_state.auto_numbers).set_index('馬名').T, width='stretch')
            else:
                for _, row in result.iterrows():
                    with st.container(border=True):
                        label = f'🐎 {horse_display(row["馬名"], horses, st.session_state.auto_numbers)}'
                        if mode == 'ランキング':
                            label = f'{int(row["順位"])}位 {label}'
                        st.markdown(f'### {label}')
                        st.caption(f'対象：{int(row["出走数"])}走')
                        if mode == 'ランキング':
                            st.caption(f'【{metric}】有効データ {int(row["指標対象走数"])}走 ／ {row["データ量"]}')
                        for key in METRICS:
                            value = row[key]
                            shown = 'データなし' if pd.isna(value) else f'{value:.2f}' + ('%' if key.endswith('(%)') else '')
                            st.write(f'**{key}：** {shown}')
                        st.caption(f'上がり順位：{int(row["上がり対象走数"])}走 / 推定ペース：{int(row["ペース判定対象走数"])}走 / 推定脚質：{int(row["脚質判定対象走数"])}走')
            with st.expander('今回の集計に入ったレース一覧', expanded=False):
                show_cols = ['馬名', '日付', 'レース名', 'レース格', '競馬場', '距離_m', '着順_数値', '推定ペース']
                used = filtered[[c for c in show_cols if c in filtered.columns]].copy()
                if '日付' in used.columns:
                    used['日付'] = pd.to_datetime(used['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
                st.dataframe(display_horse_column(used, horses, st.session_state.auto_numbers), hide_index=True, width='stretch')
                st.download_button('集計対象レース一覧をCSVで保存', data=display_horse_column(used, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_used_races_v146.csv', mime='text/csv', width='stretch')
            if st.checkbox('開発用の詳細検証も表示する（処理が重くなります）', value=False, key='legacy_analysis_heavy_v2081'):
                st.subheader('ペース別の成績比較（Ver.15.8）')
                st.caption('レース格・直近走数・距離などの条件はランキングと同じ。ここだけは「推定ペース」の絞り込みを外し、3種類を並べて比較します。')
                st.info('netkeibaのS/M/Hが取得できたレースのみで集計します。未取得レースは「不明」として除外し、独自推定は混ぜません。')
                # Ver.15.8: ペース判定のカバー率を必ず明示する（評価不能=苦手、ではない）。
                coverage_source = pace_compare_base.copy()
                coverage_source['判定状態'] = coverage_source['推定ペース'].apply(
                    lambda x: 'netkeiba判定あり' if x in ('ハイ寄り', 'ミドル寄り', 'スロー寄り') else '未取得・不明')
                coverage_rows = []
                for h, hg in coverage_source.groupby('馬名', sort=False):
                    known_h = hg[hg['判定状態'].eq('netkeiba判定あり')]
                    total_h = len(hg)
                    n_known = len(known_h)
                    coverage_rows.append({'馬名': h, '条件一致走数': total_h,
                                          'netkeiba判定あり': n_known, '未取得・不明': total_h - n_known,
                                          '判定カバー率(%)': round(n_known / total_h * 100, 1) if total_h else 0,
                                          'ハイ': int(known_h['推定ペース'].eq('ハイ寄り').sum()),
                                          'ミドル': int(known_h['推定ペース'].eq('ミドル寄り').sum()),
                                          'スロー': int(known_h['推定ペース'].eq('スロー寄り').sum())})
                if coverage_rows:
                    coverage = pd.DataFrame(coverage_rows)
                    all_count = len(coverage_source)
                    known_count = int(coverage_source['判定状態'].eq('netkeiba判定あり').sum())
                    st.caption(f'ペース判定のカバー率：{known_count}/{all_count}走'
                               f'（{known_count / all_count * 100:.1f}%）｜除外：{all_count-known_count}走')
                    with st.expander('ペース判定の取得状況（馬別・Ver.15.8）', expanded=False):
                        st.caption('選択した直近走数・格付け・距離などに合う過去成績が母数。'
                                   '「不明」は苦手を意味しません。ハイ0走も能力の低さではありません。')
                        st.dataframe(display_horse_column(coverage, horses, st.session_state.auto_numbers),
                                     hide_index=True, width='stretch')
                        st.download_button('ペース取得状況をCSVで保存',
                                           display_horse_column(coverage, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                           file_name='keiba_pace_coverage_v158.csv', mime='text/csv', width='stretch')
                        unknown = coverage_source[coverage_source['判定状態'].eq('未取得・不明')].copy()
                        if unknown.empty:
                            st.success('現在の分析条件に該当する走はすべてnetkeiba判定を取得済みです。')
                        else:
                            columns = ['馬名','日付','レース名','レースID','競馬場','距離_m','レース格','ペース判定元']
                            unknown = unknown[[c for c in columns if c in unknown.columns]]
                            st.warning(f'未取得・不明が{len(unknown)}走あります。下のCSVで対象レースを確認できます。')
                            st.dataframe(display_horse_column(unknown, horses, st.session_state.auto_numbers),
                                         hide_index=True, width='stretch')
                            st.download_button('ペース未判定の成績をCSVで保存',
                                               display_horse_column(unknown, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                               file_name='keiba_pace_unknown_v158.csv', mime='text/csv', width='stretch')
                detail = pace_compare_base[pace_compare_base['推定ペース'].ne('不明')].copy()
                if detail.empty:
                    st.info('今回の条件ではペースを分類できる成績がありません。')
                else:
                    detail_rows = []
                    for (h, p), g in detail.groupby(['馬名', '推定ペース']):
                        finish = g['着順_数値'].dropna()
                        n = len(finish)
                        detail_rows.append({'馬名': h, '推定ペース': p, '対象走数': len(g), '着順有効走数': n,
                                            'データ量': sample_label(n),
                                            '勝率(%)': round(100 * (finish == 1).sum() / n, 1) if n else float('nan'),
                                            '複勝率(%)': round(100 * (finish <= 3).sum() / n, 1) if n else float('nan'),
                                            '平均4角': round(g['4角順位'].mean(), 2),
                                            '上がり順位対象走数': int(g['上がり順位'].notna().sum()),
                                            '平均上がり順位': round(g['上がり順位'].mean(), 2)})
                    comparison = pd.DataFrame(detail_rows)
                    order = {'ハイ寄り': 0, 'ミドル寄り': 1, 'スロー寄り': 2}
                    comparison['_順'] = comparison['推定ペース'].map(order)
                    comparison = comparison.sort_values(['馬名', '_順']).drop(columns='_順')
                    st.dataframe(display_horse_column(comparison, horses, st.session_state.auto_numbers), hide_index=True, width='stretch',
                                 column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
                    st.download_button('ペース別成績をCSVで保存', display_horse_column(comparison, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_pace_compare_v15.csv', mime='text/csv', width='stretch')
                with st.expander('ペース判定の根拠（レースごとの前後半3F）', expanded=False):
                    st.caption('差＝前半3F−後半3F。負なら前半が速く、正なら後半が速い。各レースの元データと分類を照合できます。')
                    cols = ['馬名', '日付', 'レース名', 'レース格', '競馬場', '距離_m', '前半3F', '後半3F', '前後半3F差', '暫定ペース', '推定ペース', 'ペース判定元', '着順_数値']
                    pace_audit = pace_compare_base[[c for c in dict.fromkeys(cols) if c in pace_compare_base.columns]].copy()
                    if '日付' in pace_audit.columns:
                        pace_audit['日付'] = pd.to_datetime(pace_audit['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
                    st.write(f'判定可能：{int(pace_audit["推定ペース"].ne("不明").sum())}走 ／ 全{len(pace_audit)}走')
                    st.dataframe(display_horse_column(pace_audit, horses, st.session_state.auto_numbers), hide_index=True, width='stretch',
                                 column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
                    st.download_button('ペース判定根拠をCSVで保存', display_horse_column(pace_audit, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_pace_audit_v15.csv', mime='text/csv', width='stretch')

                with st.expander('200mラップの試験取得（5レース＋1600m確認・Ver.15.9.4）', expanded=False):
                    st.caption('ラップ表の上段（通過タイム）と下段（200mラップ）を区別して読み込み、合計・累積タイムを検証します。ランキングは変更しません。')
                    sample_ids = []
                    if 'レースID' in pace_compare_base.columns:
                        sample_ids = (pace_compare_base['レースID'].fillna('').astype(str)
                                      .str.replace(r'\.0$', '', regex=True))
                        sample_ids = sample_ids[sample_ids.str.fullmatch(r'\d{12}')].drop_duplicates().head(5).tolist()
                    # 通常5レースに加えて、該当データがあれば1600mのレースを1件検証。
                    extra_1600 = ''
                    if 'レースID' in pace_compare_base.columns and '距離_m' in pace_compare_base.columns:
                        distance_1600 = pd.to_numeric(pace_compare_base['距離_m'], errors='coerce').eq(1600)
                        ids_1600 = (pace_compare_base.loc[distance_1600, 'レースID'].fillna('').astype(str)
                                    .str.replace(r'\.0$', '', regex=True))
                        candidates_1600 = ids_1600[ids_1600.str.fullmatch(r'\d{12}')]
                        extra_1600 = next((r for r in candidates_1600 if r not in sample_ids), '')
                        if extra_1600:
                            sample_ids.append(extra_1600)
                    st.caption('対象：通常の先頭5レース' + (' ＋1600mの追加1レース' if extra_1600 else '（1600mの追加対象なし）'))
                    if not sample_ids:
                        st.info('試験取得できるレースIDがありません。')
                    elif st.button('200mラップを試験取得', key='lap_table_v1594'):
                        rows = []
                        progress_lap = st.progress(0)
                        for i, rid in enumerate(sample_ids, 1):
                            try:
                                result_lap = get_lap_table_test_v1594(rid)
                                rows.append({'レースID':rid, **result_lap})
                            except Exception as e:
                                rows.append({'レースID':rid, '状態':'取得不可', '200mラップ':'',
                                             '区間数':0, 'レース距離':'', '期待区間数':'', '合計秒':'', '検証':str(e)[:200], '末尾補完':'例外', '表の一部':'',
                                             'ページURL':f'https://db.sp.netkeiba.com/race/{rid}/'})
                            progress_lap.progress(i / len(sample_ids))
                        st.session_state['lap_table_v1592_result'] = pd.DataFrame(rows)
                    if 'lap_table_v1592_result' in st.session_state:
                        lap_result = st.session_state['lap_table_v1592_result']
                        st.dataframe(lap_result, hide_index=True, width='stretch')
                        st.download_button('200mラップ試験結果をCSVで保存',
                                           lap_result.to_csv(index=False).encode('utf-8-sig'),
                                           file_name='keiba_lap_table_test_v1594.csv', mime='text/csv',
                                           width='stretch')

                with st.expander('200mラップ展開タイプ分析（Ver.16.5・全レース対応）', expanded=False):
                    st.caption('20・30レースの試験、または保存済みの全過去成績を対象にできます。全件は重複レースを1回だけ取得し、5件ごとに途中保存。結果表は現在の分析条件で絞った馬を比較します。')
                    sample = pace_compare_base.copy()
                    # Ver.16.6.1: 表示用の過去成績にも照合用レースIDを設定する。
                    # 以前は取得対象のコピーにだけ _rid16 を追加していたため、
                    # 展開タイプ分布は表示されても全馬比較表が表示されなかった。
                    if 'レースID' in sample.columns:
                        sample['_rid16'] = (sample['レースID'].fillna('').astype(str)
                                            .str.strip().str.replace(r'\.0$', '', regex=True))
                    full_sample16 = analysis_df.copy()
                    test_size = tap_choice('取得するレース数', [20, 30, '全レース'], 'lap_shape_v163_n')
                    all_mode16 = test_size == '全レース'
                    acquisition_sample16 = full_sample16 if all_mode16 else sample
                    if 'レースID' in acquisition_sample16.columns:
                        acquisition_sample16['_rid16'] = acquisition_sample16['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
                        candidates = acquisition_sample16[acquisition_sample16['_rid16'].str.fullmatch(r'\d{12}')].drop_duplicates('_rid16').copy()
                    else:
                        candidates = pd.DataFrame()
                    race_ids_16 = candidates['_rid16'].tolist() if all_mode16 and not candidates.empty else (candidates['_rid16'].head(test_size).tolist() if not candidates.empty else [])
                    # 試験モードのみ、1600mを追加確認する（全件モードは全レースそのまま）。
                    if not all_mode16 and '距離_m' in candidates.columns and race_ids_16:
                        d1600 = candidates.loc[pd.to_numeric(candidates['距離_m'], errors='coerce').eq(1600), '_rid16'].tolist()
                        if d1600 and not any(r in d1600 for r in race_ids_16):
                            race_ids_16[-1] = d1600[0]
                    race_key16 = str(st.session_state.get('auto_race_id', ''))
                    stored16 = load_lap_full_progress(race_key16) if all_mode16 else pd.DataFrame()
                    if all_mode16 and not stored16.empty:
                        stored16['レースID'] = stored16['レースID'].astype(str).str.replace(r'\.0$', '', regex=True)
                        stored16 = stored16.drop_duplicates('レースID', keep='last')
                        stored16 = stored16[stored16['レースID'].isin(race_ids_16)].copy()
                    successful16 = set(stored16.loc[stored16.get('検証', pd.Series('', index=stored16.index)).eq('OK'), 'レースID']) if not stored16.empty else set()
                    if all_mode16:
                        pending_ids16 = [rid for rid in race_ids_16 if rid not in successful16]
                        st.write(f'**対象：{len(race_ids_16)}レース ／ 検証OK：{len(successful16)} ／ 未取得・要再試行：{len(pending_ids16)}**')
                        st.caption('「未取得・要再試行」には特殊距離・海外レースなどの取得不可も含みます。再試行を繰り返しても取得できないレースは、下のCSVで原因を確認してください。')
                    else:
                        pending_ids16 = race_ids_16
                        st.caption(f'候補{len(candidates)}レース ／ 今回の試験対象{len(race_ids_16)}レース')
                    start16 = st.button('全レースの未取得分をまとめて取得・再開' if all_mode16 else '展開タイプを20〜30レース試験判定する',
                                        key='classify_lap_v165_button', disabled=not pending_ids16, width='stretch')
                    if start16:
                        # 過去の成功分は残す。失敗分は今回の試行で上書きする。
                        rows16 = stored16.to_dict('records') if all_mode16 else []
                        merged16 = {str(r['レースID']):r for r in rows16} if all_mode16 else {}
                        progress16 = st.progress(0)
                        for j, rid16 in enumerate(pending_ids16, 1):
                            try:
                                payload16 = load_lap_v16(rid16)
                                cache_hit = bool(payload16 is not None and payload16.get('検証') == 'OK')
                                if not cache_hit:
                                    payload16 = get_lap_table_test_v1594(rid16)
                                    if payload16.get('検証') == 'OK':
                                        save_lap_v16(rid16, payload16)
                                extra16 = classify_lap_shape_v16(payload16.get('200mラップ', ''),
                                                                  payload16.get('レース距離'), payload16.get('検証'))
                                title16, day16 = get_lap_race_title(rid16, acquisition_sample16)
                                row16 = {'レースID': rid16, 'レース名': title16, '開催日': day16,
                                         'レース距離': payload16.get('レース距離'),
                                         '200mラップ': payload16.get('200mラップ'),
                                         '検証': payload16.get('検証'),
                                         '取得方法': '保存済み' if cache_hit else '新規取得', **extra16}
                            except Exception as ex16:
                                row16 = {'レースID':rid16, 'レース名':'取得エラー', '開催日':'',
                                         '検証':'取得エラー', '取得方法':'失敗',
                                         '展開タイプ':'判定不可', '判定根拠':str(ex16)[:180]}
                            merged16[str(rid16)] = row16
                            progress16.progress(j / len(pending_ids16))
                            if j % 5 == 0 or j == len(pending_ids16):
                                current16 = pd.DataFrame(list(merged16.values()))
                                st.session_state['lap_shapes_v163'] = current16
                                st.session_state['lap_shapes_v163_race'] = race_key16
                                st.session_state['lap_shapes_v163_n'] = test_size
                                if all_mode16:
                                    save_lap_full_progress(race_key16, current16)
                        st.success(f'{len(pending_ids16)}レースを処理しました。取得不可は未確認として保持します。')
                    if all_mode16:
                        # 別操作による再実行後も、ディスクに保存済みの全件結果を表示。
                        shapes_current16 = load_lap_full_progress(race_key16)
                        if not shapes_current16.empty:
                            shapes_current16['レースID'] = shapes_current16['レースID'].astype(str).str.replace(r'\.0$', '', regex=True)
                            shapes_current16 = shapes_current16[shapes_current16['レースID'].isin(race_ids_16)].copy()
                            st.session_state['lap_shapes_v163'] = shapes_current16
                            st.session_state['lap_shapes_v163_race'] = race_key16
                            st.session_state['lap_shapes_v163_n'] = test_size
                    shapes16 = st.session_state.get('lap_shapes_v163')
                    if isinstance(shapes16, pd.DataFrame) and not shapes16.empty and st.session_state.get('lap_shapes_v163_race') == race_key16:
                        st.write('**レース単位の検証結果**')
                        st.dataframe(shapes16, hide_index=True, width='stretch')
                        st.download_button('展開タイプ検証結果をCSVで保存', shapes16.to_csv(index=False).encode('utf-8-sig'),
                                           file_name='keiba_lap_shape_v165.csv', mime='text/csv', width='stretch')
                        count16 = (shapes16['展開タイプ'].fillna('判定不可').value_counts()
                                   .rename_axis('展開タイプ').reset_index(name='レース数'))
                        count16['割合(%)'] = (100 * count16['レース数'] / len(shapes16)).round(1)
                        st.write('**展開タイプの分布（レース単位）**')
                        st.dataframe(count16, hide_index=True, width='stretch')
                        st.download_button('展開タイプ分布をCSVで保存', count16.to_csv(index=False).encode('utf-8-sig'),
                                           file_name='keiba_lap_shape_distribution_v163.csv', mime='text/csv', width='stretch')
                        if 'レース距離' in shapes16.columns:
                            distance16 = shapes16[['レース距離', '展開タイプ']].copy()
                            distance16['レース距離'] = pd.to_numeric(distance16['レース距離'], errors='coerce')
                            distance16['距離帯'] = pd.cut(distance16['レース距離'],
                                                     bins=[0, 1600, 1800, 2200, 2400, 10000],
                                                     labels=['1600m以下', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'])
                            bydistance16 = (distance16.groupby(['距離帯', '展開タイプ'], observed=True)
                                             .size().rename('レース数').reset_index())
                            st.write('**距離別の判定分布**')
                            st.dataframe(bydistance16, hide_index=True, width='stretch')
                            st.download_button('距離別の判定分布をCSVで保存',
                                               bydistance16.to_csv(index=False).encode('utf-8-sig'),
                                               file_name='keiba_lap_shape_distance_v163.csv', mime='text/csv', width='stretch')
                        valid16 = shapes16.loc[shapes16['検証'].eq('OK') &
                                               shapes16['展開タイプ'].ne('判定不可'),
                                               ['レースID', '展開タイプ']].drop_duplicates('レースID')
                        # 表が出ないときに理由が分かるよう、照合数も明示する。
                        if '_rid16' not in sample.columns:
                            st.warning('展開別・全馬比較：過去成績にレースID列がありません。過去成績を再取得してください。')
                        elif valid16.empty:
                            st.warning('展開別・全馬比較：検証OKのラップがありません。ラップ取得結果を確認してください。')
                        if not valid16.empty and '_rid16' in sample.columns:
                            linked16 = sample.merge(valid16, left_on='_rid16', right_on='レースID',
                                                    how='inner', suffixes=('', '_分類'))
                            if linked16.empty:
                                st.warning(f'展開別・全馬比較：レースIDの照合が0件です（現在の分析条件：{len(sample)}走／ラップ検証OK：{len(valid16)}レース）。フィルター条件またはレースIDを確認してください。')
                            if not linked16.empty:
                                st.caption(f'展開別・全馬比較の照合結果：{len(linked16)}走（重複除去前）／{linked16["馬名"].nunique()}頭')
                                # 同一馬・同一レースの重複行は1走として数える。
                                linked16 = linked16.drop_duplicates(['馬名', '_rid16']).copy()
                                for field in ['着順_数値', '上がり順位', '4角順位']:
                                    if field not in linked16:
                                        linked16[field] = float('nan')
                                    linked16[field] = pd.to_numeric(linked16[field], errors='coerce')
                                linked16['勝利'] = linked16['着順_数値'].eq(1).astype(int)
                                linked16['馬券内'] = linked16['着順_数値'].between(1, 3).astype(int)
                                overview16 = (linked16.groupby(['馬名', '展開タイプ'], dropna=False)
                                              .agg(対象走数=('馬券内', 'size'), 勝利数=('勝利', 'sum'),
                                                   馬券内数=('馬券内', 'sum'), 平均着順=('着順_数値', 'mean'),
                                                   **{'4角取得走数': ('4角順位', 'count'), '平均4角': ('4角順位', 'mean')},
                                                   上がり順位取得走数=('上がり順位', 'count'),
                                                   平均上がり順位=('上がり順位', 'mean')).reset_index())
                                overview16['勝率(%)'] = (100 * overview16['勝利数'] / overview16['対象走数']).round(1)
                                overview16['複勝率(%)'] = (100 * overview16['馬券内数'] / overview16['対象走数']).round(1)
                                for field in ['平均着順', '平均4角', '平均上がり順位']:
                                    overview16[field] = overview16[field].round(2)
                                overview16['データ量'] = overview16['対象走数'].map(
                                    lambda n: '参考（1〜2走）' if n < 3 else ('少数（3〜4走）' if n < 5 else '5走以上'))
                                # Ver.16.6：中心は「馬名 + 3展開」のコンパクトな横並び表。
                                type_order166 = ['瞬発戦寄り', 'ロングスパート戦寄り', '消耗戦寄り', '混合・判定保留']
                                type_labels166 = {'瞬発戦寄り': '瞬発戦', 'ロングスパート戦寄り': 'ロンスパ',
                                                  '消耗戦寄り': '消耗戦', '混合・判定保留': '保留'}
                                # 分母は現在の分析条件に入る「馬×レース」の重複を除いた走数。
                                candidates166 = sample.loc[
                                    sample['_rid16'].astype(str).str.fullmatch(r'\d{12}', na=False)
                                ].drop_duplicates(['馬名', '_rid16']).copy()
                                denominator166 = candidates166.groupby('馬名').size().to_dict()
                                by_horse_type166 = {(str(r['馬名']), str(r['展開タイプ'])): r
                                                    for _, r in overview16.iterrows()}
                                ordered_horses166 = list(dict.fromkeys(horses.values())) if horses else []
                                present_horses166 = set(sample['馬名'].dropna().astype(str))
                                ordered_horses166 = [h for h in ordered_horses166 if h in present_horses166]
                                ordered_horses166.extend(sorted(present_horses166.difference(ordered_horses166)))
                                wide_rows166 = []
                                for horse166 in ordered_horses166:
                                    row166 = {'馬名': horse166}
                                    covered166 = 0
                                    for typ166 in type_order166:
                                        label166 = type_labels166[typ166]
                                        hit166 = by_horse_type166.get((horse166, typ166))
                                        if hit166 is None:
                                            row166[label166] = '—'
                                            row166[label166 + '_走数'] = 0
                                        else:
                                            n166 = int(hit166['対象走数'])
                                            rate166 = float(hit166['複勝率(%)'])
                                            row166[label166] = f'{rate166:g}%（{n166}走）'
                                            row166[label166 + '_走数'] = n166
                                            covered166 += n166
                                    total166 = int(denominator166.get(horse166, 0))
                                    row166['分類済み'] = covered166
                                    row166['対象候補'] = total166
                                    row166['未確認'] = max(0, total166 - covered166)
                                    row166['確認率'] = f'{100*covered166/total166:.0f}%' if total166 else '—'
                                    wide_rows166.append(row166)
                                wide166 = pd.DataFrame(wide_rows166)
                                if not wide166.empty:
                                    wide166 = display_horse_column(wide166, horses, st.session_state.auto_numbers)
                                    st.subheader('展開タイプ別・全馬比較（Ver.16.6）')
                                    st.caption('複勝率（該当走数）で表示。— はデータなし、0%は該当レースで3着以内なし。1〜2走は参考値です。')
                                    main_cols166 = ['馬名', '瞬発戦', 'ロンスパ', '消耗戦']
                                    st.dataframe(wide166[main_cols166], hide_index=True, width='stretch',
                                                 height=min(690, 45 + 36 * len(wide166)),
                                                 column_config={'馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                                '瞬発戦': st.column_config.TextColumn('瞬発戦 複勝率（走数）', width='medium'),
                                                                'ロンスパ': st.column_config.TextColumn('ロンスパ 複勝率（走数）', width='medium'),
                                                                '消耗戦': st.column_config.TextColumn('消耗戦 複勝率（走数）', width='medium')})
                                    all_cols166 = ['馬名', '瞬発戦', 'ロンスパ', '消耗戦', '保留', '分類済み',
                                                   '対象候補', '未確認', '確認率']
                                    st.download_button('展開別・全馬比較をCSVで保存',
                                                       wide166[all_cols166].to_csv(index=False).encode('utf-8-sig'),
                                                       file_name='keiba_lap_horse_wide_v166.csv',
                                                       mime='text/csv', width='stretch')
                                    # Ver.16.7: 分類保留のレースでも数値を活用する比較表。
                                    # いずれも各馬が記録した個別ラップではなく、出走したレース全体のラップ。
                                    metric_cols167 = ['終盤加速幅_秒', '残1000m相対加速_秒', '残1000mラップ幅_秒']
                                    shape_metrics167 = shapes16.copy()
                                    shape_metrics167['レースID'] = (shape_metrics167['レースID'].fillna('').astype(str)
                                                                       .str.strip().str.replace(r'\.0$', '', regex=True))
                                    shape_metrics167 = shape_metrics167.loc[
                                        shape_metrics167['検証'].eq('OK')].drop_duplicates('レースID').copy()
                                    for metric167 in metric_cols167:
                                        if metric167 not in shape_metrics167.columns:
                                            shape_metrics167[metric167] = float('nan')
                                        shape_metrics167[metric167] = pd.to_numeric(shape_metrics167[metric167], errors='coerce')
                                    # 分類用ラップを取得できたものだけ対象にする（全件解析結果から再計算不要）。
                                    numeric_source167 = sample.merge(
                                        shape_metrics167[['レースID'] + metric_cols167],
                                        left_on='_rid16', right_on='レースID', how='inner')
                                    numeric_source167 = numeric_source167.drop_duplicates(['馬名', '_rid16']).copy()
                                    numeric_source167['着順_数値'] = pd.to_numeric(
                                        numeric_source167['着順_数値'], errors='coerce')
                                    numeric_source167['馬券内167'] = numeric_source167['着順_数値'].between(1, 3)
                                    numeric_rows167 = []
                                    for horse167 in ordered_horses166:
                                        horse_df167 = numeric_source167.loc[numeric_source167['馬名'].eq(horse167)]
                                        good_df167 = horse_df167.loc[horse_df167['馬券内167']]
                                        row167 = {'馬名': horse167,
                                                  'ラップ確認走数': len(horse_df167),
                                                  '好走走数': len(good_df167)}
                                        for source_col167, short_col167 in [
                                            ('終盤加速幅_秒', '好走時_終盤加速秒'),
                                            ('残1000m相対加速_秒', '好走時_残1000m加速秒'),
                                            ('残1000mラップ幅_秒', '好走時_残1000mラップ幅秒')]:
                                            series167 = good_df167[source_col167].dropna()
                                            row167[short_col167] = round(float(series167.mean()), 2) if not series167.empty else float('nan')
                                            row167[short_col167 + '_対象走数'] = int(len(series167))
                                        numeric_rows167.append(row167)
                                    numeric_wide167 = pd.DataFrame(numeric_rows167)
                                    if not numeric_wide167.empty:
                                        numeric_wide167 = display_horse_column(
                                            numeric_wide167, horses, st.session_state.auto_numbers)
                                        st.subheader('加速・持続ラップの比較（Ver.16.7）')
                                        st.caption('**3着以内に好走したレースの平均値**。馬自身の個別ラップ・能力値ではなく、経験したレース全体のラップ構成です。分類保留のレースも含めます。')
                                        st.caption('終盤加速：直前600mよりラスト600mが何秒/200m速いか。残1000m加速：直前基準より残1000m平均が何秒速いか。ラップ幅：残1000mの最速と最遅の差（小さいほど一定）。')
                                        display_cols167 = ['馬名', '好走時_終盤加速秒',
                                                           '好走時_残1000m加速秒', '好走時_残1000mラップ幅秒',
                                                           '好走走数', 'ラップ確認走数']
                                        display167 = numeric_wide167[display_cols167].copy()
                                        st.dataframe(display167, hide_index=True, width='stretch',
                                                     height=min(690, 45 + 36 * len(display167)),
                                                     column_config={
                                                         '馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                         '好走時_終盤加速秒': st.column_config.NumberColumn('終盤加速', format='%.2f'),
                                                         '好走時_残1000m加速秒': st.column_config.NumberColumn('残1000m加速', format='%.2f'),
                                                         '好走時_残1000mラップ幅秒': st.column_config.NumberColumn('残1000mラップ幅', format='%.2f'),
                                                         '好走走数': st.column_config.NumberColumn('好走数', format='%d走'),
                                                         'ラップ確認走数': st.column_config.NumberColumn('確認数', format='%d走')
                                                     })
                                        st.download_button('加速・持続ラップ比較をCSVで保存',
                                                           numeric_wide167.to_csv(index=False).encode('utf-8-sig'),
                                                           file_name='keiba_lap_numeric_v167.csv',
                                                           mime='text/csv', width='stretch')
                                        with st.expander('数値の根拠になったレースを確認（Ver.16.7.1）', expanded=False):
                                            reason167 = numeric_source167.loc[numeric_source167['馬券内167']].copy()
                                            if not reason167.empty:
                                                # sampleにもレースIDが存在する場合、merge後はレースID_x / レースID_y
                                                # になり得る。常に正規化済みの_rid16を照合キーに使う。
                                                info167 = shapes16[['レースID'] + [
                                                    c for c in ['レース名', '開催日', '展開タイプ'] if c in shapes16.columns
                                                ]].drop_duplicates('レースID').copy()
                                                info167['_rid16'] = (info167['レースID'].fillna('').astype(str)
                                                    .str.strip().str.replace(r'\.0$', '', regex=True))
                                                info167 = info167.drop(columns=['レースID']).drop_duplicates('_rid16')
                                                # 同名の既存列は混在させず、ラップ集計側を表示する。
                                                reason167 = reason167.drop(columns=[
                                                    c for c in ['レース名', '開催日', '展開タイプ'] if c in reason167.columns
                                                ])
                                                reason167 = reason167.merge(info167, on='_rid16', how='left')
                                                reason167 = display_horse_column(reason167, horses, st.session_state.auto_numbers)
                                                for col167 in ['レース名', '開催日', '展開タイプ']:
                                                    if col167 not in reason167.columns:
                                                        reason167[col167] = ''
                                                detail_cols167 = ['馬名', 'レース名', '開催日', '着順_数値', '展開タイプ'] + metric_cols167
                                                st.dataframe(reason167[detail_cols167], hide_index=True,
                                                             width='stretch')
                                            else:
                                                st.info('この条件で3着以内のラップ確認レースはありません。')
                                    # Ver.16.8: 3着以内と4着以下のレース全体ラップを比較。
                                    # 取消・中止・失格など着順が数値でないレースは比較から除外する。
                                    st.subheader('好走時 vs 凡走時：ラップ比較（Ver.16.8）')
                                    st.caption('3着以内＝好走、4着以下＝凡走。表示値は**各馬が出走したレース全体**のラップ平均であり、馬自身の個別ラップではありません。差＝好走時 − 凡走時。')
                                    valid168 = numeric_source167.loc[
                                        numeric_source167['着順_数値'].ge(1)].copy()
                                    valid168['結果区分168'] = valid168['着順_数値'].apply(
                                        lambda x: '好走' if x <= 3 else '凡走')
                                    pairs168 = [
                                        ('終盤加速幅_秒', '終盤加速'),
                                        ('残1000m相対加速_秒', '残1000m加速'),
                                        ('残1000mラップ幅_秒', '残1000mラップ幅'),
                                    ]
                                    rows168 = []
                                    for horse168 in ordered_horses166:
                                        subset168 = valid168.loc[valid168['馬名'].eq(horse168)]
                                        row168 = {'馬名': horse168,
                                                  '好走走数': int(subset168['結果区分168'].eq('好走').sum()),
                                                  '凡走走数': int(subset168['結果区分168'].eq('凡走').sum())}
                                        for original168, label168 in pairs168:
                                            good168 = pd.to_numeric(subset168.loc[
                                                subset168['結果区分168'].eq('好走'), original168],
                                                errors='coerce').dropna()
                                            bad168 = pd.to_numeric(subset168.loc[
                                                subset168['結果区分168'].eq('凡走'), original168],
                                                errors='coerce').dropna()
                                            row168[label168 + '_好走走数'] = int(len(good168))
                                            row168[label168 + '_凡走走数'] = int(len(bad168))
                                            row168[label168 + '_好走'] = round(float(good168.mean()), 2) if len(good168) else float('nan')
                                            row168[label168 + '_凡走'] = round(float(bad168.mean()), 2) if len(bad168) else float('nan')
                                            row168[label168 + '_差'] = (round(float(good168.mean() - bad168.mean()), 2)
                                                                        if len(good168) and len(bad168) else float('nan'))
                                        rows168.append(row168)
                                    comparison168 = pd.DataFrame(rows168)
                                    if not comparison168.empty:
                                        comparison168 = display_horse_column(
                                            comparison168, horses, st.session_state.auto_numbers)
                                        def show_sec168(x):
                                            return f'{float(x):+.2f}' if pd.notna(x) else '—'
                                        main168 = comparison168[['馬名', '終盤加速_好走', '終盤加速_凡走',
                                                                  '終盤加速_差', '好走走数', '凡走走数']].copy()
                                        for c168 in ['終盤加速_好走', '終盤加速_凡走', '終盤加速_差']:
                                            main168[c168] = main168[c168].map(show_sec168)
                                        main168 = main168.rename(columns={
                                            '終盤加速_好走': '好走時', '終盤加速_凡走': '凡走時',
                                            '終盤加速_差': '差（秒）', '好走走数': '好走', '凡走走数': '凡走'})
                                        st.dataframe(main168, hide_index=True, width='stretch',
                                                     height=min(690, 45 + 36 * len(main168)))
                                        st.caption('上の表は**終盤加速**（秒/200m）の比較。— はデータなし。差がプラスでも「その展開が得意」と断定できず、1〜2走の数字は参考値です。')
                                        with st.expander('残1000m加速・持続性・指標別の対象走数を見る', expanded=False):
                                            extra168 = ['馬名', '好走走数', '凡走走数']
                                            for _, name168 in pairs168:
                                                extra168.extend([name168 + '_好走', name168 + '_凡走',
                                                                 name168 + '_差', name168 + '_好走走数',
                                                                 name168 + '_凡走走数'])
                                            st.dataframe(comparison168[extra168], hide_index=True,
                                                         width='stretch')
                                            st.caption('残1000mラップ幅は小さいほど一定。ただし差の正負だけで良し悪しは判断できません。')
                                        st.subheader('どんな展開で好走しやすい？（Ver.17.0・自動解説）')
                                        st.caption('好走・凡走の**レース全体のラップ差**から特徴を自動要約。馬自身のラップや能力値ではありません。分類基準は試験用で、統計的な有意差を示すものではありません。')
                                        interpretations169 = []
                                        for _, row169 in comparison168.iterrows():
                                            label169, confidence169, reason169 = interpret_running_pattern(row169)
                                            interpretations169.append({'馬名': row169['馬名'],
                                                '好走する展開の特徴': label169,
                                                '判定の確かさ': confidence169,
                                                '好走走数': int(row169['好走走数']),
                                                '凡走走数': int(row169['凡走走数']),
                                                '判定理由': reason169})
                                        interpretation_df169 = pd.DataFrame(interpretations169)
                                        st.dataframe(interpretation_df169[['馬名', '好走する展開の特徴', '判定の確かさ', '好走走数', '凡走走数']],
                                                     hide_index=True, width='stretch',
                                                     height=min(690, 45 + 36 * len(interpretation_df169)))
                                        with st.expander('なぜこの判定になった？（馬ごとの説明）', expanded=False):
                                            selected169 = tap_choice('確認する馬', interpretation_df169['馬名'].tolist(), 'interpret_horse169')
                                            rec169 = interpretation_df169[interpretation_df169['馬名'].eq(selected169)].iloc[0]
                                            st.markdown(f"**{rec169['好走する展開の特徴']}**　／　{rec169['判定の確かさ']}")
                                            st.write(rec169['判定理由'])
                                            st.caption('好走時と凡走時の比較で、各指標の差が0.25秒以上（ラップ幅は0.30秒以上）の場合に特徴として表示。閾値は暫定です。')
                                        st.download_button('好走展開の自動解説をCSVで保存',
                                                           interpretation_df169.to_csv(index=False).encode('utf-8-sig'),
                                                           file_name='keiba_running_pattern_v169.csv',
                                                           mime='text/csv', width='stretch')
                                        # Ver.17: 馬自身の位置取り・上がり順位と、レース全体のラップを統合表示。
                                        st.subheader('脚質 × 末脚 × 展開：総合診断（Ver.17）')
                                        st.caption('位置・上がりは**馬自身の成績**、展開は**レース全体のラップ**。診断は暫定的な説明で、能力点や未来の勝率ではありません。')
                                        explanation_map17 = dict(zip(
                                            interpretation_df169['馬名'].astype(str),
                                            interpretation_df169['好走する展開の特徴'].astype(str)))
                                        diagnoses17 = []
                                        for h17 in ordered_horses166:
                                            hdata17 = numeric_source167.loc[numeric_source167['馬名'].eq(h17)].copy()
                                            diag17 = diagnose_horse_v17(hdata17, explanation_map17.get(h17, '不明'))
                                            diag17['馬名'] = h17
                                            diagnoses17.append(diag17)
                                        diagnosis_df17 = pd.DataFrame(diagnoses17)
                                        if not diagnosis_df17.empty:
                                            diagnosis_df17 = display_horse_column(diagnosis_df17, horses, st.session_state.auto_numbers)
                                            st.dataframe(diagnosis_df17[['馬名', '走りの総合診断', '展開', '信頼度', '好走走数']],
                                                         hide_index=True, width='stretch',
                                                         height=min(690, 45 + 36 * len(diagnosis_df17)),
                                                         column_config={'馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                                        '走りの総合診断': st.column_config.TextColumn('走りの特徴', width='medium'),
                                                                        '展開': st.column_config.TextColumn('好走展開', width='medium')})
                                            with st.expander('総合診断の根拠（馬ごとに確認）', expanded=False):
                                                chosen17 = tap_choice('診断を見る馬', diagnosis_df17['馬名'].tolist(), 'diagnosis_horse_v17')
                                                rec17 = diagnosis_df17.loc[diagnosis_df17['馬名'].eq(chosen17)].iloc[0]
                                                st.markdown(f"**{rec17['走りの総合診断']}**　／　{rec17['信頼度']}")
                                                st.write(rec17['根拠'])
                                                st.caption(f"対象{rec17['対象走数']}走・好走{rec17['好走走数']}走・4角取得{rec17['位置取得']}走・上がり順位取得{rec17['上がり取得']}走")
                                            st.download_button('総合診断をCSVで保存',
                                                               diagnosis_df17.to_csv(index=False).encode('utf-8-sig'),
                                                               file_name='keiba_horse_total_diagnosis_v17.csv',
                                                               mime='text/csv', width='stretch')
                                        st.download_button('好走・凡走ラップ比較をCSVで保存',
                                                           comparison168.to_csv(index=False).encode('utf-8-sig'),
                                                           file_name='keiba_good_bad_lap_v168.csv',
                                                           mime='text/csv', width='stretch')
                                    else:
                                        st.info('比較対象の着順・ラップがありません。')
                                    with st.expander('確認率・保留レース・データ量を見る', expanded=False):
                                        st.dataframe(wide166[['馬名','保留','分類済み','対象候補','未確認','確認率']],
                                                     hide_index=True, width='stretch')
                                        st.caption('「分類済み」はラップ検証済み（保留を含む）。「未確認」は対象候補のうち展開分類を確認できなかった走数。確認率は能力の信頼度ではありません。')
                                    with st.expander('集計に使ったレース名・着順を見る', expanded=False):
                                        race_detail166 = linked16.copy()
                                        race_detail166['レースID_照合'] = race_detail166['_rid16'].astype(str)
                                        shape_info166_cols = ['レースID'] + [c for c in ['開催日','レース名','レース距離'] if c in shapes16.columns]
                                        shape_info166 = shapes16[shape_info166_cols].drop_duplicates('レースID').copy()
                                        shape_info166['レースID'] = shape_info166['レースID'].astype(str)
                                        race_detail166 = race_detail166.merge(shape_info166, left_on='レースID_照合',
                                                                              right_on='レースID', how='left', suffixes=('', '_ラップ'))
                                        # レース名は過去成績の原文を優先し、欠損時だけラップ取得結果を使用。
                                        if 'レース名_ラップ' in race_detail166.columns:
                                            name166 = race_detail166['レース名'].fillna('').astype(str).str.strip() if 'レース名' in race_detail166 else pd.Series('',index=race_detail166.index)
                                            race_detail166['レース名'] = name166.where(name166.ne(''), race_detail166['レース名_ラップ'])
                                        if 'レース名' not in race_detail166.columns:
                                            race_detail166['レース名'] = '不明'
                                        if '日付' not in race_detail166.columns:
                                            race_detail166['日付'] = ''
                                        race_detail166['馬名_内部'] = race_detail166['馬名'].astype(str)
                                        race_detail166 = display_horse_column(race_detail166, horses, st.session_state.auto_numbers)
                                        chosen166 = st.radio('表示する展開タイプ', ['全タイプ', '瞬発戦寄り', 'ロングスパート戦寄り', '消耗戦寄り', '混合・判定保留'],
                                                                horizontal=True, key='shape_detail_type_v166')
                                        if chosen166 != '全タイプ':
                                            race_detail166 = race_detail166[race_detail166['展開タイプ'].eq(chosen166)]
                                        detailed_cols166 = ['馬名', '日付', 'レース名', '展開タイプ', '着順_数値',
                                                            '4角順位', '上がり順位', 'レースID_照合']
                                        detail_out166 = race_detail166[detailed_cols166].rename(columns={'レースID_照合':'レースID'})
                                        st.dataframe(detail_out166, hide_index=True, width='stretch',
                                                     height=min(600, 48 + 34*len(detail_out166)))
                                        st.download_button('集計対象レースの詳細をCSVで保存',
                                                           detail_out166.to_csv(index=False).encode('utf-8-sig'),
                                                           file_name='keiba_lap_horse_races_v166.csv', mime='text/csv',
                                                           width='stretch')
                                    with st.expander('指標別の詳しい成績（勝率・上がり・4角）', expanded=False):
                                        detail_metrics166 = display_horse_column(overview16, horses, st.session_state.auto_numbers)
                                        st.dataframe(detail_metrics166, hide_index=True, width='stretch')
                                        st.download_button('展開タイプ別の指標をCSVで保存',
                                                           detail_metrics166.to_csv(index=False).encode('utf-8-sig'),
                                                           file_name='keiba_lap_horse_metrics_v166.csv', mime='text/csv',
                                                           width='stretch')
                        st.warning('展開タイプは独自の暫定分類です。200mで割り切れない距離や地方・海外レースは取得不可の場合があります。少数走数では適性を断定できません。')

                with tab_quality172:
                    with st.expander('② ペース判定の精度チェック（分析条件に連動）', expanded=False):
                        st.caption('レース単位で重複を除き、判定の食い違い・距離別分布・判定できないレースを確認します。対象は現在選択中の直近走数・競馬場・馬場・コース・距離・レース格に従い、推定ペースの選択だけは無視します。')
                        audit = pace_compare_base.copy()
                        # レースIDが取得できた場合は最優先で使用。ない場合はレース日、競馬場、レース名、距離を組み合わせる。
                        rid_series = (audit['レースID'].fillna('').astype(str).str.strip()
                                      if 'レースID' in audit.columns else pd.Series('', index=audit.index))
                        valid_rid = rid_series.str.fullmatch(r'\d{12}').fillna(False)
                        day_series = pd.to_datetime(audit['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
                        name_series = audit['レース名'].fillna('').astype(str) if 'レース名' in audit else pd.Series('', index=audit.index)
                        venue_series = audit['競馬場'].fillna('').astype(str)
                        dist_series = audit['距離_m'].astype('Int64').astype(str)
                        backup = day_series + '|' + venue_series + '|' + name_series + '|' + dist_series
                        # レースを識別できない場合、別レースを無理にまとめない。
                        backup_ok = day_series.ne('') & name_series.str.strip().ne('') & venue_series.str.strip().ne('')
                        audit['_race_key'] = ['ID:' + r if ok else '補完:' + b if bk else '行:' + str(i)
                                              for i, (r, ok, b, bk) in enumerate(zip(rid_series, valid_rid, backup, backup_ok))]
                        audit['_識別方法'] = ['レースID' if ok else '日付・場所・レース名' if bk else '識別不可（行単位）'
                                              for ok, bk in zip(valid_rid, backup_ok)]
                        pace_order = ['ハイ寄り', 'ミドル寄り', 'スロー寄り', '不明']
                        dist_bins = [-float('inf'), 1600, 1800, 2200, 2400, float('inf')]
                        dist_labels = ['1600m以下', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上']
                        audit['_距離帯'] = pd.cut(audit['距離_m'], bins=dist_bins, labels=dist_labels)
                        rows_audit = []
                        for key, grp in audit.groupby('_race_key', sort=False):
                            first = grp.iloc[0]
                            classifications = sorted(set(grp['推定ペース'].fillna('不明').astype(str)))
                            f3 = pd.to_numeric(grp['前半3F'], errors='coerce').dropna()
                            l3 = pd.to_numeric(grp['後半3F'], errors='coerce').dropna()
                            d3 = pd.to_numeric(grp['前後半3F差'], errors='coerce').dropna()
                            # 同一レースで数値が異なる場合、分類が同じでもデータ不一致として検出する。
                            numeric_conflict = any(len(vals) >= 2 and vals.max() - vals.min() > 0.11 for vals in [f3, l3, d3])
                            status = ('識別不可' if first['_識別方法'] == '識別不可（行単位）'
                                      else '不一致' if len(classifications) > 1 or numeric_conflict
                                      else '判定不能' if classifications == ['不明'] else '一致')
                            rid = str(first['レースID']).strip() if 'レースID' in grp.columns else ''
                            rows_audit.append({'日付': day_series.loc[grp.index[0]], 'レース名': first['レース名'],
                                               '競馬場': first['競馬場'], '距離_m': first['距離_m'],
                                               '距離帯': str(first['_距離帯']) if pd.notna(first['_距離帯']) else '距離不明',
                                               'レースID': rid if re.fullmatch(r'\d{12}', rid) else '',
                                               '識別方法': first['_識別方法'], '登録馬数': len(grp),
                                               '前半3F': round(f3.iloc[0], 1) if not f3.empty else float('nan'),
                                               '後半3F': round(l3.iloc[0], 1) if not l3.empty else float('nan'),
                                               '前後半3F差': round(d3.iloc[0], 1) if not d3.empty else float('nan'),
                                               '推定ペース': ' / '.join(classifications), '判定元': ' / '.join(sorted(set(grp['ペース判定元'].fillna('不明').astype(str)))), '判定チェック': status,
                                               'レース結果URL': f'https://race.netkeiba.com/race/result.html?race_id={rid}' if re.fullmatch(r'\d{12}', rid) else ''})
                        race_audit = pd.DataFrame(rows_audit)
                        if race_audit.empty:
                            st.info('この分析条件では検証できるレースがありません。')
                        else:
                            total = len(race_audit)
                            conflicts = int((race_audit['判定チェック'] == '不一致').sum())
                            unknown = int((race_audit['判定チェック'] == '判定不能').sum())
                            unidentified = int((race_audit['判定チェック'] == '識別不可').sum())
                            c1, c2, c3 = st.columns(3)
                            c1.metric('異なるレース数', total)
                            c2.metric('判定不一致', conflicts)
                            c3.metric('判定不能', unknown)
                            if unidentified:
                                st.warning(f'識別に必要な情報がない成績が{unidentified}件あり、行単位で扱っています。レース数は概算です。')
                            if conflicts:
                                st.error(f'同じレースなのにペースの分類または数値が違うレースが{conflicts}件あります。下の一覧で確認してください。')
                            else:
                                st.success('識別できたレースについて、馬ごとのペース判定の食い違いは見つかりませんでした。')
                            st.markdown('**距離別のペース分布（重複レース除外）**')
                            valid_once = race_audit[race_audit['判定チェック'] == '一致'].copy()
                            counts_pace = pd.crosstab(valid_once['距離帯'], valid_once['推定ペース']).reindex(columns=pace_order[:3], fill_value=0)
                            counts_pace['判定可能レース数'] = counts_pace.sum(axis=1)
                            if unknown:
                                missing_by_dist = race_audit[race_audit['判定チェック'] == '判定不能'].groupby('距離帯').size()
                                counts_pace['判定不能'] = missing_by_dist.reindex(counts_pace.index, fill_value=0).astype(int)
                            st.dataframe(counts_pace.reset_index(), hide_index=True, width='stretch')
                            st.caption('ハイ寄りの割合が少なくても「ハイに弱い」ことは意味しません。距離ごとのサンプル数と判定不能数を見てください。')
                            st.markdown('**判定基準を変えた場合の分布（感度チェック）**')
                            st.caption('以下は前後半3F差の閾値を変えた参考比較。netkeibaのS/M/Hとは別の方法です。')
                            sensitivity = []
                            eligible = race_audit[(race_audit['判定チェック'] == '一致') &
                                                  pd.to_numeric(race_audit['前後半3F差'], errors='coerce').notna()].copy()
                            diff_values = pd.to_numeric(eligible['前後半3F差'], errors='coerce')
                            for threshold in [0.5, 1.0, 1.5]:
                                high = int((diff_values < -threshold).sum())
                                slow = int((diff_values > threshold).sum())
                                middle = int(len(diff_values) - high - slow)
                                sensitivity.append({'閾値': f'±{threshold:.1f}秒' + ('（現行）' if threshold == 1.0 else ''),
                                                    'ハイ寄り': high, 'ミドル寄り': middle, 'スロー寄り': slow,
                                                    '判定可能レース': len(diff_values)})
                            sensitivity_df = pd.DataFrame(sensitivity)
                            st.dataframe(sensitivity_df, hide_index=True, width='stretch')
                            st.download_button('閾値感度チェックをCSVで保存', sensitivity_df.to_csv(index=False).encode('utf-8-sig'),
                                               file_name='keiba_pace_threshold_v153.csv', mime='text/csv', width='stretch')
                            st.info('今の取得データにはレース中盤の区間ラップがありません。中盤まで考慮したペース判定は、別途ラップ取得の実装・検証が必要です。今回は本番のペース判定基準は変更していません。')
                            st.markdown('**各馬のペース経験数**')
                            horse_pace = pd.crosstab(audit['馬名'], audit['推定ペース']).reindex(columns=pace_order, fill_value=0)
                            horse_pace = horse_pace.reindex(sorted(audit['馬名'].dropna().unique()), fill_value=0)
                            horse_pace['判定可能走数'] = horse_pace[pace_order[:3]].sum(axis=1)
                            horse_pace['ハイ実績'] = horse_pace['ハイ寄り'].map(lambda n: 'データなし' if n == 0 else f'{n}走')
                            horse_pace.index.name = '馬名'
                            st.dataframe(display_horse_column(horse_pace.reset_index(), horses, st.session_state.auto_numbers), hide_index=True, width='stretch')
                            st.markdown('**レース単位の判定一覧**')
                            st.dataframe(race_audit, hide_index=True, width='stretch',
                                         column_config={'レース名': st.column_config.TextColumn('レース名', width='medium'),
                                                        'レース結果URL': st.column_config.LinkColumn('レース結果')})
                            st.download_button('ペース精度チェックをCSVで保存', race_audit.to_csv(index=False).encode('utf-8-sig'),
                                               file_name='keiba_pace_validation_v151.csv', mime='text/csv', width='stretch')

            st.download_button('📥 分析結果をCSVで保存', data=display_horse_column(result, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'), file_name='keiba_analysis.csv', mime='text/csv', width='stretch')




# 計測だけを追加。重い処理は増やさない。
if selected_page208 == '🧰 管理・検証':
    with st.sidebar.expander('⏱ 速度診断（開発用）', expanded=False):
        st.caption(f"初期設定: {_keiba_setup_t - _keiba_start_t:.2f}秒")
        st.caption(f"データ準備: {_keiba_data_t - _keiba_setup_t:.2f}秒")
        st.caption(f"画面描画: {time.perf_counter() - _keiba_data_t:.2f}秒")
        st.caption("上記はこの再実行1回のサーバー側処理時間。通信・ブラウザ表示時間は含みません。")
        st.markdown('**前回の一括取得（通信・待機時間を含む）**')
        fetch_profile = st.session_state.get('last_fetch_profile_v2083')
        if fetch_profile:
            st.caption(f"レースID: {fetch_profile['race_id']} / 合計: {fetch_profile['total']:.2f}秒")
            for stage, sec in fetch_profile['stages'].items():
                st.caption(f"{stage}: {sec:.2f}秒")
        else:
            st.caption('未計測。次に一括取得ボタンを押した結果を記録します。')
        st.markdown('**前回の「分析する」集計時間**')
        analysis_profile = st.session_state.get('last_analysis_profile_v2083')
        if analysis_profile:
            st.caption(f"集計: {analysis_profile['compute']:.2f}秒 / {analysis_profile['rows']}行（詳細表の描画時間は別）")
        else:
            st.caption('未計測。次に「分析する」を押すと記録します。')
        st.caption('この記録は現在のセッションに保存。サーバー再起動で消える場合があります。')
