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
