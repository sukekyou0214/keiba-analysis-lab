"""v8（docs/accuracy/00 の 2026-11-26 追記「v8」どおり）。

v8a ＝ v5 ＋ 乗り替わり・休み明け・距離替わり・コース替わり（前日までに分かる）。
v8b ＝ v8a ＋ 馬体重増減・当日の内有利×内枠点・当日の先行有利×先行度（当日に分かる）。
v5・v8a・v8b とも手元の JRA 芝の全期間で学習し、2024年秋（未使用）の新馬戦以外で1回だけ比べる。
使い方: python scripts/v8_test.py --train-backtest DIR [DIR ...] --test-backtest DIR --out DIR
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import keiba_eval  # noqa: E402
import keiba_model_v1 as v1  # noqa: E402
from v7_test import first_corner  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
A_SET = ('乗り替わり', '休み明け', '距離替わり', 'コース替わり')
B_SET = ('馬体重増減', '当日内有利×内枠点', '当日先行有利×先行度')
REST_DAYS = 70


def weight_change(text):
    """「470 (+10)」「518(-4)」→ 増減の絶対値÷10。読めなければ0。"""
    hit = re.search(r'\(\s*([+-]?\d+)\s*\)', str(text or ''))
    return abs(int(hit[1])) / 10 if hit else 0.0


def distance_of(text):
    hit = re.search(r'(\d{3,4})', str(text or ''))
    return float(hit[1]) if hit else np.nan


def prepare(raw):
    """戦績（馬のページ）：日付・場・距離・騎手・レースID・通過・頭数。"""
    h = pd.DataFrame({'馬ID': raw['馬ID'].astype(str), 'レースID': raw['レースID'].astype(str),
                      '日付': pd.to_datetime(raw['日付'], errors='coerce'),
                      '場': raw['開催'].astype(str).str.replace(r'\d', '', regex=True),
                      '距離': raw['距離'].map(distance_of), '騎手': raw['騎手'].astype(str).str.strip(),
                      '頭数': pd.to_numeric(raw['頭 数'], errors='coerce'), '通過': raw['通過']})
    h = h.loc[h['日付'].notna()].drop_duplicates(['馬ID', 'レースID'])
    h['先行'] = [first_corner(t, f) for t, f in zip(h['通過'], h['頭数'])]
    return {k: g.sort_values('日付') for k, g in h.groupby('馬ID')}


def a_features(prior, day, venue, distance, jockey):
    """prior：対象日より前の走（日付順）。jockey：今回の騎手（戦績の表記）。"""
    if prior is None or prior.empty:
        return {c: 0.0 for c in A_SET}
    last = prior.iloc[-1]
    gap = (day - last['日付']).days
    return {'乗り替わり': float(bool(jockey) and jockey != last['騎手']),
            '休み明け': float(gap >= REST_DAYS),
            '距離替わり': abs(distance - last['距離']) / 400 if pd.notna(last['距離']) and pd.notna(distance) else 0.0,
            'コース替わり': float(last['場'] != venue)}


def day_bias(fields):
    """同じ日・同じ競馬場の前のレースの結果（[(着順, 馬番, 通過) の表]）→ (内有利, 先行有利)。前のレースがなければ (0, 0)。"""
    inner, lead = [], []
    for f in fields:
        f = f.loc[f['実出走']]
        n = len(f)
        if n <= 1:
            continue
        num = pd.to_numeric(f['馬番'], errors='coerce')
        pos = pd.Series([first_corner(t, n) for t in f['通過']], index=f.index)
        top = pd.to_numeric(f['着順'], errors='coerce').le(3)
        if not top.any():
            continue
        inner.append((1 - (num[top] - 1) / (n - 1)).mean() - 0.5)
        if pos.notna().any() and pos[top].notna().any():
            lead.append(pos.mean() - pos[top].mean())
    return (float(np.mean(inner)) if inner else 0.0, float(np.mean(lead)) if lead else 0.0)


def v8_season(path):
    from v7_test import v7_season
    base = v7_season(path)
    cls = type(base)

    class V8Season(cls):
        def __init__(self, p):
            super().__init__(p)
            self.h8 = prepare(self.data.bt.raw)
            self.bias = {}

        def day_bias(self, rid):
            if rid not in self.bias:
                before = [r for r in self.data.races.index if r[:10] == rid[:10] and r[10:] < rid[10:]]
                self.bias[rid] = day_bias([self.data.bt.field(r) for r in before])
            return self.bias[rid]

        def features(self, rid, params):
            f = super().features(rid, params)
            if A_SET[0] in f:
                return f
            info = self.data.races.loc[rid]
            day = pd.Timestamp(info['開催日'])
            field = self.data.bt.field(rid).assign(馬ID=lambda x: x['馬ID'].astype(str)).set_index('馬ID')
            inner_bias, lead_bias = self.day_bias(rid)
            rows = []
            for hid in f['馬ID'].astype(str):
                g = self.h8.get(hid)
                prior = g.loc[g['日付'] < day] if g is not None else None
                now = g.loc[g['レースID'].eq(rid), '騎手'] if g is not None else pd.Series(dtype=str)
                a = a_features(prior, day, info['競馬場'], float(info['距離']), now.iloc[0] if len(now) else '')
                w = weight_change(field.loc[hid, '馬体重']) if hid in field.index and '馬体重' in field else 0.0
                rows.append(a | {'馬体重増減': w, '_内': inner_bias, '_先': lead_bias})
            x = pd.DataFrame(rows, index=f.index)
            for c in A_SET + ('馬体重増減',):
                f[c] = x[c]
            f['当日内有利×内枠点'] = x['_内'] * pd.to_numeric(f['内枠点'], errors='coerce').fillna(0.5)
            f['当日先行有利×先行度'] = x['_先'] * pd.to_numeric(f['先行度'], errors='coerce').fillna(0.5)
            return f
    return V8Season(path)


def main(argv=None):
    import market_test as mt
    import v2_candidates as vc
    import v4_test as v4
    import v5_test as v5
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-backtest', nargs='+', required=True)
    ap.add_argument('--test-backtest', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trains = [v8_season(p) for p in args.train_backtest]
    base, _, _ = v1.load_artifact(ROOT / 'models' / 'v1.json')
    p5 = vc.spec(base, list(base.features) + ['近走人気'])
    pa = vc.spec(base, list(base.features) + ['近走人気', *A_SET])
    pb = vc.spec(base, list(base.features) + ['近走人気', *A_SET, *B_SET])
    b5, ba, bb = (vc.fit([(s, s.ids) for s in trains], p) for p in (p5, pa, pb))
    test = v8_season(args.test_backtest)
    ids = [r for r in test.ids if not test.data.debut(r)]
    preds = {'全馬共通': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'common')
                                 for r in ids]),
             'v5': v4.predict(test, ids, p5, b5), 'v8a': v4.predict(test, ids, pa, ba), 'v8b': v4.predict(test, ids, pb, bb),
             '参考：当日の人気': v5.market(test, ids)}
    per = pd.concat([vc.evaluate(test.base, ids, p).assign(モデル=m) for m, p in preds.items()], ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in preds}
    log = {'学習レース': sum(len(s.ids) for s in trains), '評価レース（新馬戦以外）': len(ids),
           '係数': {n: dict(zip(p.features, map(float, b))) for n, p, b in (('v5', p5, b5), ('v8a', pa, ba), ('v8b', pb, bb))}}
    for a, b in (('v8a', 'v5'), ('v8b', 'v8a'), ('v8b', 'v5'), ('v5', '全馬共通')):
        log[f'{a}−{b}'] = keiba_eval.bootstrap_diff(by[a], by[b])
    log['判定 v8a'] = 'v8aを影の予測に加える' if log['v8a−v5']['下限'] > 0 else '加えない'
    log['判定 v8b'] = 'v8bを影の予測に加える（当日の情報が効いた）' if log['v8b−v8a']['下限'] > 0 else '加えない'
    # 参考（判断には使わない）：確定オッズに v8b のロジットを足すと、勝ち馬の対数尤度が上がるか（学習の期間で係数、評価の期間で差）
    def tables(season, rids):
        rows = []
        for rid in rids:
            f = season.features(rid, pb)
            s = v1.design(f, pb) @ bb
            lab = mt.rank_dict(f['馬ID'], s)
            rows.append(mt.race_rows(rid, season.data.bt.field(rid), lab, {}))
        return pd.concat(rows, ignore_index=True)
    try:
        tr = pd.concat([tables(s, [r for r in s.ids if not s.data.debut(r)]) for s in trains], ignore_index=True)
        te = tables(test, ids)
        tr_r, _ = mt.m1_races(tr)
        te_r, drop = mt.m1_races(te)
        c1, c2 = mt.fit_clogit(tr_r, False), mt.fit_clogit(tr_r, True)
        log['参考：確定オッズ＋v8b − 確定オッズだけ（対数尤度）'] = mt.m1_eval(te_r, c1, c2) | {'係数': c2.tolist(), '除いたレース': drop}
    except Exception as e:  # 参考の計算が失敗しても判定は変えない
        log['参考：確定オッズ＋v8b − 確定オッズだけ（対数尤度）'] = f'計算できなかった：{e}'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    cols = ['モデル', 'レース数', '上位3勝ち馬', '上位3勝ち馬率', '首位勝率', '首位3着内率', '三連複5頭的中率']
    print(summary[cols].to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
