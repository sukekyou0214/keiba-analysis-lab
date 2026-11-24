"""地方ベース nar-local-v1（docs/accuracy/00 の 2026-11-24 追記どおり）。

当日の出走馬の戦績ページにある地方のダートの走を学習・検証に使い、当日の各レースの参考順位を出す。
使い方: python scripts/nar_local.py --nar-work <地方の取得ZIPを展開> --out <出力> [--date 2026-11-24]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

FEATURES = ['着順点平均', '前走着順点', '着差平均', '近走人気', '先行度', '同場3着内率', '距離差', '休養日数', 'log頭数']
TRAIN_END = '2026-07-31'
TEST = ('2026-08-01', '2026-11-23')
SHRINK = 2.0
L2 = 1.0


def first_corner(text, field):
    hit = re.match(r'\s*(\d+)', str(text or ''))
    if not hit or not field or field <= 1:
        return np.nan
    return (int(hit[1]) - 1) / (field - 1)


def prepare(hist):
    h = hist.copy()
    h['日付'] = pd.to_datetime(h['日付'], errors='coerce')
    for c in ('頭数', '着順', '人気', '着差', '距離'):
        h[c] = pd.to_numeric(h[c], errors='coerce')
    h = h.loc[h['日付'].notna() & h['コース'].ne('芝') & h['状態'].eq('完走')].copy()
    n = (h['頭数'] - 1).where(h['頭数'] > 1)
    h['着順点'] = 1 - (h['着順'] - 1) / n
    h['人気点'] = 1 - (h['人気'] - 1) / n
    h['先行'] = [first_corner(t, f) for t, f in zip(h['通過_原文'], h['頭数'])]
    h['3着内'] = h['着順'].le(3).astype(float)
    return h.drop_duplicates(['馬ID', 'レースID']).sort_values(['馬ID', '日付'])


def features_for(prior, venue, distance, day, field):
    """prior：その馬の、対象より前の走（日付順）。"""
    last = prior.tail(5)
    if last.empty:
        return None
    same = prior.loc[prior['競馬場'].eq(venue)]
    base = prior['3着内'].mean()
    return {'着順点平均': last['着順点'].mean(), '前走着順点': last['着順点'].iloc[-1], '着差平均': last['着差'].mean(),
            '近走人気': last['人気点'].mean(), '先行度': last['先行'].mean(),
            '同場3着内率': (same['3着内'].sum() + SHRINK * base) / (len(same) + SHRINK),
            '距離差': abs(distance - last['距離'].mean()) if pd.notna(distance) else np.nan,
            '休養日数': (day - last['日付'].iloc[-1]).days, 'log頭数': np.log(field) if field and field > 0 else np.nan}


def training_rows(h):
    rows = []
    for hid, g in h.groupby('馬ID'):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            r = g.iloc[i]
            if r['JRA'] in (True, 'True'):
                continue  # 学習の対象は地方の走だけ
            f = features_for(g.iloc[:i], r['競馬場'], r['距離'], r['日付'], r['頭数'])
            if f:
                rows.append({'馬ID': hid, 'レースID': r['レースID'], '日付': r['日付'], '3着内': r['3着内'], **f})
    return pd.DataFrame(rows)


def fit(x, y, l2=L2, iters=800, lr=0.5):
    z = np.c_[np.ones(len(x)), x]
    w = np.zeros(z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(z @ w)))
        w -= lr * (z.T @ (p - y) / len(z) + l2 * np.r_[0, w[1:]] / len(z))
    return w


def standardize(train, frame):
    med = train[FEATURES].median()
    x = frame[FEATURES].astype(float).fillna(med)
    mean, std = train[FEATURES].astype(float).fillna(med).mean(), train[FEATURES].astype(float).fillna(med).std().replace(0, 1)
    return ((x - mean) / std).to_numpy(float)


def auc(score, y):
    s, y = np.asarray(score, float), np.asarray(y, float)
    ok = ~np.isnan(s)
    s, y = s[ok], y[ok]
    ranks = pd.Series(s).rank().to_numpy()
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else np.nan


def main(argv=None):
    from keiba_model_data import normalize_history
    ap = argparse.ArgumentParser()
    ap.add_argument('--nar-work', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--date', default='2026-11-24')
    a = ap.parse_args(argv)
    work, out = Path(a.nar_work), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raws = [pd.read_csv(f, dtype=str) for f in (work / 'horses').glob('horse_*.csv') if f.name != 'horse_log.csv']
    h = prepare(normalize_history(pd.concat(raws, ignore_index=True)))
    day = pd.Timestamp(a.date)
    h = h.loc[h['日付'] < day]
    rows = training_rows(h)
    train = rows.loc[rows['日付'] <= TRAIN_END]
    test = rows.loc[rows['日付'].between(*TEST)]
    w = fit(standardize(train, train), train['3着内'].to_numpy(float))
    zt = np.c_[np.ones(len(test)), standardize(train, test)]
    score = zt @ w
    y = test['3着内'].to_numpy(float)
    res = {'model': auc(score, y), 'pop': auc(test['近走人気'], y), 'last': auc(test['前走着順点'], y)}
    rng = np.random.default_rng(0)
    diffs = []
    for _ in range(2000):
        i = rng.integers(0, len(test), len(test))
        diffs.append(auc(score[i], y[i]) - auc(test['近走人気'].to_numpy(float)[i], y[i]))
    lo, hi = np.nanpercentile(diffs, [2.5, 97.5])
    log = {'学習の走': len(train), '検証の走': len(test), '検証の3着内率': float(y.mean()),
           'AUC': {'nar-local-v1': res['model'], '近走人気だけ': res['pop'], '前走着順点だけ': res['last']},
           'AUC差（v1−近走人気）': {'差': res['model'] - res['pop'], '下限': float(lo), '上限': float(hi)},
           '係数（学習期間）': dict(zip(['切片'] + FEATURES, map(float, w)))}
    log['判定'] = '参考（地方の走で検証済み）' if lo > 0 else '根拠不足'
    allw = fit(standardize(rows, rows), rows['3着内'].to_numpy(float))
    log['係数（全期間）'] = dict(zip(['切片'] + FEATURES, map(float, allw)))
    tables = []
    for info_path in sorted(work.glob('info_*.json')):
        info = json.loads(info_path.read_text(encoding='utf-8'))
        rid = info['レースID']
        ent = pd.read_csv(work / f'entries_{rid}.csv', dtype={'馬ID': str})
        ent = ent.loc[ent['状態'].eq('出走予定')]
        listed = str(info.get('一覧表記', ''))
        dist = re.search(r'ダ(\d{3,4})m', listed)
        distance = float(dist[1]) if dist else np.nan
        feats = []
        for _, e in ent.iterrows():
            f = features_for(h.loc[h['馬ID'].eq(str(e['馬ID']))], info.get('競馬場', '浦和'), distance, day, len(ent))
            feats.append({'馬ID': str(e['馬ID']), **(f or {c: np.nan for c in FEATURES}), '戦績なし': f is None})
        f = pd.DataFrame(feats)
        s = np.c_[np.ones(len(f)), standardize(rows, f)] @ allw
        lo_, hi_ = s.min(), s.max()
        f['地方点'] = ((s - lo_) / (hi_ - lo_) * 100).round(1) if hi_ > lo_ else 100.0
        f = f.merge(ent[['馬ID', '馬番', '馬名', '騎手']], on='馬ID')
        f = f.sort_values(['地方点', '馬番'], ascending=[False, True])
        f.insert(0, '順位', range(1, len(f) + 1))
        f.insert(0, 'レース', listed)
        f.insert(0, '発走', info.get('発走', ''))
        tables.append(f)
    if tables:
        allt = pd.concat(tables, ignore_index=True).sort_values(['発走', '順位'])
        allt.to_csv(out / f'地方参考順位_{a.date}.csv', index=False, encoding='utf-8-sig')
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
