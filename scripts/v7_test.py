"""v7（v5 ＋ 先行度・同場3着内率・内枠点）の検証（docs/accuracy/00 の 2026-11-25 追記「v7」どおり）。

v5・v7 とも、手元の JRA 芝の全期間（2025年秋・2026年春・夏・秋）で学習し、2025年春（未使用）の新馬戦以外で1回だけ比べる。
参考として全馬共通・当日の人気上位、重賞（G1〜G3）だけの数字も出す（重賞の数字は判断に使わない）。
使い方: python scripts/v7_test.py --train-backtest DIR [DIR ...] --test-backtest DIR --out DIR
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

ROOT = Path(__file__).resolve().parents[1]
V7_EXTRA = ('先行度', '同場3着内率', '内枠点')
SHRINK = 2.0


def first_corner(text, field):
    hit = re.match(r'\s*(\d+)', str(text or ''))
    if not hit or not field or field <= 1:
        return np.nan
    return (int(hit[1]) - 1) / (field - 1)


def prepare_history(raw):
    """出走馬の戦績（netkeiba の馬のページ）から、日付・競馬場・先行・3着内を作る。完走した走だけ。"""
    h = pd.DataFrame({'馬ID': raw['馬ID'].astype(str), 'レースID': raw['レースID'].astype(str),
                      '日付': pd.to_datetime(raw['日付'], errors='coerce'),
                      '場': raw['開催'].astype(str).str.replace(r'\d', '', regex=True),
                      '頭数': pd.to_numeric(raw['頭 数'], errors='coerce'),
                      '着順': pd.to_numeric(raw['着 順'], errors='coerce'), '通過': raw['通過']})
    h = h.loc[h['日付'].notna() & h['着順'].notna()].drop_duplicates(['馬ID', 'レースID'])
    h['先行'] = [first_corner(t, f) for t, f in zip(h['通過'], h['頭数'])]
    h['3着内'] = h['着順'].le(3).astype(float)
    return {k: g.sort_values('日付') for k, g in h.groupby('馬ID')}


def v7_features(prior, venue, number, field):
    """prior：その馬の対象日より前の走（日付順）。"""
    if prior is None or prior.empty:
        lead = same_rate = np.nan
    else:
        lead = prior.tail(5)['先行'].mean()
        same = prior.loc[prior['場'].eq(venue), '3着内']
        same_rate = (same.sum() + SHRINK * prior['3着内'].mean()) / (len(same) + SHRINK)
    inner = 1 - (number - 1) / (field - 1) if pd.notna(number) and field and field > 1 and number <= field else np.nan
    return {'先行度': lead, '同場3着内率': same_rate, '内枠点': inner}


def v7_season(path):
    import v4_test as v4

    class V7Season(v4.JockeySeason):
        def __init__(self, p):
            super().__init__(p)
            self.hist = prepare_history(self.data.bt.raw)

        def features(self, rid, params):
            f = super().features(rid, params)
            if '先行度' not in f:
                info = self.data.races.loc[rid]
                day = pd.Timestamp(info['開催日'])
                field = self.data.bt.field(rid)
                field = field.loc[field['実出走']] if '実出走' in field else field
                n = len(field)
                num = dict(zip(field['馬ID'].astype(str), pd.to_numeric(field['馬番'], errors='coerce')))
                rows = []
                for hid in f['馬ID'].astype(str):
                    g = self.hist.get(hid)
                    prior = g.loc[g['日付'] < day] if g is not None else None
                    rows.append(v7_features(prior, info['競馬場'], num.get(hid, np.nan), n))
                extra = pd.DataFrame(rows, index=f.index)
                for c in V7_EXTRA:
                    f[c] = extra[c]
            return f
    return V7Season(path)


def graded(season, ids):
    """重賞（G1〜G3）。クラス列（G1・G2・G3）で判定する。"""
    cls = season.data.races.loc[ids, 'クラス'].astype(str)
    return {r for r, c in zip(ids, cls) if c in ('G1', 'G2', 'G3')}


def main(argv=None):
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
    trains = [v7_season(p) for p in args.train_backtest]
    base, _, _ = v1.load_artifact(ROOT / 'models' / 'v1.json')
    p5 = vc.spec(base, list(base.features) + ['近走人気'])
    p7 = vc.spec(base, list(base.features) + ['近走人気', *V7_EXTRA])
    b5 = vc.fit([(s, s.ids) for s in trains], p5)
    b7 = vc.fit([(s, s.ids) for s in trains], p7)
    test = v7_season(args.test_backtest)
    ids = [r for r in test.ids if not test.data.debut(r)]
    preds = {'全馬共通': pd.concat([keiba_eval.frozen_rank(test.data.bt, test.data.races.loc[r].to_dict() | {'レースID': r}, 'common')
                                 for r in ids]),
             'v5': v4.predict(test, ids, p5, b5), 'v7': v4.predict(test, ids, p7, b7), '参考：当日の人気': v5.market(test, ids)}
    per = pd.concat([vc.evaluate(test.base, ids, p).assign(モデル=m) for m, p in preds.items()], ignore_index=True)
    g = graded(test, ids)
    per['重賞'] = per['レースID'].astype(str).isin({str(r) for r in g})
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    sg = per.loc[per['重賞']].groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    sg.to_csv(out / 'summary_graded.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in preds}
    log = {'学習レース': sum(len(s.ids) for s in trains), '評価レース（新馬戦以外）': len(ids), '重賞': len(g),
           'v7の係数': dict(zip(p7.features, map(float, b7))), 'v5の係数': dict(zip(p5.features, map(float, b5)))}
    for a, b in (('v7', 'v5'), ('v7', '全馬共通'), ('v5', '全馬共通')):
        log[f'{a}−{b}'] = keiba_eval.bootstrap_diff(by[a], by[b])
    log['判定'] = 'v7を影の予測に加える' if log['v7−v5']['下限'] > 0 else '加えない'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    cols = ['モデル', 'レース数', '上位3勝ち馬', '上位3勝ち馬率', '首位勝率', '首位3着内率', '三連複5頭的中率']
    print(summary[cols].to_string())
    print(sg[cols].to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
