"""地方（浦和）のレース単位の検証 nar-v1（docs/accuracy/00 の 2026-11-25 追記2つのとおり）。

候補：C1（dirt-v1 と同じ特徴量）・C2（C1＋地方4項目）・C3（C2＋格付け3項目）× 学習データ U（浦和だけ）・S（南関東4場）。
選択：〜7/31 で学習し 8/1〜8/31（浦和）の上位3勝ち馬で1つ選ぶ。最終：〜8/31 で学習し直し、9/1〜11/24（浦和）で1回評価。
使い方: python scripts/nar_backtest_test.py --urawa <浦和の収集を展開> [--others <大井・川崎・船橋の収集を展開>] --out <出力>
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
import nar_local as nl  # noqa: E402
from keiba_nar_class import nar_class_level  # noqa: E402

BASE = ('着順', '着差', 'クラス水準', 'クラス差', '距離適性', '芝実績なし', '近走人気')
LOCAL4 = ('先行度', '同場3着内率', '内枠点', '騎手3着内率')
CLASS3 = ('地方格平均', '格差', '前走格')
SETS = {'C1': BASE, 'C2': BASE + LOCAL4, 'C3': BASE + LOCAL4 + CLASS3}
SELECT_TRAIN_END, SELECT = '2026-07-31', ('2026-08-01', '2026-08-31')
FINAL_TRAIN_END, TEST = '2026-08-31', ('2026-09-01', '2026-11-24')
ORDER = ('C1U', 'C2U', 'C3U', 'C1S', 'C2S', 'C3S')


def params(features):
    return v1.Params(features=tuple(features), half_life=180, shrink_k=2.0, band=200, surface='ダ')


def merged_dir(dirs, out):
    """複数の収集フォルダを1つに見せる（races と horses を並べる。同じ馬は先のものを使う）。"""
    out = Path(out)
    for sub in ('races', 'horses'):
        (out / sub).mkdir(parents=True, exist_ok=True)
    logs = []
    for d in dirs:
        d = Path(d)
        for f in (d / 'races').glob('*'):
            t = out / 'races' / f.name
            if not t.exists():
                t.symlink_to(f.resolve())
        for f in (d / 'horses').glob('horse_[0-9]*.csv'):
            t = out / 'horses' / f.name
            if not t.exists():
                t.symlink_to(f.resolve())
        if (d / 'horses' / 'horse_log.csv').exists():
            logs.append(pd.read_csv(d / 'horses' / 'horse_log.csv', dtype=str))
    if logs:
        pd.concat(logs, ignore_index=True).drop_duplicates('馬ID').to_csv(out / 'horses' / 'horse_log.csv', index=False)
    return out


class NarData:
    """accuracy_experiment.Data に、地方4項目・格付け3項目を足す。"""

    def __init__(self, path):
        import accuracy_experiment as ax
        bt = keiba_eval.Backtest(path)
        infos = {json.loads(p.read_text(encoding='utf-8'))['レースID']: json.loads(p.read_text(encoding='utf-8'))
                 for p in (Path(path) / 'races').glob('*.json')}
        bt.races['競馬場'] = bt.races['レースID'].map(lambda r: infos[r].get('競馬場', ''))
        bt.races['クラス'] = bt.races['クラス'].where(bt.races['クラス'].eq('新馬'), '不明')
        self.infos = infos
        self.data = ax.Data(bt)
        raw = bt.raw.copy()
        h = bt.history.copy()
        jk = raw[['馬ID', 'レースID', '騎手']].dropna(subset=['レースID']).drop_duplicates(['馬ID', 'レースID'])
        if '騎手' not in h:
            h = h.merge(jk, on=['馬ID', 'レースID'], how='left')
        self.h = nl.prepare(h)
        self.by_horse = {k: g for k, g in self.h.groupby('馬ID')}
        self.daily, self.base = nl.jockey_table(self.h)
        self.jdaily = {k: g for k, g in self.daily.groupby('騎手')}
        self.memo = {}

    def race_class(self, rid):
        info = self.infos[rid]
        text = f"{info.get('一覧表記', '')} {info.get('レース名', '')}"
        paren = re.findall(r'\(([^)]*)\)', text)
        return next((v for v in map(nar_class_level, paren + [text]) if pd.notna(v)), np.nan)

    def jockey_rate(self, name, day):
        g = self.jdaily.get(name)
        if g is None:
            return self.base
        g = g.loc[g['日付'].lt(day)]
        if g.empty:
            return self.base
        last = g.iloc[-1]
        return (last['s'] + last['sum'] + nl.JOCKEY_K * self.base) / (last['n'] + last['count'] + nl.JOCKEY_K)

    def features(self, rid, p):
        key = (rid, p.priors.get('top3'))
        if key in self.memo:
            return self.memo[key]
        f = self.data.features(rid, p)
        info = self.data.races.loc[rid]
        day = pd.Timestamp(info['開催日'])
        field = self.data.bt.field(rid).set_index('馬ID')
        n = int(field['実出走'].sum()) or len(field)
        cls = self.race_class(rid)
        extra = []
        for hid in f['馬ID']:
            prior = self.by_horse.get(str(hid), self.h.iloc[0:0])
            prior = prior.loc[prior['日付'] < day]
            num = pd.to_numeric(field.loc[hid, '馬番'], errors='coerce') if hid in field.index else np.nan
            jr = self.jockey_rate(field.loc[hid, '騎手'], day) if hid in field.index else np.nan
            x = nl.features_for(prior, info['競馬場'], float(info['距離']), day, n, num, jr, cls) or {}
            local_runs = int((~prior['JRA'].isin([True, 'True'])).sum()) if len(prior) else 0
            extra.append({c: x.get(c, np.nan) for c in LOCAL4 + CLASS3} | {'地方走数': local_runs})
        f = pd.concat([f.reset_index(drop=True), pd.DataFrame(extra)], axis=1)
        self.memo[key] = f
        return f


def ids_between(nd, venues, lo, hi):
    r = nd.data.races
    keep = r.loc[r['開催日'].between(lo, hi) & r['競馬場'].isin(venues)].index
    return [x for x in keep if x in nd.data.complete and not nd.data.debut(x)]


def fit(nd, ids, feats):
    import accuracy_experiment as ax
    p = params(feats)
    return p, ax.fit_v1(nd.data, ids, p, nd.features)


def predict(nd, ids, p, beta):
    import accuracy_experiment as ax
    return ax.predict_v1(nd.data, ids, p, beta, nd.features)


def popularity_rank(nd, ids, p):
    out = []
    for rid in ids:
        f = nd.features(rid, p)
        s = pd.to_numeric(f['近走人気'], errors='coerce').fillna(-1)
        order = f.assign(_s=s).sort_values(['_s', '馬ID'], ascending=[False, True]).index
        r = f[['レースID', '馬ID']].copy()
        r['順位'] = pd.Series(range(1, len(f) + 1), index=order).reindex(f.index)
        out.append(r)
    return pd.concat(out, ignore_index=True)


def market_rank(nd, ids):
    return pd.concat([pd.DataFrame({'レースID': rid, '馬ID': nd.data.bt.field(rid)['馬ID'].astype(str),
                                    '順位': pd.to_numeric(nd.data.bt.field(rid)['人気'], errors='coerce')}) for rid in ids],
                     ignore_index=True)


def local_v1_rank(nd, ids, train_end):
    """nar-local-v1（1頭ずつのロジスティック回帰、戦績の走で学習）を同じレースに当てはめる。"""
    rows = nl.training_rows(nd.h.loc[nd.h['日付'] <= train_end])
    w = nl.fit(nl.standardize(rows, rows), rows['3着内'].to_numpy(float))
    out = []
    for rid in ids:
        f = nd.features(rid, params(BASE))
        info = nd.data.races.loc[rid]
        day = pd.Timestamp(info['開催日'])
        field = nd.data.bt.field(rid).set_index('馬ID')
        feats = []
        for hid in f['馬ID']:
            prior = nd.by_horse.get(str(hid), nd.h.iloc[0:0])
            prior = prior.loc[prior['日付'] < day]
            x = nl.features_for(prior, info['競馬場'], float(info['距離']), day, len(field)) if len(prior) else None
            feats.append({'馬ID': hid, **(x or {c: np.nan for c in nl.FEATURES})})
        g = pd.DataFrame(feats)
        s = np.c_[np.ones(len(g)), nl.standardize(rows, g)] @ w
        order = g.assign(_s=s).sort_values(['_s', '馬ID'], ascending=[False, True]).index
        r = pd.DataFrame({'レースID': rid, '馬ID': g['馬ID']})
        r['順位'] = pd.Series(range(1, len(g) + 1), index=order).reindex(g.index)
        out.append(r)
    return pd.concat(out, ignore_index=True)


def tiers(nd, pred, ids, rule='both'):
    """信頼度区分（A＝各日のボックス向き上位3、B＝4〜6、C＝7以下、地方の戦績2走未満が2頭以上で1段下げ）。"""
    rows = []
    for rid in ids:
        g = pred.loc[pred['レースID'].eq(rid)].dropna(subset=['_s']).sort_values('順位')
        s = g['_s'].to_numpy(float)
        pts = (s - s.min()) / (s.max() - s.min()) * 100 if len(s) and s.max() > s.min() else np.full(len(s), 100.0)
        gap = pts[:5].mean() - pts[5:].mean() if len(pts) > 5 else np.inf
        f = nd.features(rid, params(BASE))
        few = int((pd.to_numeric(f['地方走数'], errors='coerce').fillna(0) < 2).sum()) if '地方走数' in f else 0
        rows.append({'レースID': rid, '日付': nd.data.races.loc[rid, '開催日'], '頭数': len(g), '点差': gap, '戦績少': few})
    t = pd.DataFrame(rows)
    keys = {'both': (['頭数', '点差', 'レースID'], [True, False, True]), 'size': (['頭数', 'レースID'], [True, True]),
            'gap': (['点差', 'レースID'], [False, True])}[rule]
    t = t.sort_values(keys[0], ascending=keys[1])
    t['順'] = t.groupby('日付').cumcount() + 1
    level = np.where(t['順'] <= 3, 0, np.where(t['順'] <= 6, 1, 2)) + (t['戦績少'] >= 2).astype(int)
    t['信頼度'] = np.array(['A', 'B', 'C', 'C'])[np.minimum(level, 3)]
    return t


def payouts(nd, pred, ids):
    """上位5頭ボックスの実際の払戻（三連複10点＝1,000円、三連単60点＝6,000円）。"""
    rows = []
    for rid in ids:
        field = nd.data.bt.field(rid)
        top3 = field.assign(_f=field['着順']).dropna(subset=['_f']).sort_values('_f')
        top3 = top3.loc[top3['_f'] <= 3]
        g = pred.loc[pred['レースID'].eq(rid)].sort_values('順位').head(5)
        nums = field.set_index('馬ID')['馬番']
        five = {str(nums.get(h)) for h in g['馬ID']}
        order = [str(x) for x in top3['馬番']]
        hit = len(order) == 3 and set(order) <= five
        pay = {(x['券種'], x['組番']): x['払戻'] for x in nd.infos[rid].get('払戻', [])}
        fuku = pay.get(('三連複', '-'.join(sorted(order, key=int)))) if hit else 0
        tan = pay.get(('三連単', '-'.join(order))) if hit else 0
        rows.append({'レースID': rid, '的中': hit, '三連複払戻': fuku, '三連単払戻': tan, '払戻あり': bool(pay)})
    return pd.DataFrame(rows)


def tier_table(t, pay):
    m = t.merge(pay, on='レースID')
    out = []
    for name, g in list(m.groupby('信頼度')) + [('全レース', m)]:
        ok = g.loc[g['払戻あり']]
        out.append({'信頼度': name, 'レース数': len(g), '5頭ボックス的中': int(g['的中'].sum()), '的中率': float(g['的中'].mean()),
                    '三連複回収率': float(ok['三連複払戻'].fillna(0).sum() / (len(ok) * 1000)) if len(ok) else np.nan,
                    '三連単回収率': float(ok['三連単払戻'].fillna(0).sum() / (len(ok) * 6000)) if len(ok) else np.nan,
                    '払戻ありレース': len(ok)})
    return pd.DataFrame(out)


def tier_check(t, pay, seed=0):
    m = t.merge(pay, on='レースID')
    a = m.loc[m['信頼度'].eq('A'), '的中'].astype(float).to_numpy()
    base = m['的中'].astype(float).mean()
    if not len(a):
        return {'A': 0, '差': np.nan, '下限': np.nan, '上限': np.nan}
    rng = np.random.default_rng(seed)
    s = [a[rng.integers(0, len(a), len(a))].mean() - base for _ in range(2000)]
    return {'Aレース数': len(a), 'A的中率': float(a.mean()), '全レース': float(base), '差': float(a.mean() - base),
            '下限': float(np.percentile(s, 2.5)), '上限': float(np.percentile(s, 97.5))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--urawa', required=True)
    ap.add_argument('--others', nargs='*', default=[])
    ap.add_argument('--venue', default='浦和', help='予想の対象にする競馬場')
    ap.add_argument('--set', choices=('C1', 'C2', 'C3'), help='fit-only：特徴量の組（第1段階で選んだ組）')
    ap.add_argument('--train-venues', default='all', help='fit-only：all（集めた全場）か、対象の競馬場だけ（venue）')
    ap.add_argument('--save', help='fit-only：保存先（models/nar_v1s.json など）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--stage', choices=('select', 'final', 'fit-only'), default='final',
                    help='select：選択の期間まで（検証の期間は見ない）。final：事前登録どおり検証の期間を1回見る')
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dirs = [a.urawa] + list(a.others)
    nd = NarData(merged_dir(dirs, out / '_merged'))
    target = a.venue
    have = sorted(set(nd.data.races['競馬場'].dropna()) - {''})
    venues_s = have if len(have) > 1 else None
    if a.stage == 'fit-only':
        venues = have if a.train_venues == 'all' else [target]
        ids = ids_between(nd, venues, '2000-01-01', TEST[1])
        p, beta = fit(nd, ids, SETS[a.set])
        v1.save_artifact(a.save, p, beta, {'版': Path(a.save).stem, '特徴量の組': a.set, '学習の競馬場': venues, '学習レース': len(ids),
                                          '学習期間の終わり': TEST[1], '対象': target})
        print(json.dumps({'保存': a.save, '学習レース': len(ids), '競馬場': venues}, ensure_ascii=False))
        return
    log = {'データ': {'対象': target, 'フォルダ': dirs, '集めた競馬場': have}, '使える組み合わせ': [k for k in ORDER if k.endswith('U') or venues_s]}
    sel_ids = ids_between(nd, [target], *SELECT)
    scores = {}
    for key in log['使える組み合わせ']:
        venues = [target] if key.endswith('U') else venues_s
        train = ids_between(nd, venues, '2000-01-01', SELECT_TRAIN_END)
        p, beta = fit(nd, train, SETS[key[:2]])
        per = keiba_eval_races(nd, sel_ids, predict(nd, sel_ids, p, beta))
        scores[key] = {'学習レース': len(train), '選択レース': len(sel_ids), '上位3勝ち馬率': float(per['上位3勝ち馬'].mean())}
    log['選択'] = scores
    best = max(scores.items(), key=lambda kv: (kv[1]['上位3勝ち馬率'], -ORDER.index(kv[0])))[0]
    log['選んだ組み合わせ'] = best
    # 区分の選び方の予備（検証で A が確かめられなかった場合に使う）は、選択の期間だけで決めておく
    venues = [target] if best.endswith('U') else venues_s
    p_sel, b_sel = fit(nd, ids_between(nd, venues, '2000-01-01', SELECT_TRAIN_END), SETS[best[:2]])
    pred_sel = predict(nd, sel_ids, p_sel, b_sel)
    pay_sel = payouts(nd, pred_sel, sel_ids)
    alt = {rule: tier_check(tiers(nd, pred_sel, sel_ids, rule), pay_sel)['A的中率'] if len(sel_ids) else np.nan
           for rule in ('size', 'gap', 'both')}
    log['区分の予備選択（選択の期間のA的中率）'] = alt
    log['区分の予備'] = max(('size', 'gap', 'both'), key=lambda r: (np.nan_to_num(alt[r], nan=-1), -('size', 'gap', 'both').index(r)))
    if a.stage == 'select':
        (out / 'run_log_select.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        print(json.dumps(log, ensure_ascii=False, indent=1, default=str))
        return
    # 最終：〜8/31 で学習し直して、検証の期間（浦和）で1回だけ評価
    train = ids_between(nd, venues, '2000-01-01', FINAL_TRAIN_END)
    test = ids_between(nd, [target], *TEST)
    p, beta = fit(nd, train, SETS[best[:2]])
    mine = predict(nd, test, p, beta)
    preds = {best: mine, '近走人気だけ': popularity_rank(nd, test, p), 'nar-local-v1': local_v1_rank(nd, test, FINAL_TRAIN_END),
             '参考：当日の人気': market_rank(nd, test)}
    per = pd.concat([keiba_eval_races(nd, test, pr).assign(モデル=m) for m, pr in preds.items()], ignore_index=True)
    per.to_csv(out / 'per_race_metrics.csv', index=False, encoding='utf-8-sig')
    summary = per.groupby('モデル').apply(keiba_eval.summarize, include_groups=False).reset_index()
    summary.to_csv(out / 'summary.csv', index=False, encoding='utf-8-sig')
    by = {m: per.loc[per['モデル'].eq(m)] for m in preds}
    rand = (by[best]['上位3勝ち馬'].astype(float) - 3 / by[best]['頭数'].clip(lower=3)).to_numpy()
    rng = np.random.default_rng(0)
    samp = [rand[rng.integers(0, len(rand), len(rand))].mean() for _ in range(2000)]
    log['最終'] = {'学習レース': len(train), '検証レース': len(test), '係数': dict(zip(p.features, map(float, beta))),
                 f'{best}−近走人気だけ': keiba_eval.bootstrap_diff(by[best], by['近走人気だけ']),
                 f'{best}−nar-local-v1': keiba_eval.bootstrap_diff(by[best], by['nar-local-v1']),
                 f'{best}−無作為': {'差': float(rand.mean()), '下限': float(np.percentile(samp, 2.5)), '上限': float(np.percentile(samp, 97.5))}}
    log['判定'] = f'{target}の主予測を替える' if log['最終'][f'{best}−近走人気だけ']['下限'] > 0 else '替えない（根拠不足）'
    pay = payouts(nd, mine, test)
    t = tiers(nd, mine, test, 'both')
    tt = tier_table(t, pay)
    tt.to_csv(out / 'tier_table.csv', index=False, encoding='utf-8-sig')
    check = tier_check(t, pay)
    log['信頼度（今の区分）'] = check
    log['信頼度の判定'] = '地方で確かめ済み' if check.get('下限', -1) > 0 else '未確認'
    if log['信頼度の判定'] == '未確認' and log['区分の予備'] != 'both':
        t2 = tiers(nd, mine, test, log['区分の予備'])
        tier_table(t2, pay).to_csv(out / 'tier_table_alt.csv', index=False, encoding='utf-8-sig')
        c2 = tier_check(t2, pay)
        log['信頼度（予備の区分）'] = {'選び方': log['区分の予備'], **c2}
        if c2.get('下限', -1) > 0:
            log['信頼度の判定'] = f"地方で確かめ済み（予備の区分：{log['区分の予備']}）"
    log['信頼度別'] = tt.to_dict('records')
    if log['判定'].endswith('主予測を替える'):
        all_train = ids_between(nd, venues, '2000-01-01', TEST[1])
        pf, bf = fit(nd, all_train, SETS[best[:2]])
        name = 'nar_v1.json' if target == '浦和' else f'nar_{target}.json'
        v1.save_artifact(Path(__file__).resolve().parents[1] / 'models' / name, pf, bf,
                         {'版': name[:-5], '組み合わせ': best, '学習レース': len(all_train), '対象': target, '採用': True})
        log['保存'] = f'models/{name}'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(summary.to_string())
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


def keiba_eval_races(nd, ids, pred):
    import accuracy_experiment as ax
    return ax.evaluate(nd.data, ids, pred)


if __name__ == '__main__':
    main()
