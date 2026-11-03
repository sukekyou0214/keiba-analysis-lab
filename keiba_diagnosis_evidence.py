"""Source-labelled pace evidence for the original main diagnosis."""
import pandas as pd


def summarize_source_pace(data):
    """Report observed top-three counts, never infer pace or winning chances."""
    if data.empty or not {'netkeibaペース', '着順_数値'}.issubset(data):
        return '未取得'
    frame = data.copy()
    if {'馬ID', 'レースID'}.issubset(frame):
        # Only identified races are deduplicated; unknown IDs are not one race.
        identified = frame['レースID'].fillna('').astype(str).str.fullmatch(r'\d{12}')
        frame = pd.concat([frame.loc[identified].drop_duplicates(['馬ID', 'レースID']),
                           frame.loc[~identified]])
    finish = pd.to_numeric(frame['着順_数値'], errors='coerce')
    parts = []
    for label, short in [('スロー寄り', 'S'), ('ミドル寄り', 'M'), ('ハイ寄り', 'H')]:
        selected = frame['netkeibaペース'].eq(label) & finish.ge(1)
        total = int(selected.sum())
        good = int((selected & finish.le(3)).sum())
        parts.append(f'{short} {good}/{total}' if total else f'{short} —')
    return '・'.join(parts) if frame['netkeibaペース'].isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り']).any() else '未取得'


def summarize_lap_results(data, shapes):
    """Join verified race shapes to horse results; no individual lap inference."""
    if data.empty or shapes.empty or not {'レースID', '着順_数値'}.issubset(data) or not {'レースID', '検証', '展開タイプ'}.issubset(shapes):
        return '未取得'
    results = data.copy()
    results['レースID'] = results['レースID'].astype(str)
    results = results.drop_duplicates('レースID')
    verified = shapes.loc[shapes['検証'].eq('OK')].copy()
    verified['レースID'] = verified['レースID'].astype(str)
    linked = results.merge(verified[['レースID', '展開タイプ']].drop_duplicates('レースID', keep='last'), on='レースID', how='inner')
    finish = pd.to_numeric(linked['着順_数値'], errors='coerce')
    linked = linked.loc[finish.ge(1)].copy()
    if linked.empty:
        return '未取得'
    parts = []
    for label, short in [('瞬発戦寄り','瞬'), ('ロングスパート戦寄り','持'), ('消耗戦寄り','消'), ('混合・判定保留','混')]:
        chosen = linked['展開タイプ'].eq(label)
        total = int(chosen.sum())
        good = int((chosen & pd.to_numeric(linked['着順_数値'], errors='coerce').le(3)).sum())
        parts.append(f'{short} {good}/{total}' if total else f'{short} —')
    return '・'.join(parts)
