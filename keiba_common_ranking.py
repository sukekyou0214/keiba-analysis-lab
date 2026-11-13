"""全馬共通条件の参考点：全馬を同じ窓（芝の直近10走・全クラス）と同じ式で比べる。

従来の参考点（rank_entries）は、項目の充足率60%未満や有効成績3走未満で順位を保留する。
重賞の比較ではG1〜G3だけに絞るため保留が多く、全馬を並べられない。ここでは:

- 窓：対象日より前の芝の直近10走（クラスは問わない）。全馬同じ。
- 着順点（30%）：窓内の着順を頭数で補正した相対着順（0〜100）。
- 格（25%）：窓内各走のクラスを同じ物差しで点数化（G1=100、G2=86、G3=71、OP/L=57、3勝=43、
  2勝=29、1勝=14、未勝利・新馬=0）。クラスはレース格（G1〜G3）とレース名の (L)/(OP)/3勝/2勝/
  1勝/未勝利/新馬 から読み、読めない走（地方・海外など）は格から外す（値で埋めない）。
- 3着内率（同場・距離帯15%、距離帯10%、同場5%、想定ペース15%）：距離帯は対象距離±400m。
- 走数が少ない値は、出走馬全体の値へ縮小する（k=3）：
  値 = (その馬の合計 + 3×出走馬全体の平均) / (その馬の走数 + 3)。
  少数の好走・凡走で極端な値にならないための処理で、記録の補完ではない。
  全馬に同じ式を使う。経験ゼロの項目は出走馬全体の平均と同じになる。
- 保留：芝の有効成績が1走もない馬だけ。走数5未満は「走数少」と表示。

重みは仮設定で、勝率・期待値・的中率として検証した指標ではない（過去レースでの検証は未実施）。
"""
from __future__ import annotations

import re

import pandas as pd

WINDOW = 10
SHRINK = 3
BAND = 400
WEIGHTS = (('着順点', .30), ('格', .25), ('同場距離帯', .15), ('距離帯', .10), ('同場', .05), ('想定ペース', .15))
LEVELS = {'G1': 7, 'G2': 6, 'G3': 5, 'OP/L': 4, '3勝': 3, '2勝': 2, '1勝': 1, '未勝利': 0, '新馬': 0}
RATE_ITEMS = ('同場距離帯', '距離帯', '同場', '想定ペース')
DESCRIPTION = (f'全馬共通：対象日前の芝の直近{WINDOW}走（全クラス）を同じ式で比較。着順点30%・格25%（G1=100…未勝利=0）・'
               f'同場距離帯（±{BAND}m）15%・距離帯10%・同場5%・想定ペース15%。走数が少ない値は出走馬全体の値へ縮小'
               f'（k={SHRINK}）。保留は芝の有効成績がない馬のみ。重みは仮設定で過去検証は未実施、勝率・期待値ではありません。')


def run_level(name, grade):
    """レース格・レース名の明示表記からクラスを読む。読めなければ None（推測しない）。"""
    if grade in ('G1', 'G2', 'G3'):
        return grade
    name = str(name or '')
    for pattern, level in ((r'\((?:L|OP)\)|オープン', 'OP/L'), (r'3勝|1600万', '3勝'), (r'2勝|1000万', '2勝'),
                           (r'1勝|500万', '1勝'), (r'未勝利', '未勝利'), (r'新馬', '新馬')):
        if re.search(pattern, name):
            return level
    return None


def horse_counts(history, cutoff, venue, distance, pace):
    """1頭分の窓（芝の直近10走）から、合計と走数だけを数える（縮小前）。"""
    h = history.loc[history['日付'].lt(pd.Timestamp(cutoff)) & history['コース'].eq('芝')]
    h = h.sort_values('日付', ascending=False).head(WINDOW)
    finish, field = h['着順_数値'], h['頭数']
    valid = finish.ge(1) & finish.le(field) & finish.mod(1).eq(0) & field.gt(1)
    w = h.loc[valid]
    out = {'窓内走数': len(h), '有効走数': len(w)}
    if w.empty:
        return out
    score = (1 - (w['着順_数値'] - 1) / (w['頭数'] - 1)) * 100
    out['着順点_sum'], out['着順点_n'] = float(score.sum()), len(w)
    levels = [run_level(n, g) for n, g in zip(w.get('レース名', pd.Series('', index=w.index)),
                                              w.get('レース格', pd.Series('', index=w.index)))]
    known = [LEVELS[x] / 7 * 100 for x in levels if x]
    out['格_sum'], out['格_n'] = float(sum(known)), len(known)
    out['重賞走数'] = sum(x in ('G1', 'G2', 'G3') for x in levels)
    top3 = w['着順_数値'].le(3)
    out['窓内3着内率'] = float(top3.mean() * 100)
    near = (w['距離_m'] - distance).abs().le(BAND)
    same_venue = w['競馬場'].eq(venue)
    masks = {'同場距離帯': same_venue & near, '距離帯': near, '同場': same_venue, '想定ペース': w['ペース区分'].eq(pace)}
    for label, mask in masks.items():
        mask = mask.fillna(False)
        out[label + '_sum'], out[label + '_n'] = float(top3[mask].sum() * 100), int(mask.sum())
    return out


def shrink(row, label, prior):
    n = row.get(label + '_n', 0) or 0
    return (row.get(label + '_sum', 0.0) + SHRINK * prior) / (n + SHRINK)


def common_rank(entries, raw, config, pace='M'):
    from keiba_v22 import history_frame
    h = history_frame(raw)
    if not h.empty and {'馬ID', 'レースID'}.issubset(h):
        identified = h['レースID'].fillna('').astype(str).str.fullmatch(r'\d{12}')
        h = pd.concat([h.loc[identified].drop_duplicates(['馬ID', 'レースID']), h.loc[~identified]], ignore_index=True)
    rows = []
    for _, e in entries.iterrows():
        mine = h.loc[h['馬ID'].astype(str).eq(str(e['馬ID']))] if not h.empty else h
        counts = horse_counts(mine, config.race_date, config.venue, int(config.distance), pace) if not mine.empty else {}
        rows.append({**e.to_dict(), **counts, '_has_history': not mine.empty})
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    ranked = d.get('着順点_n', pd.Series(0, index=d.index)).fillna(0).gt(0)
    # 出走馬全体の値（各項目の総和÷総走数）。縮小の目標に使う。
    priors = {}
    for label in ('着順点', '格') + RATE_ITEMS:
        total_n = d.loc[ranked, label + '_n'].sum() if label + '_n' in d else 0
        priors[label] = d.loc[ranked, label + '_sum'].sum() / total_n if total_n else 0.0
    for idx in d.index[ranked]:
        row = d.loc[idx].to_dict()
        values = {label: shrink(row, label, priors[label]) for label in ('着順点', '格') + RATE_ITEMS}
        for label, value in values.items():
            d.at[idx, label] = round(value, 1)
            d.at[idx, label + '走数'] = int(row.get(label + '_n', 0) or 0)
        d.at[idx, '総合参考点'] = sum(values[label] * weight for label, weight in WEIGHTS)
    if '総合参考点' not in d:
        d['総合参考点'] = float('nan')
    for idx in d.index:
        valid = int(d.at[idx, '有効走数']) if pd.notna(d.at[idx, '有効走数'] if '有効走数' in d else None) else 0
        if not d.at[idx, '_has_history']:
            d.at[idx, '判定'] = '戦績未取得'
        elif not ranked[idx]:
            d.at[idx, '判定'] = '芝の有効成績なし・保留'
        else:
            d.at[idx, '判定'] = '共通条件' if valid >= 5 else f'共通条件・走数少（{valid}走）'
        tried = [label for label in RATE_ITEMS if (d.at[idx, label + '_n'] if label + '_n' in d else 0) or 0]
        d.at[idx, '評価項目充足率'] = round((2 + len(tried)) / len(WEIGHTS) * 100) if ranked[idx] else float('nan')
        d.at[idx, '近5走有効数'] = valid
        d.at[idx, '対象場距離走数'] = d.at[idx, '同場距離帯_n'] if ranked[idx] else float('nan')
        d.at[idx, '妙味'] = '想定人気未取得・保留'
    d = d.drop(columns=[c for c in d.columns if c.endswith('_sum') or c.endswith('_n') or c == '_has_history'])
    d['総合参考点'] = pd.to_numeric(d['総合参考点'], errors='coerce').round(1)
    d = d.sort_values('総合参考点', ascending=False, na_position='last').reset_index(drop=True)
    d['参考順位'] = d['総合参考点'].rank(method='min', ascending=False).astype('Int64')
    return d
