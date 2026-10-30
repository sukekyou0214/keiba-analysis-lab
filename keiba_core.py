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
    with st.popover(f'{label}：{shown}', use_container_width=True):
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


def sample_count_for_metric(group, metric):
    """指標の分母となる観測走数。値が不明な走は数えない。"""
    column_map = {
        '勝率(%)': '着順_数値', '連対率(%)': '着順_数値',
        '複勝率(%)': '着順_数値', '平均着順': '着順_数値',
        '平均4角': '4角順位', '平均上がり': '上がり3F_秒',
        '上がり最速率(%)': '上がり順位',
        '上がり3位以内率(%)': '上がり順位',
        '平均上がり順位': '上がり順位',
    }
    if metric in column_map:
        return int(group[column_map[metric]].notna().sum())
    if metric in ('前方率(%)', '差し追込率(%)'):
        return int(group['推定脚質'].ne('不明').sum())
    return len(group)


def sample_label(n):
    if n == 0:
        return 'データなし'
    if n <= 2:
        return '参考（1〜2走）'
    if n <= 4:
        return '少数（3〜4走）'
    return '5走以上'


def summarize(group):
    finish = group['着順_数値'].dropna()
    agari = group['上がり順位'].dropna()
    def rate(count, denominator):
        return round(count / denominator * 100, 2) if denominator else float('nan')
    return {'出走数': len(group), '平均着順': finish.mean(), '平均4角': group['4角順位'].mean(), '平均上がり': group['上がり3F_秒'].mean(), '勝率(%)': rate((finish == 1).sum(), len(finish)), '連対率(%)': rate((finish <= 2).sum(), len(finish)), '複勝率(%)': rate((finish <= 3).sum(), len(finish)), '上がり対象走数': len(agari), '上がり最速率(%)': rate((agari == 1).sum(), len(agari)), '上がり3位以内率(%)': rate((agari <= 3).sum(), len(agari)), '平均上がり順位': agari.mean(), '前方率(%)': rate(group['推定脚質'].isin(['先頭（4角）', '先行寄り']).sum(), group['推定脚質'].ne('不明').sum()), '差し追込率(%)': rate(group['推定脚質'].eq('後方寄り').sum(), group['推定脚質'].ne('不明').sum()), 'ペース判定対象走数': int(group['推定ペース'].ne('不明').sum()), '脚質判定対象走数': int(group['推定脚質'].ne('不明').sum())}



