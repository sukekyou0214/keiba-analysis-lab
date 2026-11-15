"""予測精度の比較実験（docs/accuracy/00 の事前登録どおり）。

使い方:
  python scripts/accuracy_experiment.py --backtest DIR --out DIR [--dev1415 DIR]

出力: 比較表CSV、レース単位の指標、選択の記録、実行条件（コード版・データ件数）をJSON/CSVで保存する。
同じ入力・同じコードなら同じ結果になる（乱数はブートストラップのみで種を固定）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import keiba_model_v1 as v1  # noqa: E402
from keiba_eval import Backtest, bootstrap_diff, frozen_rank, race_metrics, summarize  # noqa: E402

INNER_TRAIN_END = '2026-10-04'
CANDIDATES = ('着差', 'クラス水準', 'クラス差', '距離適性', '同場適性', '馬場適性', '休養日数', '芝休養日数', '直近ダート数', '芝実績なし')


class Data:
    def __init__(self, bt):
        self.bt = bt
        self.by_horse = {k: g for k, g in bt.history.groupby('馬ID')} if not bt.history.empty else {}
        self.races = bt.races.set_index('レースID')
        self.complete = set(bt.complete_races())

    def features(self, race_id, params):
        info = self.races.loc[race_id].to_dict() | {'レースID': race_id}
        field = self.bt.field(race_id)
        rows = []
        for _, horse in field.iterrows():
            hist = self.by_horse.get(horse['馬ID'], pd.DataFrame(columns=self.bt.history.columns))
            rows.append({'レースID': race_id, '馬ID': horse['馬ID'], **v1.horse_rows(hist, info['開催日'], info, params)})
        return pd.DataFrame(rows)

    def debut(self, race_id):
        return self.races.loc[race_id, 'クラス'] == '新馬'


def race_ids(data, period, start=None, end=None):
    r = data.races.loc[data.races['期間'].eq(period)] if period else data.races
    if start:
        r = r.loc[r['開催日'].ge(start)]
    if end:
        r = r.loc[r['開催日'].le(end)]
    return [x for x in r.index if x in data.complete]


def base_rate(data, ids):
    fields = data.bt.runners.loc[data.bt.runners['レースID'].isin(ids) & data.bt.runners['実出走']]
    return float(fields['着順'].le(3).mean()) if len(fields) else 0.21


def fit_v1(data, train_ids, params, cache):
    params.priors = {'top3': base_rate(data, train_ids)}
    feats = [cache(rid, params) for rid in train_ids if not data.debut(rid)]
    train = pd.concat(feats, ignore_index=True)
    v1.fit_priors(train, params)
    params.priors['top3'] = base_rate(data, train_ids)
    x = v1.design(train, params)
    fields = data.bt.runners.set_index(['レースID', '馬ID'])
    finish = fields.reindex(list(zip(train['レースID'], train['馬ID'])))['着順'].to_numpy()
    winner = (finish == 1).astype(float)
    index = pd.factorize(train['レースID'])[0]
    return v1.fit_logit(x, index, winner, l2=params.l2)


def predict_v1(data, ids, params, beta, cache):
    out = []
    for rid in ids:
        f = cache(rid, params)
        out.append(v1.rank_race(f, beta, params, debut_race=data.debut(rid)).assign(_s=v1.design(f, params) @ beta))
    return pd.concat(out, ignore_index=True)


def winner_logloss(data, pred):
    losses = []
    for rid, g in pred.groupby('レースID'):
        if g['点数'].isna().all():
            continue
        s = g['_s'].to_numpy(float)
        p = np.exp(s - s.max())
        p /= p.sum()
        field = data.bt.field(rid).set_index('馬ID')
        win = field.reindex(g['馬ID'])['着順'].to_numpy() == 1
        if win.sum() == 1:
            losses.append(-np.log(p[win][0] + 1e-12))
    return float(np.mean(losses)) if losses else np.nan


def evaluate(data, ids, pred):
    rows = []
    for rid in ids:
        m = race_metrics(pred.loc[pred['レースID'].eq(rid)], data.bt.field(rid))
        rows.append({'レースID': rid, **m})
    r = pd.DataFrame(rows)
    return r.merge(data.races[['期間', '開催日', 'クラス', '距離', '競馬場']].reset_index(), on='レースID', how='left')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = Data(Backtest(args.backtest))
    memo = {}

    def cache(rid, params):
        key = (rid, params.window, params.half_life, params.shrink_k, params.band, params.priors.get('top3'))
        if key not in memo:
            memo[key] = data.features(rid, params)
        return memo[key]

    inner = race_ids(data, 'inner')
    itrain = [r for r in inner if data.races.loc[r, '開催日'] <= INNER_TRAIN_END]
    ival = [r for r in inner if data.races.loc[r, '開催日'] > INNER_TRAIN_END]
    outer, used = race_ids(data, 'outer'), race_ids(data, 'used')
    log = {'inner_train': len(itrain), 'inner_val': len(ival), 'outer': len(outer), 'used': len(used),
           'all_races': len(data.races), 'complete_races': len(data.complete)}

    def score(params, train, val):
        beta = fit_v1(data, train, params, cache)
        pred = predict_v1(data, val, params, beta, cache)
        ev = summarize(evaluate(data, val, pred))
        return winner_logloss(data, pred), ev, beta

    # 一要素ずつ（着順＋1）
    single = []
    base = v1.Params(features=('着順',))
    ll0, ev0, _ = score(base, itrain, ival)
    single.append({'特徴量': '着順のみ', '対数損失': ll0, **ev0.to_dict()})
    for cand in CANDIDATES:
        ll, ev, _ = score(v1.Params(features=('着順', cand)), itrain, ival)
        single.append({'特徴量': '着順＋' + cand, '対数損失': ll, **ev.to_dict()})
    pd.DataFrame(single).to_csv(out / 'inner_single_additions.csv', index=False, encoding='utf-8-sig')

    # 前進選択
    chosen, best, steps = ['着順'], ll0, []
    for cand in CANDIDATES:
        ll, ev, _ = score(v1.Params(features=tuple(chosen + [cand])), itrain, ival)
        keep = ll < best - 0.001
        steps.append({'候補': cand, '対数損失': ll, '採用': keep, '上位3勝ち馬率': ev['上位3勝ち馬率']})
        if keep:
            chosen.append(cand)
            best = ll
    pd.DataFrame(steps).to_csv(out / 'inner_forward_selection.csv', index=False, encoding='utf-8-sig')

    grid = []
    for hl in (None, 365, 180):
        for k in (2, 5, 10):
            for band in (200, 400):
                p = v1.Params(features=tuple(chosen), half_life=hl, shrink_k=k, band=band)
                ll, ev, _ = score(p, itrain, ival)
                grid.append({'半減期': hl or 0, 'k': k, '距離帯': band, '対数損失': ll, '上位3勝ち馬率': ev['上位3勝ち馬率']})
    grid = pd.DataFrame(grid).sort_values(['対数損失', '半減期', 'k', '距離帯']).reset_index(drop=True)
    grid.to_csv(out / 'inner_hyperparameters.csv', index=False, encoding='utf-8-sig')
    top = grid.iloc[0]
    final = v1.Params(features=tuple(chosen), half_life=(int(top['半減期']) or None), shrink_k=float(top['k']), band=int(top['距離帯']))
    beta = fit_v1(data, inner, final, cache)
    log['選択'] = {'特徴量': list(chosen), '半減期': final.half_life, 'k': final.shrink_k, '距離帯': final.band,
                 '係数（標準化後）': dict(zip(final.features, map(float, beta))), '基準率': final.priors.get('top3')}

    # 比較：旧・全馬共通・v1 を同じレースで
    results = []
    for name, ids in (('inner', inner), ('outer', outer), ('used', used)):
        if not ids:
            continue
        preds = {'旧方式': pd.concat([frozen_rank(data.bt, data.races.loc[r].to_dict() | {'レースID': r}, 'old') for r in ids]),
                 '全馬共通': pd.concat([frozen_rank(data.bt, data.races.loc[r].to_dict() | {'レースID': r}, 'common') for r in ids]),
                 '基準v1': predict_v1(data, ids, final, beta, cache)}
        per = {}
        for model, pred in preds.items():
            ev = evaluate(data, ids, pred)
            ev['モデル'] = model
            per[model] = ev
            results.append(ev)
        for other in ('旧方式', '全馬共通'):
            log[f'{name}: 基準v1−{other}'] = bootstrap_diff(per['基準v1'], per[other])
        log[f'{name}: 基準v1 対数損失'] = winner_logloss(data, preds['基準v1'])
    per_race = pd.concat(results, ignore_index=True)
    per_race.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per_race.groupby(['期間', 'モデル']).apply(summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    layers = []
    for col in ('クラス', '競馬場'):
        s = per_race.groupby(['期間', 'モデル', col]).apply(summarize, include_groups=False).reset_index()
        layers.append(s.rename(columns={col: '層'}).assign(層の種類=col))
    per_race['距離帯'] = pd.cut(per_race['距離'].astype(float), [0, 1400, 1800, 2200, 4000], labels=['〜1400', '1500〜1800', '1900〜2200', '2300〜'])
    s = per_race.groupby(['期間', 'モデル', '距離帯'], observed=True).apply(summarize, include_groups=False).reset_index()
    layers.append(s.rename(columns={'距離帯': '層'}).assign(層の種類='距離帯'))
    pd.concat(layers, ignore_index=True).to_csv(out / 'layers.csv', index=False, encoding='utf-8-sig')
    # 展開予測（ルール方式）：inner でしきい値を選び、outer で一致率を見る（公表S/M/Hとの一致）。
    import keiba_pace_v1 as pv
    published = data.bt.pace.loc[data.bt.pace['状態'].eq('成功')].set_index('レースID')['公表ペース'].map(pv.LABELS)
    prows = []
    for rid in data.races.index:
        if rid not in published.index or data.debut(rid):
            continue
        info = data.races.loc[rid]
        res, _ = pv.predict_pace(data.bt.field(rid), data.by_horse, info['開催日'], pv.PaceParams())
        prows.append({'レースID': rid, '期間': info['期間'], '公表': published[rid], '先行圧力': res['先行圧力'],
                      '保留': res['独自判定'] is None})
    pace = pd.DataFrame(prows)
    if len(pace):
        tr = pace.loc[pace['期間'].eq('inner') & ~pace['保留']]
        acc, low, high = pv.choose_thresholds(tr['先行圧力'].to_numpy(), tr['公表'].to_numpy())
        majority = tr['公表'].mode().iloc[0]
        pace['独自判定'] = np.where(pace['保留'], None, np.where(pace['先行圧力'] >= high, 'H', np.where(pace['先行圧力'] <= low, 'S', 'M')))
        pace.to_csv(out / 'pace_predictions.csv', index=False, encoding='utf-8-sig')
        prow = []
        for name, g in pace.groupby('期間'):
            judged = g.loc[~g['保留']]
            prow.append({'期間': name, 'レース数': len(g), '判定数': len(judged),
                         '一致率': float((judged['独自判定'] == judged['公表']).mean()) if len(judged) else np.nan,
                         '最多判定の一致率': float((g['公表'] == majority).mean()), '最多判定': majority,
                         '分布': g['公表'].value_counts().to_dict()})
        pd.DataFrame(prow).to_csv(out / 'pace_summary.csv', index=False, encoding='utf-8-sig')
        log['展開しきい値'] = {'low': low, 'high': high, 'inner一致率': acc, '最多判定': majority}
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps({k: v for k, v in log.items() if ':' in k or k == '選択'}, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
