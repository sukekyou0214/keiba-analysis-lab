"""多頭数の注目レース box-big（docs/accuracy/00 の 2026-11-27 追記どおり）。

対象：JRA 芝・新馬戦以外・出走頭数13以上。v5（本番の models/v5.json）の上位5頭の三連複ボックス（10点・1,000円）の
的中率と実払戻の回収率を、各日上位2R（G-B2：上位5頭が1〜3着を占める見込み順、G-B3：5位と6位の点差順）と多頭数の全レースで比べる。
使い方:
  python scripts/box_big_test.py --backtest DIR --stage select --out DIR             # 2025年春：候補を1つ選ぶ
  python scripts/box_big_test.py --backtest DIR --stage final --rule G-B2 --out DIR  # 2025年夏：1回だけ確かめる
的中率・回収率は過去の検証の値で、当たる確率ではない。
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

import keiba_box as kb  # noqa: E402
import keiba_model_v1 as v1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_FIELD, PER_DAY, STAKE = 13, 2, 1000.0
RULES = {'G-B2': ('B2', False), 'G-B3': ('B3', False)}


def race_table(path):
    import v2_candidates as vc
    import v4_test as v4
    season = vc.Season(path)
    params, beta, _ = v1.load_artifact(ROOT / 'models' / 'v5.json')
    infos = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (Path(path) / 'races').glob('*.json')}
    rows, broken = [], []
    for rid in season.ids:
        if season.data.debut(rid):
            continue
        if pd.isna(pd.to_numeric(season.data.races.loc[rid, '距離'], errors='coerce')):
            broken.append(rid)
            continue
        field = season.data.bt.field(rid)
        starters = field.loc[field['実出走']]
        if len(starters) < MIN_FIELD:
            continue
        pred = v4.predict(season, [rid], params, beta)
        pred = pred.assign(_r=pd.to_numeric(pred['順位'], errors='coerce')).dropna(subset=['_r']).sort_values('_r')
        if len(pred) < 5:
            continue
        vals = kb.race_values(pred[['順位', '点数']])
        num = field.assign(馬ID=field['馬ID'].astype(str)).set_index('馬ID')['馬番']
        top5 = {str(num.get(str(h))) for h in pred['馬ID'].astype(str).head(5)}
        top3 = starters.loc[starters['着順'].le(3)]
        settled = len(top3) == 3
        combo = '-'.join(sorted(top3['馬番'].astype(str), key=int)) if settled else ''
        hit = settled and set(top3['馬番'].astype(str)) <= top5
        pays = [p for p in (infos.get(rid, {}).get('払戻') or []) if p.get('券種') == '三連複']
        paid = float(sum(p['払戻'] for p in pays if '-'.join(sorted(str(p['組番']).split('-'), key=int)) == combo)) * 10 if hit else 0.0
        pop = pd.to_numeric(starters['人気'], errors='coerce') if '人気' in starters else pd.Series(dtype=float)
        pop5 = set(starters.loc[pop.le(5), '馬番'].astype(str))
        rows.append({'レースID': rid, '日付': season.data.races.loc[rid, '開催日'], '頭数': len(starters), 'B2': vals['B2'], 'B3': vals['B3'],
                     '的中': bool(hit), '払戻': paid, '払戻あり': bool(pays), '人気上位5的中': bool(settled and set(top3['馬番'].astype(str)) <= pop5)})
    return pd.DataFrame(rows), broken


def chosen(tab, rule):
    key, _ = RULES[rule]
    o = tab.sort_values([key, 'レースID'], ascending=[False, True], na_position='last')
    o = o.assign(_n=o.groupby('日付').cumcount() + 1)
    return set(o.loc[o['_n'].le(PER_DAY), 'レースID'])


def summary(tab, ids):
    t = tab.loc[tab['レースID'].isin(ids)]
    p = t.loc[t['払戻あり']]
    return {'レース': len(t), '的中': int(t['的中'].sum()), '的中率': float(t['的中'].mean()) if len(t) else np.nan,
            '回収率': float(p['払戻'].sum() / (STAKE * len(p))) if len(p) else np.nan, '払戻ありのレース': len(p),
            '参考：人気上位5の的中率': float(t['人気上位5的中'].mean()) if len(t) else np.nan}


def boot(tab, ids, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    days = sorted(tab['日付'].unique())
    by = {d: g for d, g in tab.groupby('日付')}
    diffs, recs = [], []
    for _ in range(n):
        g = pd.concat([by[d] for d in rng.choice(days, len(days))], ignore_index=True)
        c = g.loc[g['レースID'].isin(ids)]
        if len(c) == 0:
            continue
        diffs.append(c['的中'].mean() - g['的中'].mean())
        cp = c.loc[c['払戻あり']]
        if len(cp):
            recs.append(cp['払戻'].sum() / (STAKE * len(cp)))
    q = lambda a: [float(np.quantile(a, .025)), float(np.quantile(a, .975))]
    return {'的中率の差の95%区間': q(diffs), '回収率の95%区間': q(recs)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', required=True)
    ap.add_argument('--stage', choices=('select', 'final'), required=True)
    ap.add_argument('--rule', choices=tuple(RULES))
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tab, broken = race_table(args.backtest)
    tab.drop(columns=['払戻']).assign(的中=tab['的中']).to_csv(out / f'races_{args.stage}.csv', index=False, encoding='utf-8-sig')
    log = {'段階': args.stage, '多頭数のレース': len(tab), '開催日': int(tab['日付'].nunique()), '取得が壊れて除いたレース': broken,
           '多頭数の全レース': summary(tab, set(tab['レースID']))}
    if args.stage == 'select':
        res = {r: summary(tab, chosen(tab, r)) for r in RULES}
        log['候補'] = res
        best = max(RULES, key=lambda r: (res[r]['的中率'], r == 'G-B2'))
        log['選んだ候補'] = best
    else:
        ids = chosen(tab, args.rule)
        log['候補'] = args.rule
        log['注目レース'] = summary(tab, ids)
        log |= boot(tab, ids)
        log['判定'] = '採用' if log['的中率の差の95%区間'][0] > 0 else '採用しない'
        log['妙味'] = '回収率でも上回った' if log['回収率の95%区間'][0] > 1 else '回収率で上回ったとは言えない（妙味は書かない）'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / f'run_log_{args.stage}.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
