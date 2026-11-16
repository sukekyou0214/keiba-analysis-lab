"""主画面：新馬戦の「参考：騎手順位（新馬戦のみ）」。

新馬戦は戦績がなく、主予測（全馬共通）では順位が出ない。騎手点（keiba_jockey、規則 jockey-v1・固定）で並べた順位を、
検証結果を添えて参考として出す（docs/accuracy/00 の 2026-11-16 追記「新馬戦の表示」、利用者の判断）。
騎手点は同じ週末の出走馬すべての戦績から数えるので、週末一括分析で取得したレースだけ表示できる（通信なし）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

VALIDATION = ('過去検証（まだ使っていなかった2026年夏の新馬戦92R）で、上位3頭に勝ち馬が入ったのは47.8%'
              '（無作為に3頭選んだ場合29.8%）。騎手の過去1年の3着内率だけで並べたもので、馬の能力や勝つ確率ではありません。')


def is_debut(name):
    return '新馬' in str(name or '')


@lru_cache(maxsize=4)
def _pool(work, stamp):
    """この週末の出走馬すべての戦績（騎乗の表）。stamp は更新検知用。"""
    import keiba_jockey as kj
    from keiba_weekend_batch import HistoryCache
    work = Path(work)
    cache = HistoryCache(work / 'horses')
    ids = set()
    for path in (work / 'entries').glob('*.csv'):
        ids |= set(pd.read_csv(path, dtype={'馬ID': str})['馬ID'])
    raws = [cache.load(h) for h in sorted(ids)]
    raws = [r for r in raws if not r.empty]
    return kj.rides(pd.concat(raws, ignore_index=True) if raws else pd.DataFrame())


def reference_table(work, race_id):
    """新馬戦の騎手順位の表（順位・馬番・馬名・騎手・騎手点・判定）。新馬戦でなければ None。"""
    import keiba_jockey as kj
    work = Path(work)
    info = json.loads((work / 'entries' / f'{race_id}.json').read_text(encoding='utf-8'))
    if not is_debut(info.get('レース名')):
        return None
    entries = pd.read_csv(work / 'entries' / f'{race_id}.csv', dtype={'馬ID': str, 'レースID': str})
    active = entries.loc[entries['状態'].eq('出走予定')].copy()
    if '騎手' not in active:
        active['騎手'] = ''
    horses_dir = work / 'horses'
    stamp = max((p.stat().st_mtime for p in horses_dir.glob('*.csv')), default=0) if horses_dir.exists() else 0
    rides = _pool(str(work), stamp)
    ranking = kj.debut_ranking(active[['馬ID', '騎手']].fillna(''), rides, info['開催日'], race_id)
    table = ranking.merge(active[['馬ID', '馬番', '馬名', '騎手']], on='馬ID', how='left').sort_values('順位')
    table['騎手点（3着内率）'] = (table['点数'] * 100).round(1).astype(str) + '%'
    return table[['順位', '馬番', '馬名', '騎手', '騎手点（3着内率）', '判定']].reset_index(drop=True)


def render(work, race_id):
    """選んだレースが新馬戦なら参考の騎手順位を出す。出した場合 True。"""
    import streamlit as st
    try:
        table = reference_table(work, race_id)
    except (OSError, ValueError, KeyError) as exc:
        st.caption(f'新馬戦の参考順位を出せませんでした：{type(exc).__name__}')
        return False
    if table is None:
        return False
    st.markdown('##### 参考：騎手順位（新馬戦のみ）')
    st.caption('新馬戦は戦績がないため、主予測の順位は出ません。' + VALIDATION)
    st.dataframe(table, hide_index=True, width='stretch')
    return True
