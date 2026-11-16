"""v4（新馬戦以外にも騎手点）の検証（docs/accuracy/00 の 2026-11-16 追記「v4」どおり）。

v3・v4 とも 2025年秋だけで学習し、2026年春（未使用期間）で比べる。新馬戦は両方とも騎手点の順位。
v4 は v3 のロジットに特徴量「騎手点」を1つだけ加えたもの。
使い方: python scripts/v4_test.py --train-backtest DIR(2025秋) --test-backtest DIR(2026春) --out DIR
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
import keiba_jockey as kj  # noqa: E402
import keiba_model_v1 as v1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JOCKEY = '騎手点'


def weekend_key(date):
    d = pd.Timestamp(date)
    return (d - pd.Timedelta(days=(d.weekday() - 5) % 7)).strftime('%Y-%m-%d')


class JockeySeason:
    """v2_candidates.Season に騎手点の列を足す（同じ週末の出走馬の戦績から数える）。"""

    def __init__(self, path):
        import v2_candidates as vc
        self.base = vc.Season(path)
        self.data, self.ids = self.base.data, self.base.ids
        races = self.data.races
        self.week = races['開催日'].map(weekend_key)
        self.rides = {}

    def _rides(self, key):
        if key not in self.rides:
            group = self.week.index[self.week.eq(key)]
            horses = set(self.data.bt.runners.loc[self.data.bt.runners['レースID'].isin(group), '馬ID'].astype(str))
            self.rides[key] = kj.rides(self.data.bt.raw.loc[self.data.bt.raw['馬ID'].astype(str).isin(horses)])
        return self.rides[key]

    def jockey_ranking(self, rid):
        field = self.data.bt.field(rid)
        return kj.debut_ranking(field[['馬ID', '騎手']], self._rides(self.week[rid]), self.data.races.loc[rid, '開催日'], rid)

    def features(self, rid, params):
        f = self.base.features(rid, params)
        if JOCKEY not in f:
            points = self.jockey_ranking(rid).set_index('馬ID')['点数']
            f[JOCKEY] = f['馬ID'].astype(str).map(points)
        return f

    def winners(self, frame):
        return self.base.winners(frame)


def predict(season, ids, params, beta):
    """新馬戦は騎手点、それ以外はロジット＋全馬共通との順位平均（v3 と同じ組み立て）。"""
    out = []
    for rid in ids:
        if season.data.debut(rid):
            out.append(season.jockey_ranking(rid))
            continue
        f = season.features(rid, params)
        r = v1.rank_race(f, beta, params)
        out.append(v1.rank_average(r, f['全馬共通順位'].to_numpy()))
    return pd.concat(out, ignore_index=True)


def main(argv=None):
    import replicate_v1 as rep
    import v2_candidates as vc
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-backtest', required=True)
    ap.add_argument('--test-backtest', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    train, test = JockeySeason(args.train_backtest), JockeySeason(args.test_backtest)
    base, _, artifact = v1.load_artifact(ROOT / 'models' / 'v1.json')
    p3 = vc.spec(base)
    p4 = vc.spec(base, list(base.features) + [JOCKEY])
    b3 = vc.fit([(train, train.ids)], p3)
    b4 = vc.fit([(train, train.ids)], p4)
    preds = {'旧方式': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'old')
                                for r in test.ids]),
             '全馬共通': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'common')
                                 for r in test.ids]),
             'v3': predict(test, test.ids, p3, b3), 'v4': predict(test, test.ids, p4, b4)}
    per = pd.concat([vc.evaluate(test.base, test.ids, p).assign(モデル=m) for m, p in preds.items()], ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in preds}
    log = {'学習レース（2025秋）': len(train.ids), '評価レース（2026春）': len(test.ids),
           'v4の係数': dict(zip(p4.features, map(float, b4))), 'v3の係数': dict(zip(p3.features, map(float, b3)))}
    for a, b in (('v4', 'v3'), ('v4', '全馬共通'), ('v3', '全馬共通'), ('v4', '旧方式'), ('v3', '旧方式')):
        log[f'{a}−{b}'] = keiba_eval.bootstrap_diff(by[a], by[b])
    nd = per.loc[per['クラス'].ne('新馬')]
    log['新馬戦以外の上位3勝ち馬'] = nd.groupby('モデル')['上位3勝ち馬'].agg(['sum', 'count']).to_dict('index')
    layers = rep.layer_check(per.loc[per['モデル'].isin(('v4', 'v3'))], a='v4', b='v3')
    layers.to_csv(out / 'layers.csv', index=False, encoding='utf-8-sig')
    log['判定'] = 'v4を影の予測に加える' if log['v4−v3']['下限'] > 0 else '加えない'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
