"""v2 候補の選択（docs/accuracy/00 の 2026-11-16 追記「v2 候補の作り方」どおり）。

2025年秋の全レースで学習し、2026年秋の全レースで主指標を比べる。選んだ候補は両方で学習し直して保存する。
使い方:
  python scripts/v2_candidates.py --train-backtest DIR(2025) --valid-backtest DIR(2026) --out DIR [--save models/v2.json]
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
COMMON = '全馬共通点数'


class Season:
    def __init__(self, path):
        import accuracy_experiment as ax
        self.data = ax.Data(keiba_eval.Backtest(path))
        self.ids = ax.race_ids(self.data, None)
        self.memo, self.common_memo = {}, {}

    def common(self, rid):
        if rid not in self.common_memo:
            info = self.data.races.loc[rid].to_dict() | {'レースID': rid}
            self.common_memo[rid] = keiba_eval.frozen_rank(self.data.bt, info, 'common')
        return self.common_memo[rid]

    def features(self, rid, params):
        key = (rid, params.window, params.half_life, params.shrink_k, params.band, params.priors.get('top3'))
        if key not in self.memo:
            f = self.data.features(rid, params)
            c = self.common(rid).set_index('馬ID')
            f[COMMON] = f['馬ID'].map(pd.to_numeric(c['点数'], errors='coerce'))
            f['全馬共通順位'] = f['馬ID'].map(pd.to_numeric(c['順位'], errors='coerce'))
            self.memo[key] = f
        return self.memo[key]

    def winners(self, frame):
        fields = self.data.bt.runners.set_index(['レースID', '馬ID'])
        finish = fields.reindex(list(zip(frame['レースID'], frame['馬ID'])))['着順'].to_numpy()
        return (finish == 1).astype(float)


def base_rate(seasons):
    import accuracy_experiment as ax
    rates = [(ax.base_rate(s.data, ids), len(ids)) for s, ids in seasons]
    return float(sum(r * n for r, n in rates) / sum(n for _, n in rates))


def fit(seasons, params):
    """seasons: [(Season, race_ids)]。基準率・標準化・係数をこれらのレースだけで作る。"""
    params.priors = {'top3': base_rate(seasons)}
    frames, winners, keys = [], [], []
    for s, ids in seasons:
        for rid in ids:
            if s.data.debut(rid):
                continue
            f = s.features(rid, params)
            frames.append(f)
            winners.append(s.winners(f))
            keys += [f'{id(s)}:{rid}'] * len(f)
    train = pd.concat(frames, ignore_index=True)
    v1.fit_priors(train, params)
    params.priors['top3'] = base_rate(seasons)
    x = v1.design(train, params)
    return v1.fit_logit(x, pd.factorize(pd.Series(keys))[0], np.concatenate(winners), l2=params.l2)


def predict(season, ids, params, beta, kind='logit'):
    out = []
    for rid in ids:
        f = season.features(rid, params)
        r = v1.rank_race(f, beta, params, debut_race=season.data.debut(rid))
        if not season.data.debut(rid):
            r['_s'] = v1.design(f, params) @ beta
            if kind == 'rank_average':
                r = rank_average(r, f['全馬共通順位'].to_numpy())
        out.append(r)
    return pd.concat(out, ignore_index=True)


def rank_average(ranking, common_rank):
    """全馬共通（ペース項目なし）と v1 の順位の平均。共通の順位がない馬は v1 の順位で補う。同点は v1 の点数。"""
    r = ranking.copy()
    v1_rank = pd.to_numeric(r['順位'], errors='coerce').to_numpy(float)
    c = np.where(np.isnan(common_rank.astype(float)), v1_rank, common_rank.astype(float))
    r['平均順位'] = (v1_rank + c) / 2
    order = r.sort_values(['平均順位', '点数', '馬ID'], ascending=[True, False, True]).index
    r['順位'] = pd.Series(range(1, len(r) + 1), index=order).reindex(r.index).astype('Int64')
    return r


def evaluate(season, ids, pred):
    import accuracy_experiment as ax
    return ax.evaluate(season.data, ids, pred)


def logloss(season, pred):
    import accuracy_experiment as ax
    return ax.winner_logloss(season.data, pred)


def forward_select(season, base):
    """C4：2025年秋の前半で学習、後半で勝ち馬の対数損失を見て、v1 の候補を同じ順で選び直す。"""
    import accuracy_experiment as ax
    dates = season.data.races['開催日']
    train = [r for r in season.ids if dates[r] <= '2025-10-13']
    val = [r for r in season.ids if '2025-10-18' <= dates[r] <= '2025-11-30']

    def score(features):
        p = v1.Params(features=tuple(features), window=base.window, half_life=base.half_life, shrink_k=base.shrink_k,
                      band=base.band, l2=base.l2)
        beta = fit([(season, train)], p)
        return logloss(season, predict(season, val, p, beta))

    chosen, steps = ['着順'], []
    best = score(chosen)
    for cand in ax.CANDIDATES:
        ll = score(chosen + [cand])
        keep = ll < best - 0.001
        steps.append({'候補': cand, '対数損失': ll, '採用': keep})
        if keep:
            chosen.append(cand)
            best = ll
    return chosen, pd.DataFrame(steps)


def spec(base, features=None):
    return v1.Params(features=tuple(features or base.features), window=base.window, half_life=base.half_life,
                     shrink_k=base.shrink_k, band=base.band, l2=base.l2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-backtest', required=True)
    ap.add_argument('--valid-backtest', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--save', default=None, help='選んだ候補を両方で学習し直して保存する先（例 models/v2.json）')
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    s25, s26 = Season(args.train_backtest), Season(args.valid_backtest)
    base, _, artifact = v1.load_artifact(ROOT / 'models' / 'v1.json')
    log = {'学習レース': len(s25.ids), '検証レース': len(s26.ids), 'v1の仕様': v1.artifact_version(artifact)}

    c4_features, steps = forward_select(s25, base)
    steps.to_csv(out / 'c4_forward_selection.csv', index=False, encoding='utf-8-sig')
    candidates = [('C1', spec(base), 'logit'), ('C2', spec(base), 'rank_average'),
                  ('C3', spec(base, list(base.features) + [COMMON]), 'logit'), ('C4', spec(base, c4_features), 'logit')]
    rows, per = [], []
    for name, params, kind in candidates:
        beta = fit([(s25, s25.ids)], params)
        pred = predict(s26, s26.ids, params, beta, kind)
        ev = evaluate(s26, s26.ids, pred).assign(候補=name)
        per.append(ev)
        rows.append({'候補': name, '種類': kind, '特徴量': '・'.join(params.features), **keiba_eval.summarize(ev).to_dict(),
                     '勝ち馬の対数損失': logloss(s26, pred) if kind == 'logit' else np.nan})
    for name, mode in (('全馬共通', 'common'), ('旧方式', 'old')):
        pred = pd.concat([keiba_eval.frozen_rank(s26.data.bt, s26.data.races.loc[r].to_dict() | {'レースID': r}, mode)
                          for r in s26.ids])
        ev = evaluate(s26, s26.ids, pred).assign(候補=name)
        per.append(ev)
        rows.append({'候補': name, '種類': '参考', **keiba_eval.summarize(ev).to_dict()})
    table = pd.DataFrame(rows)
    table.to_csv(out / 'candidates.csv', index=False, encoding='utf-8-sig')
    per = pd.concat(per, ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    ranked = table.loc[table['候補'].str.match(r'C\d')].copy()
    ranked['番号'] = ranked['候補'].str[1:].astype(int)
    ranked = ranked.sort_values(['上位3勝ち馬', '番号'], ascending=[False, True])
    top = ranked.iloc[0]
    c1 = float(table.loc[table['候補'].eq('C1'), '上位3勝ち馬'].iloc[0])
    chosen = top['候補'] if float(top['上位3勝ち馬']) > c1 else None
    log['選択'] = chosen or 'なし（C1 を上回る候補がない）'
    by = {n: per.loc[per['候補'].eq(n)] for n in per['候補'].unique()}
    for n in ('C2', 'C3', 'C4'):
        log[f'{n}−C1'] = keiba_eval.bootstrap_diff(by[n], by['C1'])
        log[f'{n}−全馬共通'] = keiba_eval.bootstrap_diff(by[n], by['全馬共通'])
    if chosen and args.save:
        name, params, kind = next(c for c in candidates if c[0] == chosen)
        params = spec(params)
        beta = fit([(s25, s25.ids), (s26, s26.ids)], params)
        saved = v1.save_artifact(args.save, params, beta, {'model_version': 'v2', '候補': chosen, '種類': kind,
                                                           '学習期間': '2025-09-13〜11-30・2026-09-12〜11-08',
                                                           '採用状態': '影の予測（主予測ではない）'})
        log['保存'] = {'path': str(args.save), '版': v1.artifact_version(saved)}
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(table.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
