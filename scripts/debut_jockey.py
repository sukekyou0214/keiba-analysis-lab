"""新馬戦の騎手点の検証（docs/accuracy/00 の 2026-11-16 追記「新馬戦の順位づけ」どおり）。

使い方: python scripts/debut_jockey.py --backtest DIR [DIR ...] --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import keiba_eval  # noqa: E402
import keiba_jockey as kj  # noqa: E402


def weekend_key(date):
    d = pd.Timestamp(date)
    return (d - pd.Timedelta(days=(d.weekday() - 5) % 7)).strftime('%Y-%m-%d')


def evaluate(path):
    bt = keiba_eval.Backtest(path)
    races = bt.races.set_index('レースID')
    complete = set(bt.complete_races())
    races = races.loc[[r for r in races.index if r in complete]]
    races['週末'] = races['開催日'].map(weekend_key)
    rows, matched = [], []
    for key, group in races.groupby('週末'):
        horses = set(bt.runners.loc[bt.runners['レースID'].isin(group.index), '馬ID'].astype(str))
        pool = bt.raw.loc[bt.raw['馬ID'].astype(str).isin(horses)]
        table = kj.rides(pool)
        for rid, info in group.loc[group['クラス'].eq('新馬')].iterrows():
            field = bt.field(rid)
            pred = kj.debut_ranking(field[['馬ID', '騎手']], table, info['開催日'], rid)
            m = keiba_eval.race_metrics(pred, field)
            rows.append({'レースID': rid, '開催日': info['開催日'], **m, '無作為期待': 3 / max(m['頭数'], 3)})
            matched.append(float(pred['判定'].eq('騎手点（新馬戦）').mean()))
    out = pd.DataFrame(rows)
    return out, float(np.mean(matched)) if matched else np.nan


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', nargs='+', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables, log = [], {'規則': kj.VERSION, '期間': {}}
    for path in args.backtest:
        t, rate = evaluate(path)
        name = Path(path).name
        t['データ'] = name
        tables.append(t)
        log['期間'][name] = {'新馬戦': len(t), '上位3勝ち馬': int(t['上位3勝ち馬'].sum()),
                           '上位3勝ち馬率': float(t['上位3勝ち馬'].mean()), '無作為期待': float(t['無作為期待'].mean()),
                           '首位勝率': float(t['首位勝利'].sum() / max(t['首位数'].sum(), 1)),
                           '騎手名の対応率': rate}
    allr = pd.concat(tables, ignore_index=True)
    allr.to_csv(out / 'debut_races.csv', index=False, encoding='utf-8-sig')
    diff = (allr['上位3勝ち馬'].astype(float) - allr['無作為期待']).to_numpy()
    rng = np.random.default_rng(0)
    samples = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)]
    log['合わせた差（捕捉−無作為）'] = {'レース数': len(diff), '差': float(diff.mean()),
                                  '下限': float(np.percentile(samples, 2.5)), '上限': float(np.percentile(samples, 97.5))}
    log['判定'] = 'v3に加える' if log['合わせた差（捕捉−無作為）']['下限'] > 0 else '加えない'
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
