"""過去レースでの予測評価（事前登録 docs/accuracy/00 に従う）。

- 予想は「レース前に分かっていた情報」だけで作る：各馬の戦績は開催日より前の行だけ。
  対象レースの結果・上がり・ペース・人気・オッズは予測に使わない。
- 評価はレース単位。取消・除外の馬は実出走の分母から外すが、上位候補を繰り上げない。
- 主指標：上位3頭での勝ち馬捕捉率（全頭保留は失敗）。副指標は metrics() の各列。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PERIODS = (('inner', '2026-09-12', '2026-10-12'), ('outer', '2026-10-17', '2026-11-01'),
           ('used', '2026-11-07', '2026-11-15'))
VENUE_CODES = {'01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京', '06': '中山', '07': '中京',
               '08': '京都', '09': '阪神', '10': '小倉'}


def period_of(date):
    for name, start, end in PERIODS:
        if start <= str(date)[:10] <= end:
            return name
    return 'other'


def target_class(info):
    from keiba_model_data import classify_class
    icons = info.get('グレード表示', '')
    grade = next((g for n, g in (('1', 'G1'), ('2', 'G2'), ('3', 'G3')) if f'Icon_GradeType{n} ' in icons + ' '), '')
    return classify_class(info.get('レース名', ''), info.get('条件欄2', ''), grade) or '不明'


class Backtest:
    """収集フォルダ（keiba_backtest の出力）を読み込む。"""

    def __init__(self, work):
        from keiba_model_data import normalize_history
        work = Path(work)
        infos = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((work / 'races').glob('*.json'))]
        self.races = pd.DataFrame(infos)
        self.races['競馬場'] = self.races['レースID'].str[4:6].map(VENUE_CODES)
        self.races['期間'] = self.races['開催日'].map(period_of)
        self.races['クラス'] = [target_class(x) for x in infos]
        self.runners = pd.concat([pd.read_csv(work / 'races' / f"{x['レースID']}.csv", dtype=str) for x in infos],
                                 ignore_index=True)
        from keiba_model_data import finish_status
        parsed = self.runners['着順原文'].map(finish_status)
        self.runners['状態'] = parsed.map(lambda x: x[0])
        self.runners['着順'] = pd.to_numeric(parsed.map(lambda x: x[1]), errors='coerce')
        self.runners['実出走'] = self.runners['状態'].isin(('完走', '中止', '失格', '降着'))
        log = pd.read_csv(work / 'horses' / 'horse_log.csv', dtype=str) if (work / 'horses' / 'horse_log.csv').exists() else pd.DataFrame(columns=['馬ID', '状態'])
        self.history_status = dict(zip(log['馬ID'], log['状態']))
        raws = []
        for path in sorted((work / 'horses').glob('horse_[0-9]*.csv')):
            raws.append(pd.read_csv(path, dtype=str))
        self.raw = pd.concat(raws, ignore_index=True) if raws else pd.DataFrame()
        self.history = normalize_history(self.raw)
        pace = work / 'target_pace.csv'
        self.pace = pd.read_csv(pace, dtype=str) if pace.exists() else pd.DataFrame(columns=['レースID', '公表ペース', '状態'])

    def field(self, race_id):
        return self.runners.loc[self.runners['レースID'].eq(race_id)]

    def complete_races(self):
        """全頭の戦績状態が確定しているレース（取得不可の馬がいるレースは完全な検証に数えない）。"""
        ok = []
        for rid, field in self.runners.groupby('レースID'):
            states = field['馬ID'].map(self.history_status)
            if states.isin(('成功', '戦績なし確認')).all():
                ok.append(rid)
        return ok


def context_of(info):
    return {'race_id': info['レースID'], 'race_date': info['開催日'], 'venue': info['競馬場'], 'surface': '芝',
            'distance': int(info['距離']), 'name': info.get('レース名', ''), 'source': 'backtest',
            'grade': info['クラス'] if info['クラス'] in ('G1', 'G2', 'G3') else ('条件戦' if info['クラス'] in ('新馬', '未勝利', '1勝', '2勝', '3勝') else None)}


def frozen_rank(bt, info, mode, pace='M', past_paces=None):
    """保存済みの旧方式（mode='old'）・全馬共通方式（mode='common'）を、当時の戦績で計算する。
    過去走の公表ペースは取得していないので、想定ペース項目は空（両方式とも同じ扱い）。"""
    from keiba_core import convert_auto, prepare
    from keiba_main_ranking import main_ranking
    field = bt.field(info['レースID'])
    horses = dict(zip(field['馬ID'], field['馬名']))
    raw = bt.raw.loc[bt.raw['馬ID'].isin(horses)] if not bt.raw.empty else bt.raw
    context = context_of(info)
    if raw.empty:
        return pd.DataFrame({'レースID': info['レースID'], '馬ID': list(horses), '順位': pd.NA, '点数': np.nan, '判定': '戦績なし'})
    data = prepare(convert_auto(raw))
    if past_paces is not None and len(past_paces):
        from keiba_core import apply_netkeiba_pace
        ok = past_paces.loc[past_paces['状態'].eq('成功')]
        data = apply_netkeiba_pace(data, dict(zip(ok['レースID'], ok['netkeibaペース'])))
    data = data.loc[pd.to_datetime(data['日付'], errors='coerce').lt(pd.Timestamp(info['開催日']))]
    if data.empty:
        return pd.DataFrame({'レースID': info['レースID'], '馬ID': list(horses), '順位': pd.NA, '点数': np.nan, '判定': '戦績なし'})
    if mode == 'old':
        r = main_ranking(data, horses, context, None, pace, graded_only=info['クラス'] in ('G1', 'G2', 'G3'))
    else:
        r = main_ranking(data, horses, context, None, pace, mode='common')
    return pd.DataFrame({'レースID': info['レースID'], '馬ID': r['馬ID'].astype(str), '順位': r['参考順位'],
                         '点数': r['総合参考点'], '判定': r['判定']})


def race_metrics(pred, field):
    """1レース分の指標。pred: 馬ID・順位（NA可）。field: 結果（状態・着順・実出走）。"""
    m = field.merge(pred[['馬ID', '順位']], on='馬ID', how='left')
    starters = m.loc[m['実出走']]
    winners = set(starters.loc[starters['着順'].eq(1), '馬ID'])
    top3_actual = set(starters.loc[starters['着順'].le(3), '馬ID'])
    rank = pd.to_numeric(m['順位'], errors='coerce')
    in_top = lambda k: set(m.loc[rank.le(k), '馬ID'])
    firsts = m.loc[rank.eq(1) & m['実出走']]
    ranked_starters = starters.loc[pd.to_numeric(starters['順位'], errors='coerce').notna() & starters['着順'].notna()]
    out = {'勝ち馬あり': bool(winners), '頭数': len(starters), '順位付与数': int(pd.to_numeric(starters['順位'], errors='coerce').notna().sum()),
           '上位3勝ち馬': bool(winners & in_top(3)), '上位5勝ち馬': bool(winners & in_top(5)),
           '上位3の3着内数': len(top3_actual & in_top(3)), '実3着内数': len(top3_actual),
           '首位数': len(firsts), '首位勝利': int(firsts['着順'].eq(1).sum()), '首位3着内': int(firsts['着順'].le(3).sum())}
    if len(ranked_starters) >= 2 and len(starters) > 1:
        pr = pd.to_numeric(ranked_starters['順位']).rank(method='average')
        ac = ranked_starters['着順'].rank(method='average')
        out['順位誤差'] = float((pr - ac).abs().mean() / (len(starters) - 1))
        out['相関'] = float(pr.corr(ac)) if pr.nunique() > 1 and ac.nunique() > 1 else np.nan
    else:
        out['順位誤差'], out['相関'] = np.nan, np.nan
    return out


def summarize(rows):
    """レース単位の指標をまとめる。rows: race_metrics の DataFrame（レースID・期間つき）。"""
    r = rows.loc[rows['勝ち馬あり']]
    return pd.Series({'レース数': len(r), '上位3勝ち馬': int(r['上位3勝ち馬'].sum()), '上位3勝ち馬率': r['上位3勝ち馬'].mean(),
                      '上位5勝ち馬率': r['上位5勝ち馬'].mean(), '順位付与率': r['順位付与数'].sum() / max(r['頭数'].sum(), 1),
                      '首位勝率': r['首位勝利'].sum() / max(r['首位数'].sum(), 1),
                      '首位3着内率': r['首位3着内'].sum() / max(r['首位数'].sum(), 1),
                      '上位3の3着内捕捉率': r['上位3の3着内数'].sum() / max(r['実3着内数'].sum(), 1),
                      '順位誤差': r['順位誤差'].mean(), '相関': r['相関'].mean(),
                      '無作為の上位3期待': (3 / r['頭数'].clip(lower=3)).mean()})


def bootstrap_diff(a, b, column='上位3勝ち馬', n=2000, seed=0):
    """同じレースで比べた差（a−b）のレース単位ブートストラップ95%区間。"""
    joined = a[['レースID', column]].merge(b[['レースID', column]], on='レースID', suffixes=('_a', '_b'))
    diff = joined[column + '_a'].astype(float) - joined[column + '_b'].astype(float)
    rng = np.random.default_rng(seed)
    samples = [diff.iloc[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)] if len(diff) else [np.nan]
    return {'レース数': len(diff), '差': float(diff.mean()) if len(diff) else np.nan,
            '下限': float(np.percentile(samples, 2.5)), '上限': float(np.percentile(samples, 97.5))}
