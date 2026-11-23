"""ダート版 dirt-v1 の検証と、地方の1レース（浦和記念など）への参考順位（docs/accuracy/00 の 2026-11-23 追記どおり）。

使い方:
  python scripts/dirt_test.py --backtest <JRAダートの収集ZIPを展開> --out <出力> [--nar-work <地方の1レース取得ZIPを展開>
      --nar-venue 浦和 --nar-class G2 --nar-name 浦和記念]
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

FEATURES = ('着順', '着差', 'クラス水準', 'クラス差', '距離適性', '芝実績なし', '近走人気')
TRAIN = ('2026-09-12', '2026-10-25')
TEST = ('2026-10-31', '2026-11-23')


def params():
    return v1.Params(features=FEATURES, half_life=180, shrink_k=2.0, band=200, surface='ダ')


def popularity_rank(data, ids, cache, p):
    """単純な基準：近走人気だけで並べる（欠ける馬は最後）。"""
    out = []
    for rid in ids:
        f = cache(rid, p)
        score = pd.to_numeric(f['近走人気'], errors='coerce')
        order = f.assign(_s=score.fillna(-1)).sort_values(['_s', '馬ID'], ascending=[False, True]).index
        r = f[['レースID', '馬ID']].copy()
        r['順位'] = pd.Series(range(1, len(f) + 1), index=order).reindex(f.index)
        out.append(r)
    return pd.concat(out, ignore_index=True)


def market_rank(data, ids):
    rows = []
    for rid in ids:
        f = data.bt.field(rid)
        rows.append(pd.DataFrame({'レースID': rid, '馬ID': f['馬ID'].astype(str), '順位': pd.to_numeric(f['人気'], errors='coerce')}))
    return pd.concat(rows, ignore_index=True)


def predict_nar(work, p, beta, venue, cls, name):
    """地方の1レースの参考順位。戦績は取得した各馬のページ（対象日より前だけ）。"""
    from keiba_model_data import normalize_history
    work = Path(work)
    info_path = next(work.glob('info_*.json'))
    info = json.loads(info_path.read_text(encoding='utf-8'))
    rid = info['レースID']
    entries = pd.read_csv(work / f'entries_{rid}.csv', dtype={'馬ID': str})
    raws = [pd.read_csv(f, dtype=str) for f in (work / 'horses').glob('horse_*.csv') if f.name != 'horse_log.csv']
    hist = normalize_history(pd.concat(raws, ignore_index=True))
    race = {'レースID': rid, '開催日': info['開催日'], '競馬場': venue, '距離': int(info['距離']), 'クラス': cls, '馬場': None}
    feats = v1.build_features(entries.loc[entries['状態'].eq('出走予定')], hist, race, p)
    ranking = v1.rank_race(feats, beta, p)
    score = v1.design(feats, p) @ beta
    lo, hi = score.min(), score.max()
    ranking['dirt点'] = ((score - lo) / (hi - lo) * 100).round(1) if hi > lo else 100.0
    keep = ['馬ID', '芝有効走数', '着順', '近走人気', 'クラス水準', '距離適性', '休養日数']
    table = ranking.merge(entries[['馬ID', '馬番', '馬名', '騎手']], on='馬ID').merge(feats[keep], on='馬ID')
    table = table.rename(columns={'芝有効走数': 'ダート有効走数（直近5走中）', '着順': '着順点（頭数補正）'})
    return info, race, table.sort_values('順位')


def main(argv=None):
    import accuracy_experiment as ax
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--nar-work')
    ap.add_argument('--nar-venue', default='浦和')
    ap.add_argument('--nar-class', default='G2')
    ap.add_argument('--nar-name', default='浦和記念')
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = ax.Data(keiba_eval.Backtest(a.backtest))
    memo = {}

    def cache(rid, p):
        key = (rid, p.priors.get('top3'))
        if key not in memo:
            memo[key] = data.features(rid, p)
        return memo[key]

    dates = data.races['開催日']
    ids = [r for r in data.races.index if r in data.complete and not data.debut(r)]
    train = [r for r in ids if TRAIN[0] <= dates[r] <= TRAIN[1]]
    test = [r for r in ids if TEST[0] <= dates[r] <= TEST[1]]
    p = params()
    beta = ax.fit_v1(data, train, p, cache)
    preds = {'dirt-v1': ax.predict_v1(data, test, p, beta, cache),
             '近走人気だけ': popularity_rank(data, test, cache, p),
             '参考：当日の人気': market_rank(data, test)}
    per = pd.concat([ax.evaluate(data, test, pr).assign(モデル=m) for m, pr in preds.items()], ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    mine = per.loc[per['モデル'].eq('dirt-v1')]
    diff = (mine['上位3勝ち馬'].astype(float) - 3 / mine['頭数'].clip(lower=3)).to_numpy()
    rng = np.random.default_rng(0)
    samples = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)]
    s = summary.set_index('モデル')
    log = {'学習レース': len(train), '検証レース': len(test), '係数（学習期間）': dict(zip(p.features, map(float, beta))),
           'dirt-v1−無作為': {'差': float(diff.mean()), '下限': float(np.percentile(samples, 2.5)), '上限': float(np.percentile(samples, 97.5))},
           'dirt-v1−近走人気だけ': keiba_eval.bootstrap_diff(mine, per.loc[per['モデル'].eq('近走人気だけ')])}
    ok = log['dirt-v1−無作為']['下限'] > 0 and s.loc['dirt-v1', '上位3勝ち馬率'] >= s.loc['近走人気だけ', '上位3勝ち馬率']
    log['判定'] = '参考として出す（JRAダートで検証済み・地方は未検証）' if ok else '根拠不足'
    if a.nar_work:
        final = params()
        final_beta = ax.fit_v1(data, train + test, final, cache)  # 地方のレースには、集めたJRAダート全体で学習し直した係数を使う
        info, race, table = predict_nar(a.nar_work, final, final_beta, a.nar_venue, a.nar_class, a.nar_name)
        table.to_csv(out / f'{a.nar_name}_参考順位.csv', index=False, encoding='utf-8-sig')
        log['地方'] = {'レース': a.nar_name, '条件': race, '頭数': len(table), '係数（全期間）': dict(zip(final.features, map(float, final_beta)))}
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
