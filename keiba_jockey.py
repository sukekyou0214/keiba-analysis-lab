"""新馬戦の順位づけ（騎手点）。docs/accuracy/00 の 2026-11-16 追記「新馬戦の順位づけ」の規則どおり（固定値・調整しない）。

- 騎手点：対象日前365日の全騎乗（芝・ダート、着順が数値の騎乗）の3着内率を、騎乗20回分の縮小で全体の率に寄せた値。
- 数える騎乗は、同じ週末の出走馬の戦績にあるものだけ（本番の週末一括分析と同じ条件）。
- 出走表の騎手名は略称（例「吉田隼」「▲和田陽」）。戦績の正式名に略称の文字がすべて順に含まれる騎手が1人だけなら対応づける。
  対応できない騎手は全体の率。
"""
from __future__ import annotations

import unicodedata

import pandas as pd

WINDOW_DAYS = 365
SHRINK = 20
MARKS = '▲△☆◇★◎○●□■*＊'
VERSION = 'jockey-v1'


def normalize(name):
    text = unicodedata.normalize('NFKC', str(name or ''))
    return ''.join(ch for ch in text if ch not in MARKS and ch not in ' .．・').strip()


def _subsequence(short, full):
    it = iter(full)
    return all(ch in it for ch in short)


def match(short, full_names):
    """略称→正式名（1人に決まる時だけ）。"""
    s = normalize(short)
    if not s:
        return None
    hits = [f for f in full_names if _subsequence(s, normalize(f))]
    return hits[0] if len(hits) == 1 else None


def rides(raw_pool):
    """戦績（get_results の表、複数頭）から騎乗の表：馬ID・日付・騎手・3着内。着順が数値の騎乗だけ。"""
    if raw_pool is None or raw_pool.empty or '騎手' not in raw_pool:
        return pd.DataFrame(columns=['馬ID', '日付', '騎手', '3着内'])
    finish = raw_pool['着 順'] if '着 順' in raw_pool else raw_pool.get('着順')
    d = pd.DataFrame({'馬ID': raw_pool['馬ID'].astype(str),
                      '日付': pd.to_datetime(raw_pool['日付'].astype(str).str.replace('/', '-'), errors='coerce'),
                      '騎手': raw_pool['騎手'].astype(str).str.strip(),
                      'レース': raw_pool.get('レースID', raw_pool['日付']).astype(str),
                      '着順': pd.to_numeric(finish, errors='coerce'),
                      'コース': raw_pool.get('距離', pd.Series('', index=raw_pool.index)).astype(str).map(
                          lambda s: unicodedata.normalize('NFKC', s)[:1])})
    d = d.loc[d['コース'].isin(('芝', 'ダ'))]  # 障害は数えない
    d = d.dropna(subset=['日付', '着順']).drop_duplicates(['馬ID', 'レース'])
    d['3着内'] = d['着順'].le(3)
    return d[['馬ID', '日付', '騎手', '3着内']]


def jockey_points(ride_table, cutoff):
    """騎手→騎手点（対象日前365日）と全体の率。"""
    cutoff = pd.Timestamp(cutoff)
    r = ride_table.loc[ride_table['日付'].lt(cutoff) & ride_table['日付'].ge(cutoff - pd.Timedelta(days=WINDOW_DAYS))]
    if r.empty:
        return {}, 0.25
    overall = float(r['3着内'].mean())
    g = r.groupby('騎手')['3着内'].agg(['sum', 'count'])
    points = ((g['sum'] + SHRINK * overall) / (g['count'] + SHRINK)).to_dict()
    return points, overall


def debut_ranking(field, ride_table, cutoff, race_id):
    """新馬戦の順位：騎手点の高い順（同点は馬ID）。field: 馬ID・騎手（略称）。"""
    points, overall = jockey_points(ride_table, cutoff)
    names = list(points)
    out = pd.DataFrame({'レースID': race_id, '馬ID': field['馬ID'].astype(str).to_numpy()})
    full = [match(j, names) for j in field['騎手']]
    out['点数'] = [points.get(f, overall) if f else overall for f in full]
    out['判定'] = ['騎手点（新馬戦）' if f else '騎手点（騎手名を対応できず全体の率）' for f in full]
    order = out.sort_values(['点数', '馬ID'], ascending=[False, True]).index
    out['順位'] = pd.Series(range(1, len(out) + 1), index=order).reindex(out.index).astype('Int64')
    return out
