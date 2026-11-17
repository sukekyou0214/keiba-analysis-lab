"""v6（出走表の基本項目を追加）の検証（docs/accuracy/00 の 2026-11-17 追記「v6」どおり）。
使い方: python scripts/v6_test.py --train-backtest DIR(2025秋) --test-backtest DIR [DIR ...] --out DIR
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


def entry_season(path):
    import v4_test as v4

    class EntrySeason(v4.JockeySeason):
        def features(self, rid, params):
            f = super().features(rid, params)
            if '斤量差' not in f:
                field = self.data.bt.field(rid)
                field = field.loc[field['実出走']] if '実出走' in field else field
                extra = v1.entry_features(field).set_index('馬ID')
                for c in v1.ENTRY_FEATURES:
                    f[c] = f['馬ID'].astype(str).map(extra[c])
            return f
    return EntrySeason(path)


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
    train = entry_season(args.train_backtest)
    base, _, _ = v1.load_artifact(ROOT / 'models' / 'v1.json')
    p5 = vc.spec(base, list(base.features) + ['近走人気'])
    p6 = vc.spec(base, list(base.features) + ['近走人気', *v1.ENTRY_FEATURES])
    b5, b6 = vc.fit([(train, train.ids)], p5), vc.fit([(train, train.ids)], p6)
    log = {'v6の係数': dict(zip(p6.features, map(float, b6))), '期間': {}}
    per = []
    for path in args.test_backtest:
        test = entry_season(path)
        name = Path(path).name
        preds = {'v5': v4.predict(test, test.ids, p5, b5), 'v6': v4.predict(test, test.ids, p6, b6)}
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
    by = {m: per.loc[per['モデル'].eq(m)] for m in ('v5', 'v6')}
    log['v6−v5'] = keiba_eval.bootstrap_diff(by['v6'], by['v5'])
    log['判定'] = 'v6を影の予測に加える' if log['v6−v5']['下限'] > 0 else '加えない'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary[['データ', 'モデル', 'レース数', '上位3勝ち馬', '上位3勝ち馬率', '首位勝率', '首位3着内率']].to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
