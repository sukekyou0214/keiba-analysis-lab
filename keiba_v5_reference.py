"""主画面：「参考：v5順位（検証中）」。

週末一括分析が結果前に保存した v5（影の予測）の記録をそのまま読み、表示する（再計算・通信なし）。
主予測（全馬共通）は変えない（docs/accuracy/00 の 2026-11-17 追記「v5 の参考表示」、利用者の判断）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

VALIDATION = ('過去検証（2026年夏・春の863R）で、上位3頭に勝ち馬が入ったのは56.2%（主予測の全馬共通は48.1%）。'
              'まだ実戦（結果前に保存した予想）での確認中です。勝つ確率ではありません。')


def latest_record(work, race_id, prefix='shadow-v5-'):
    """保存済みの v5 の予想のうち、最新の改訂（r番号が最大）。無ければ None。"""
    root = Path(work) / 'records' / 'predictions'
    if not root.exists():
        return None
    files = []
    for folder in sorted(root.glob(prefix + '*')):
        for path in folder.glob(f'{race_id}*.csv'):
            stem = path.stem
            rev = int(stem.split('_r')[1]) if '_r' in stem else 0
            if stem == race_id or stem.startswith(race_id + '_r'):
                files.append((folder.name, rev, path))
    if not files:
        return None
    current = _current_version()
    preferred = [f for f in files if f[0] == f'shadow-{current}'] or files  # 今の models/v5.json の版を優先
    preferred.sort(key=lambda x: (x[0], x[1]))
    return preferred[-1][2]


def _current_version():
    try:
        import keiba_model_v1 as v1
        _, _, data = v1.load_artifact(Path(__file__).resolve().parent / 'models' / 'v5.json')
        return v1.artifact_version(data)
    except (OSError, ValueError, KeyError):
        return None


def points_label(table):
    """表示用の点数。新馬戦（騎手点）は騎手の3着内率（%）、それ以外はレース内で 0〜100 に直した v5 の能力点。
    並び順は能力点と全馬共通の順位の平均で決めるので、点数の大小と順位が前後することがある。勝つ確率ではない。"""
    score = pd.to_numeric(table['点数'], errors='coerce')
    jockey = table['判定'].astype(str).str.startswith('騎手点')
    out = pd.Series('', index=table.index, dtype=object)
    out[jockey] = (score[jockey] * 100).round(1).astype(str) + '%'
    rest = score[~jockey]
    if rest.notna().any():
        lo, hi = rest.min(), rest.max()
        scaled = (rest - lo) / (hi - lo) * 100 if hi > lo else rest * 0 + 100
        out[~jockey] = scaled.round(1).astype(str)
    return out


def reference_table(work, race_id):
    path = latest_record(work, race_id)
    if path is None:
        return None
    table = pd.read_csv(path, dtype={'馬ID': str, 'レースID': str})
    table = table.loc[pd.to_numeric(table['順位'], errors='coerce').notna()].copy()
    if table.empty:
        return None
    table['順位'] = pd.to_numeric(table['順位']).astype(int)
    entries = Path(work) / 'entries' / f'{race_id}.csv'
    if entries.exists():  # 予想の保存後に枠順が出た場合も、最新の出走表の馬番を出す（予想の中身は変えない）
        latest = pd.read_csv(entries, dtype={'馬ID': str})
        numbers = latest.set_index('馬ID')['馬番']
        table['馬番'] = table['馬ID'].map(numbers).where(table['馬ID'].isin(numbers.index), table['馬番'])
    table['馬番'] = pd.to_numeric(table['馬番'], errors='coerce').astype('Int64')
    table['v5点'] = points_label(table)
    return table.sort_values('順位')[['順位', '馬番', '馬名', 'v5点', '判定']].reset_index(drop=True)


def render(work, race_id):
    import streamlit as st
    try:
        table = reference_table(work, race_id)
    except (OSError, ValueError, KeyError) as exc:
        st.caption(f'v5の参考順位を出せませんでした：{type(exc).__name__}')
        return False
    if table is None:
        return False
    st.markdown('##### 参考：v5順位（検証中）')
    st.caption('主予測（全馬共通）とは別の候補モデルです。' + VALIDATION +
               'v5点はレース内で0〜100に直した能力点（新馬戦は騎手の3着内率）。順位は能力点と全馬共通の順位の平均で決めるため、点数と順位が前後することがあります。')
    st.dataframe(table, hide_index=True, width='stretch')
    return True
