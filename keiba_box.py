"""ボックス向きレースの選び方（docs/accuracy/00 の 2026-11-24 追記どおり）。

各日、v5 の上位5頭で1〜3着が決まりやすいレースを並べる。予想時点で分かるもの（頭数・v5 の順位と点数）だけを使う。
B2 の値はレースを並べるためだけに使い、確率として表示しない。
"""
from __future__ import annotations

from itertools import permutations

import numpy as np
import pandas as pd

RULES = ('B1', 'B2', 'B3')


def harville_top5(scores):
    """v5 のロジット点（順位順）から、上位5頭が1〜3着を独占する見込み（並べるための値）。"""
    s = np.asarray(scores, float)
    if len(s) < 3:
        return np.nan
    w = np.exp(s - s.max())
    p = w / w.sum()
    top = range(min(5, len(p)))
    total = 0.0
    for a, b, c in permutations(top, 3):
        total += p[a] * p[b] / (1 - p[a]) * p[c] / (1 - p[a] - p[b])
    return float(total)


def race_values(ranking):
    """ranking：1レース分の 順位・点数（v5）。頭数・B2・B3 の値を返す。"""
    r = ranking.assign(_r=pd.to_numeric(ranking['順位'], errors='coerce'),
                       _s=pd.to_numeric(ranking['点数'], errors='coerce')).dropna(subset=['_r', '_s']).sort_values('_r')
    s = r['_s'].to_numpy(float)
    gap = float(s[4] - s[5]) if len(s) >= 6 else (np.inf if len(s) == 5 else np.nan)
    return {'頭数': len(r), 'B2': harville_top5(s), 'B3': gap}


def order(races, rule):
    """races：日付・レースID・頭数・B2・B3 の表。rule の順に並べた表（各日の中での順位つき）。"""
    if rule == 'B1':
        keys, asc = ['頭数', 'B2', 'レースID'], [True, False, True]
    elif rule == 'B2':
        keys, asc = ['B2', 'レースID'], [False, True]
    else:
        keys, asc = ['B3', 'レースID'], [False, True]
    out = races.sort_values(keys, ascending=asc, na_position='last').copy()
    out['ボックス向き順'] = out.groupby('日付').cumcount() + 1
    return out


def top3(races, rule):
    return order(races, rule).loc[lambda d: d['ボックス向き順'] <= 3]
