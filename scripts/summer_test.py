"""2026年夏（未使用期間）での確認（docs/accuracy/00 の 2026-11-16 追記「未使用期間での確認」どおり）。

確認1：2025年秋だけで学習した v1・v2（C2）を 2026年夏に当て、旧方式・全馬共通と比べる。
確認2：新馬戦の騎手点（jockey-v1、固定）の 捕捉−無作為。
使い方: python scripts/summer_test.py --train-backtest DIR(2025秋) --test-backtest DIR(2026夏) --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import keiba_eval  # noqa: E402
import keiba_model_v1 as v1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    import debut_jockey
    import replicate_v1 as rep
    import v2_candidates as vc
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-backtest', required=True)
    ap.add_argument('--test-backtest', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    train, test = vc.Season(args.train_backtest), vc.Season(args.test_backtest)
    base, _, artifact = v1.load_artifact(ROOT / 'models' / 'v1.json')
    log = {'学習レース（2025秋）': len(train.ids), '評価レース（2026夏）': len(test.ids), 'v1の仕様': v1.artifact_version(artifact)}
    params = vc.spec(base)
    beta = vc.fit([(train, train.ids)], params)
    preds = {'旧方式': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'old')
                                for r in test.ids]),
             '全馬共通': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'common')
                                 for r in test.ids]),
             '基準v1': vc.predict(test, test.ids, params, beta, 'logit'),
             'v2': vc.predict(test, test.ids, params, beta, 'rank_average')}
    per = pd.concat([vc.evaluate(test, test.ids, p).assign(モデル=m) for m, p in preds.items()], ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in preds}
    for a in ('v2', '基準v1'):
        for b in ('全馬共通', '旧方式'):
            log[f'{a}−{b}'] = keiba_eval.bootstrap_diff(by[a], by[b])
    s = summary.set_index('モデル')
    layers = rep.layer_check(per.loc[per['モデル'].isin(('v2', '全馬共通'))], a='v2', b='全馬共通')
    layers.to_csv(out / 'layers.csv', index=False, encoding='utf-8-sig')
    checks = {
        '1 v2が旧方式・全馬共通の両方を上回る': bool(s.loc['v2', '上位3勝ち馬率'] > s.loc['全馬共通', '上位3勝ち馬率']
                                       and s.loc['v2', '上位3勝ち馬率'] > s.loc['旧方式', '上位3勝ち馬率']),
        '2 v2−全馬共通の95%区間の下限が0以上': bool(log['v2−全馬共通']['下限'] >= 0),
        '3 順位付与率が全馬共通から5pt以上下がらない': bool(s.loc['v2', '順位付与率'] - s.loc['全馬共通', '順位付与率'] >= -0.05),
        '4 10R以上の層で10pt以上の悪化がない': bool(not layers['悪化'].any()),
        '5 評価期間が60R以上': bool(len(test.ids) >= 60),
    }
    log['確認1 採用条件'] = checks
    log['確認1 判定'] = ('条件を満たす（主画面の切り替えは前向き評価と合わせて判断）' if all(checks.values())
                     else '条件を満たさない（切り替えない）')
    t, rate = debut_jockey.evaluate(args.test_backtest)
    t.to_csv(out / 'debut_races.csv', index=False, encoding='utf-8-sig')
    diff = (t['上位3勝ち馬'].astype(float) - t['無作為期待']).to_numpy()
    rng = np.random.default_rng(0)
    samples = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)] if len(diff) else [np.nan]
    log['確認2 新馬戦'] = {'レース数': len(t), '上位3勝ち馬': int(t['上位3勝ち馬'].sum()) if len(t) else 0,
                       '上位3勝ち馬率': float(t['上位3勝ち馬'].mean()) if len(t) else None,
                       '無作為期待': float(t['無作為期待'].mean()) if len(t) else None, '騎手名の対応率': rate,
                       '差': float(diff.mean()) if len(diff) else None,
                       '下限': float(np.percentile(samples, 2.5)), '上限': float(np.percentile(samples, 97.5))}
    log['確認2 判定'] = 'v3を影の予測に加える' if log['確認2 新馬戦']['下限'] > 0 else '加えない'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
