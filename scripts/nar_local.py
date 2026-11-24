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
V2_EXTRA = ['内枠点', '騎手3着内率']
V3_EXTRA = ['地方格平均', '格差', '前走格']
JOCKEY_K = 20.0
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
    from keiba_nar_class import nar_class_level
    h['格'] = np.where(h['JRA'].isin([True, 'True']), np.nan, h['レース名'].map(nar_class_level))
    return h.drop_duplicates(['馬ID', 'レースID']).sort_values(['馬ID', '日付'])


def features_for(prior, venue, distance, day, field, number=None, jockey_rate=np.nan, cls=np.nan):
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
            '休養日数': (day - last['日付'].iloc[-1]).days, 'log頭数': np.log(field) if field and field > 0 else np.nan,
            '内枠点': 1 - (number - 1) / (field - 1) if number and field and field > 1 and number <= field else np.nan, '騎手3着内率': jockey_rate,
            '地方格平均': local_cls.mean() if len(local_cls := prior.loc[~prior['JRA'].isin([True, 'True'])].tail(5)['格'].dropna()) else np.nan,
            '前走格': last['格'].iloc[-1], '格差': np.nan if pd.isna(cls) else cls - (local_cls.mean() if len(local_cls) else np.nan)}


def jockey_table(h):
    """騎手ごと・日付ごとの「その日より前」の地方3着内率（縮小 k=20、全体平均へ）。"""
    local = h.loc[~h['JRA'].isin([True, 'True']) & h['騎手'].notna()]
    base = local['3着内'].mean()
    daily = local.groupby(['騎手', '日付'])['3着内'].agg(['sum', 'count']).reset_index().sort_values(['騎手', '日付'])
    daily[['s', 'n']] = daily.groupby('騎手')[['sum', 'count']].cumsum().to_numpy() - daily[['sum', 'count']].to_numpy()
    daily['率'] = (daily['s'] + JOCKEY_K * base) / (daily['n'] + JOCKEY_K)
    return daily, base


def jockey_rate_at(daily, base, name, day):
    d = daily.loc[daily['騎手'].eq(name) & daily['日付'].lt(day)]
    if d.empty:
        return base
    last = d.iloc[-1]
    return (last['s'] + last['sum'] + JOCKEY_K * base) / (last['n'] + last['count'] + JOCKEY_K)


def training_rows(h):
    daily, base = jockey_table(h)
    rate = daily.set_index(['騎手', '日付'])['率']
    rows = []
    for hid, g in h.groupby('馬ID'):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            r = g.iloc[i]
            if r['JRA'] in (True, 'True'):
                continue  # 学習の対象は地方の走だけ
            jr = rate.get((r['騎手'], r['日付']), base) if pd.notna(r.get('騎手')) else np.nan
            f = features_for(g.iloc[:i], r['競馬場'], r['距離'], r['日付'], r['頭数'], pd.to_numeric(r.get('馬番'), errors='coerce'), jr, r['格'])
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


def standardize(train, frame, cols=None):
    cols = cols or FEATURES
    med = train[cols].median()
    x = frame[cols].astype(float).fillna(med)
    mean, std = train[cols].astype(float).fillna(med).mean(), train[cols].astype(float).fillna(med).std().replace(0, 1)
    return ((x - mean) / std).to_numpy(float)


def auc(score, y):
    s, y = np.asarray(score, float), np.asarray(y, float)
    ok = ~np.isnan(s)
    s, y = s[ok], y[ok]
    ranks = pd.Series(s).rank().to_numpy()
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else np.nan


def load_pool(works):
    from keiba_model_data import normalize_history
    raws = [pd.read_csv(f, dtype=str) for w in works for f in (Path(w) / 'horses').glob('horse_*.csv') if f.name != 'horse_log.csv']
    raw = pd.concat(raws, ignore_index=True)
    h = normalize_history(raw)
    jk = raw[['馬ID', 'レースID', '騎手']].dropna(subset=['レースID']).drop_duplicates(['馬ID', 'レースID'])
    h = h.merge(jk, on=['馬ID', 'レースID'], how='left') if '騎手' not in h else h
    return prepare(h)


def eval_auc(train, test, cols):
    w = fit(standardize(train, train, cols), train['3着内'].to_numpy(float))
    return np.c_[np.ones(len(test)), standardize(train, test, cols)] @ w


def boot_diff(a, b, y, seed=0, n=2000):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        out.append(auc(a[i], y[i]) - auc(b[i], y[i]))
    return float(np.nanmean(out)), float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def match_jockey(full, shorts):
    from keiba_jockey import _subsequence, normalize
    f = normalize(full)
    if f in shorts:
        return f
    hits = [x for x in shorts if x and _subsequence(normalize(x), f)]
    return hits[0] if len(hits) == 1 else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nar-work', nargs='+', required=True, help='戦績の取得フォルダ（複数可、最初が予想の対象）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--date', default='2026-11-24')
    ap.add_argument('--version', choices=('v1', 'auto'), default='v1', help='auto：事前登録どおり v2 を判定して使う')
    a = ap.parse_args(argv)
    work, out = Path(a.nar_work[0]), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    day = pd.Timestamp(a.date)
    h = load_pool(a.nar_work)
    h = h.loc[h['日付'] < day]
    rows = training_rows(h)
    train = rows.loc[rows['日付'] <= TRAIN_END]
    test = rows.loc[rows['日付'].between(*TEST)]
    y = test['3着内'].to_numpy(float)
    v1s = eval_auc(train, test, FEATURES)
    pop = test['近走人気'].to_numpy(float)
    log = {'学習の走': len(train), '検証の走': len(test), '検証の3着内率': float(y.mean()),
           'AUC': {'nar-local-v1': auc(v1s, y), '近走人気だけ': auc(pop, y), '前走着順点だけ': auc(test['前走着順点'], y)}}
    d = boot_diff(v1s, pop, y)
    log['AUC差（v1−近走人気）'] = dict(zip(('差', '下限', '上限'), d))
    cols, name = FEATURES, 'nar-local-v1'
    if a.version == 'auto':
        v2s = eval_auc(train, test, FEATURES + V2_EXTRA)
        log['AUC']['nar-local-v2'] = auc(v2s, y)
        log['AUC差（v2−v1）'] = dict(zip(('差', '下限', '上限'), boot_diff(v2s, v1s, y)))
        log['AUC差（v2−近走人気）'] = dict(zip(('差', '下限', '上限'), boot_diff(v2s, pop, y)))
        v3s = eval_auc(train, test, FEATURES + V3_EXTRA)
        log['AUC']['nar-local-v3'] = auc(v3s, y)
        log['AUC差（v3−v1）'] = dict(zip(('差', '下限', '上限'), boot_diff(v3s, v1s, y)))
        log['AUC差（v3−近走人気）'] = dict(zip(('差', '下限', '上限'), boot_diff(v3s, pop, y)))
        ok = [(log['AUC'][k], -i, k, c) for i, (k, c) in enumerate((('nar-local-v2', FEATURES + V2_EXTRA), ('nar-local-v3', FEATURES + V3_EXTRA)))
              if log[f"AUC差（{k[-2:]}−v1）"]['下限'] > 0]
        if ok:
            _, _, name, cols = max(ok)
    used = log[f'AUC差（{name[-2:]}−近走人気）']
    log['使う方式'] = name
    log['判定'] = '参考（地方の走で検証済み）' if used['下限'] > 0 else '根拠不足'
    allw = fit(standardize(rows, rows, cols), rows['3着内'].to_numpy(float))
    log['係数（全期間）'] = dict(zip(['切片'] + cols, map(float, allw)))
    daily, base = jockey_table(h)
    shorts = set(daily['騎手'].dropna())
    tables, races = [], []
    for info_path in sorted(work.glob('info_*.json')):
        info = json.loads(info_path.read_text(encoding='utf-8'))
        rid = info['レースID']
        ent = pd.read_csv(work / f'entries_{rid}.csv', dtype={'馬ID': str})
        ent = ent.loc[ent['状態'].eq('出走予定')]
        listed = str(info.get('一覧表記', ''))
        dist = re.search(r'ダ(\d{3,4})m', listed)
        distance = float(dist[1]) if dist else np.nan
        from keiba_nar_class import nar_class_level
        paren = re.findall(r'\(([^)]*)\)', listed + ' ' + str(info.get('レース名', '')))
        race_cls = next((v for v in map(nar_class_level, paren + [listed]) if pd.notna(v)), np.nan)
        feats = []
        for _, e in ent.iterrows():
            prior = h.loc[h['馬ID'].eq(str(e['馬ID']))]
            jn = match_jockey(str(e.get('騎手', '')), shorts)
            jr = jockey_rate_at(daily, base, jn, day) if jn else np.nan
            f = features_for(prior, info.get('競馬場', '浦和'), distance, day, len(ent), pd.to_numeric(e.get('馬番'), errors='coerce'), jr, race_cls)
            local_runs = int((~prior['JRA'].isin([True, 'True'])).sum())
            feats.append({'馬ID': str(e['馬ID']), **(f or {c: np.nan for c in FEATURES + V2_EXTRA + V3_EXTRA}), '戦績なし': f is None, '地方走数': local_runs})
        f = pd.DataFrame(feats)
        s = np.c_[np.ones(len(f)), standardize(rows, f, cols)] @ allw
        lo_, hi_ = s.min(), s.max()
        f['地方点'] = ((s - lo_) / (hi_ - lo_) * 100).round(1) if hi_ > lo_ else 100.0
        f = f.merge(ent[['馬ID', '馬番', '馬名', '騎手']], on='馬ID')
        f = f.sort_values(['地方点', '馬番'], ascending=[False, True])
        f.insert(0, '順位', range(1, len(f) + 1))
        f.insert(0, 'レース', listed)
        f.insert(0, '発走', info.get('発走', ''))
        f.insert(0, 'レースID', rid)
        tables.append(f)
        p = f['地方点'].to_numpy()
        races.append({'レースID': rid, '発走': info.get('発走', ''), 'レース': listed, '頭数': len(f),
                      '点差': float(p[:5].mean() - p[5:].mean()) if len(p) > 5 else np.inf,
                      '戦績少': int((f['地方走数'] < 2).sum())})
    if tables:
        allt = pd.concat(tables, ignore_index=True).sort_values(['発走', '順位'])
        allt.to_csv(out / f'地方参考順位_{a.date}.csv', index=False, encoding='utf-8-sig')
        r = pd.DataFrame(races).sort_values(['頭数', '点差', 'レースID'], ascending=[True, False, True])
        r['ボックス向き順'] = range(1, len(r) + 1)
        tier = np.where(r['ボックス向き順'] <= 3, 0, np.where(r['ボックス向き順'] <= 6, 1, 2)) + (r['戦績少'] >= 2).astype(int)
        r['信頼度'] = np.array(['A', 'B', 'C', 'C'])[np.minimum(tier, 3)]
        r.to_csv(out / f'信頼度区分_{a.date}.csv', index=False, encoding='utf-8-sig')
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
