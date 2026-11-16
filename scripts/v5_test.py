"""v5（近走人気を追加）の検証（docs/accuracy/00 の 2026-11-17 追記「v5」どおり）。
使い方: python scripts/v5_test.py --train-backtest DIR(2025秋) --test-backtest DIR [DIR ...] --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import keiba_eval  # noqa: E402
import keiba_model_v1 as v1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FEATURE = '近走人気'


def market(season, ids):
    rows = []
    for rid in ids:
        f = season.data.bt.field(rid)
        pop = pd.to_numeric(f['人気'], errors='coerce')
        rows.append({'レースID': rid, '馬ID': f['馬ID'].astype(str).to_numpy(), '順位': pop.to_numpy()})
    return pd.concat([pd.DataFrame({'レースID': r['レースID'], '馬ID': r['馬ID'], '順位': r['順位']}) for r in rows])


def main(argv=None):
    import v2_candidates as vc
    import v4_test as v4
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-backtest', required=True)
    ap.add_argument('--test-backtest', nargs='+', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    train = v4.JockeySeason(args.train_backtest)
    base, _, _ = v1.load_artifact(ROOT / 'models' / 'v1.json')
    p3, p5 = vc.spec(base), vc.spec(base, list(base.features) + [FEATURE])
    b3, b5 = vc.fit([(train, train.ids)], p3), vc.fit([(train, train.ids)], p5)
    log = {'v5の係数': dict(zip(p5.features, map(float, b5))), '期間': {}}
    per = []
    for path in args.test_backtest:
        test = v4.JockeySeason(path)
        name = Path(path).name
        preds = {'全馬共通': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'common')
                                     for r in test.ids]),
                 'v3': v4.predict(test, test.ids, p3, b3), 'v5': v4.predict(test, test.ids, p5, b5),
                 '参考：当日の人気': market(test, test.ids)}
        for m, p in preds.items():
            per.append(vc.evaluate(test.base, test.ids, p).assign(モデル=m, データ=name,
                                                                  レースID=lambda d, n=name: n + ':' + d['レースID'].astype(str)))
        log['期間'][name] = len(test.ids)
    per = pd.concat(per, ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby(['データ', 'モデル']).apply(keiba_eval.summarize, include_groups=False).reset_index()
    pooled = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index().assign(データ='合計')
    summary = pd.concat([summary, pooled], ignore_index=True)
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in per['モデル'].unique()}
    for a, b in (('v5', 'v3'), ('v5', '全馬共通'), ('v3', '全馬共通')):
        log[f'{a}−{b}'] = keiba_eval.bootstrap_diff(by[a], by[b])
    log['判定'] = 'v5を影の予測に加える' if log['v5−v3']['下限'] > 0 else '加えない'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary[['データ', 'モデル', 'レース数', '上位3勝ち馬', '上位3勝ち馬率', '首位勝率', '首位3着内率']].to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
