"""v1 の追加検証（docs/accuracy/00 の 2026-11-15 追記どおり）。

v1 の仕様（特徴量・半減期・k・距離帯）は models/v1.json から読み、選び直さない。
係数と基準率は学習期間（既定 2025-train）だけで作り直し、評価期間（既定 2025-eval）で旧方式・全馬共通と比べる。
2026年 outer の既存結果（accuracy_experiment.py の per_race_metrics.csv）と合わせた差の区間も出す。

使い方:
  python scripts/replicate_v1.py --backtest DIR --out DIR --previous DIR(2026年の実験出力)
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
MODELS = ('旧方式', '全馬共通', '基準v1')


def layer_check(per_race, a='基準v1', b='全馬共通', min_races=10, limit=-0.10):
    """クラス・距離帯・開催場の層ごとに a−b の主指標の差。10R以上の層で limit 以下なら悪化。"""
    d = per_race.copy()
    d['距離帯'] = pd.cut(d['距離'].astype(float), [0, 1400, 1800, 2200, 4000],
                      labels=['〜1400', '1500〜1800', '1900〜2200', '2300〜']).astype(str)
    rows = []
    for kind in ('クラス', '距離帯', '競馬場'):
        pivot = d.pivot_table(index=['レースID', kind], columns='モデル', values='上位3勝ち馬', aggfunc='first').reset_index()
        for layer, g in pivot.groupby(kind):
            diff = float(g[a].mean() - g[b].mean())
            rows.append({'層の種類': kind, '層': layer, 'レース数': len(g), a: float(g[a].mean()), b: float(g[b].mean()),
                         '差': diff, '判定対象': len(g) >= min_races, '悪化': len(g) >= min_races and diff <= limit + 1e-12})
    return pd.DataFrame(rows)


def decide(summary_eval, rank_rates, pooled_ci, layers, n_eval):
    s = summary_eval.set_index('モデル')['上位3勝ち馬率']
    checks = {
        '1 評価期間で旧方式・全馬共通の両方を上回る': bool(s['基準v1'] > s['旧方式'] and s['基準v1'] > s['全馬共通']),
        '2 合わせた差の95%区間の下限が0以上': bool(pooled_ci['下限'] >= 0),
        '3 順位付与率が全馬共通から5pt以上下がらない（両期間）': bool(all(v >= -0.05 for v in rank_rates.values())),
        '4 10R以上の層で10pt以上の悪化がない（合わせたレース）': bool(not layers['悪化'].any()),
        '5 評価期間が60R以上': bool(n_eval >= 60),
    }
    if not checks['5 評価期間が60R以上']:
        verdict = '判定保留（評価期間のレース数不足）'
    elif all(checks.values()):
        verdict = '採用条件を満たす'
    else:
        verdict = '採用しない（影の予測を継続）'
    return checks, verdict


def main(argv=None):
    import accuracy_experiment as ax
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--previous', help='2026年の実験出力（per_race_metrics.csv）。合わせた区間に使う')
    ap.add_argument('--train', nargs=2, default=('2025-09-13', '2025-10-13'))
    ap.add_argument('--eval', nargs=2, default=('2025-10-18', '2025-11-30'))
    ap.add_argument('--artifact', default=str(ROOT / 'models' / 'v1.json'))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    keiba_eval.PERIODS = (('inner', *args.train), ('outer', *args.eval))
    data = ax.Data(keiba_eval.Backtest(args.backtest))
    memo = {}

    def cache(rid, params):
        key = (rid, params.window, params.half_life, params.shrink_k, params.band, params.priors.get('top3'))  # 本実験と同じ
        if key not in memo:
            memo[key] = data.features(rid, params)
        return memo[key]

    train, evaluation = ax.race_ids(data, 'inner'), ax.race_ids(data, 'outer')
    frozen_params, frozen_beta, artifact = v1.load_artifact(args.artifact)
    spec = v1.Params(features=frozen_params.features, window=frozen_params.window, half_life=frozen_params.half_life,
                     shrink_k=frozen_params.shrink_k, band=frozen_params.band, l2=frozen_params.l2)
    beta = ax.fit_v1(data, train, spec, cache)
    log = {'学習期間': list(args.train), '評価期間': list(args.eval), '学習レース': len(train), '評価レース': len(evaluation),
           '全レース': len(data.races), '結果のそろったレース': len(data.complete), '仕様の出典': v1.artifact_version(artifact),
           '学習し直した係数': dict(zip(spec.features, map(float, beta))), '基準率': spec.priors.get('top3')}
    results = []
    for period, ids in (('train', train), ('eval', evaluation)):
        preds = {'旧方式': pd.concat([keiba_eval.frozen_rank(data.bt, data.races.loc[r].to_dict() | {'レースID': r}, 'old') for r in ids]),
                 '全馬共通': pd.concat([keiba_eval.frozen_rank(data.bt, data.races.loc[r].to_dict() | {'レースID': r}, 'common') for r in ids]),
                 '基準v1': ax.predict_v1(data, ids, spec, beta, cache)}
        if period == 'eval':
            preds['参考：v1凍結（2026学習）'] = ax.predict_v1(data, ids, frozen_params, frozen_beta, cache)
        for model, pred in preds.items():
            results.append(ax.evaluate(data, ids, pred).assign(モデル=model, 区分=period))
    per_race = pd.concat(results, ignore_index=True)
    per_race.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per_race.groupby(['区分', 'モデル']).apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    ev = per_race.loc[per_race['区分'].eq('eval')]
    by = {m: ev.loc[ev['モデル'].eq(m)] for m in MODELS}
    log['評価期間: 基準v1−旧方式'] = keiba_eval.bootstrap_diff(by['基準v1'], by['旧方式'])
    log['評価期間: 基準v1−全馬共通'] = keiba_eval.bootstrap_diff(by['基準v1'], by['全馬共通'])
    summary_eval = summary.loc[summary['区分'].eq('eval')]
    rates = summary_eval.set_index('モデル')['順位付与率']
    rank_rates = {'評価期間': float(rates['基準v1'] - rates['全馬共通'])}
    pooled = ev.loc[ev['モデル'].isin(MODELS)].assign(期間名='2025-eval')
    if args.previous:
        prev = pd.read_csv(Path(args.previous) / 'per_race_metrics.csv', dtype={'レースID': str})
        prev = prev.loc[prev['期間'].eq('outer') & prev['モデル'].isin(MODELS)].assign(期間名='2026-outer')
        prev_sum = pd.read_csv(Path(args.previous) / 'summary.csv')
        prev_sum = prev_sum.loc[prev_sum['期間'].eq('outer')].set_index('モデル')['順位付与率']
        rank_rates['2026 outer'] = float(prev_sum['基準v1'] - prev_sum['全馬共通'])
        pooled = pd.concat([pooled, prev], ignore_index=True)
    pooled = pooled.assign(レースID=pooled['期間名'] + ':' + pooled['レースID'].astype(str))
    pb = {m: pooled.loc[pooled['モデル'].eq(m)] for m in MODELS}
    log['合わせたレース: 基準v1−全馬共通'] = keiba_eval.bootstrap_diff(pb['基準v1'], pb['全馬共通'])
    log['合わせたレース: 基準v1−旧方式'] = keiba_eval.bootstrap_diff(pb['基準v1'], pb['旧方式'])
    log['順位付与率の差（v1−全馬共通）'] = rank_rates
    layers = layer_check(pooled)
    layers.to_csv(out / 'layers_pooled.csv', index=False, encoding='utf-8-sig')
    checks, verdict = decide(summary_eval, rank_rates, log['合わせたレース: 基準v1−全馬共通'], layers, len(evaluation))
    log['採用条件'] = checks
    log['判定'] = verdict
    # 展開予測（しきい値固定）
    import keiba_pace_v1 as pv
    params = pv.load_params()
    published = data.bt.pace.loc[data.bt.pace['状態'].eq('成功')].set_index('レースID')['公表ペース'].map(pv.LABELS)
    prow = []
    for rid in data.races.index:
        if rid not in published.index or data.debut(rid):
            continue
        res, _ = pv.predict_pace(data.bt.field(rid), data.by_horse, data.races.loc[rid, '開催日'], params)
        prow.append({'レースID': rid, '公表': published[rid], '独自判定': res['独自判定'], '先行圧力': res['先行圧力']})
    pace = pd.DataFrame(prow)
    if len(pace):
        pace.to_csv(out / 'pace_predictions.csv', index=False, encoding='utf-8-sig')
        judged = pace.dropna(subset=['独自判定'])
        log['展開'] = {'レース数': len(pace), '判定数': len(judged),
                     '一致率': float((judged['独自判定'] == judged['公表']).mean()) if len(judged) else None,
                     '常にMの一致率': float((pace['公表'] == 'M').mean()),
                     '公表の分布': pace['公表'].value_counts().to_dict(),
                     '予測の分布': judged['独自判定'].value_counts().to_dict()}
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
