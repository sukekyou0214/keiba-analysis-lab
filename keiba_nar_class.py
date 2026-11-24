"""地方の格付けの点（docs/accuracy/00 の 2026-11-25 追記どおり）。

Jpn1＝10 … C3＝1.5。「B2B3」のように2つにまたがる場合は平均。「C2三四」の組分けは無視して C2。
2歳・3歳の限定戦・未格付・読めないものは NaN（学習期間の中央値で補う）。
"""
from __future__ import annotations

import re

import numpy as np

POINTS = {'JPN1': 10.0, 'JPN2': 9.0, 'JPN3': 8.0, 'S1': 7.0, 'S2': 6.0, 'S3': 5.5, 'A1': 5.0, 'A2': 4.5,
          'B1': 4.0, 'B2': 3.5, 'B3': 3.0, 'C1': 2.5, 'C2': 2.0, 'C3': 1.5}
LOCAL_GRADED = 5.5  # 地方重賞（S1〜S3 の区別が読めない場合）
_TRANS = str.maketrans('ＡＢＣＳＪＰＮｐｎ０１２３ⅠⅡⅢ', 'ABCSJPNPN0123123')


def nar_class_level(text):
    """格付けの文字列（レース名・一覧表記）→ 点。読めなければ NaN。"""
    if text is None:
        return np.nan
    s = str(text).translate(_TRANS).upper()
    if not s.strip() or s == 'NAN':
        return np.nan
    jpn = re.search(r'JPN\s*(III|II|I|[123])', s)
    if jpn:
        return POINTS['JPN' + {'I': '1', 'II': '2', 'III': '3'}.get(jpn[1], jpn[1])]
    grade = re.search(r'(?<![A-Z0-9])S([123])(?![0-9])', s)
    if grade:
        return POINTS['S' + grade[1]]
    # A1〜C3（前後に英数字が続かないもの）。B2B3・C1C2 のような連続も拾う
    hits = re.findall(r'([ABC])([123])', s)
    hits = [h for h in hits if h[0] + h[1] in POINTS]
    if hits:
        return float(np.mean([POINTS[a + b] for a, b in hits]))
    if '重賞' in s:
        return LOCAL_GRADED
    return np.nan
