"""人気との関係（docs/accuracy/00 の 2026-11-26 追記「M1・M2」どおり）。

M1：レース単位の条件付きロジットで ①log(確定単勝オッズ) と ②①＋ラボの点（レース内で標準化）を比べ、
    検証の期間の1レースあたりの対数尤度の差 ②−① の95%区間を出す。
M2：ラボの順位 k 位以内・人気 m 番人気以下の馬を単勝／複勝100円で買った回収率。選択の期間で18通りから1つ選び、検証の期間で1回だけ確かめる。
使い方:
  python scripts/market_test.py --kind jra --select-backtest DIR [DIR ...] --test-backtest DIR --out DIR --stage select|final
  python scripts/market_test.py --kind nar --select-backtest DIR [DIR ...] --test-backtest DIR --venue 浦和,大井,川崎,船橋 --out DIR --stage select|final
点数・回収率は過去の検証の値で、当たる確率ではない。
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

ROOT = Path(__file__).resolve().parents[1]
KS, MS, BETS = (1, 2, 3), (4, 6, 8), ('単勝', '複勝')
CANDIDATES = [(b, k, m) for b in BETS for k in KS for m in MS]
NAR_SELECT, NAR_TEST = ('2026-09-01', '2026-11-24'), ('2026-03-01', '2026-04-30')
NAR_SELECT_TRAIN_END, NAR_TEST_TRAIN_END = '2026-08-31', '2026-02-28'


# ---------- 読み取り ----------
def odds_value(x):
    """結果ページの「単勝」（確定単勝オッズ）。数字でなければ NaN（取消・除外など）。"""
    try:
        v = float(str(x).replace(',', '').strip())
    except (TypeError, ValueError):
        return np.nan
    return v if v > 0 else np.nan


def payout_table(info):
    """払戻表 → {('単勝'|'複勝', 馬番): 円}。払戻表がなければ None。"""
    pays = info.get('払戻')
    if not pays:
        return None
    return {(p['券種'], str(p['組番']).strip()): float(p['払戻']) for p in pays if p.get('券種') in BETS}


def standardize(s):
    s = pd.to_numeric(pd.Series(s), errors='coerce').astype(float)
    if s.notna().sum() == 0:
        return s
    s = s.fillna(s.min())
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd > 0 else s * 0.0


def race_rows(rid, field, lab, info):
    """1レースの表（実出走の馬だけ）：馬番・勝ち・人気・オッズ・ラボの点（標準化）・ラボの順位。lab：馬ID→(点, 順位)。"""
    f = field.loc[field['実出走']].copy()
    f['馬ID'] = f['馬ID'].astype(str)
    out = pd.DataFrame({'レースID': rid, '馬ID': f['馬ID'].to_numpy(), '馬番': f['馬番'].astype(str).str.strip().to_numpy(),
                        '勝ち': (pd.to_numeric(f['着順'], errors='coerce') == 1).to_numpy(),
                        '人気': pd.to_numeric(f['人気'], errors='coerce').to_numpy(),
                        'オッズ': f['単勝'].map(odds_value).to_numpy()})
    out['点'] = out['馬ID'].map(lambda h: lab.get(h, (np.nan, np.nan))[0])
    out['ラボ順位'] = out['馬ID'].map(lambda h: lab.get(h, (np.nan, np.nan))[1])
    out['点z'] = standardize(out['点']).to_numpy()
    out['払戻あり'] = payout_table(info) is not None
    return out


# ---------- M1 ----------
def m1_races(tab):
    """M1 に使えるレース：実出走の全馬にオッズ、勝ち馬あり、ラボの点がどれかの馬にある。"""
    ok, dropped = [], {'オッズなし': 0, '勝ち馬なし': 0, 'ラボの点なし': 0}
    for rid, g in tab.groupby('レースID', sort=False):
        if g['オッズ'].isna().any():
            dropped['オッズなし'] += 1
        elif not g['勝ち'].any():
            dropped['勝ち馬なし'] += 1
        elif g['点'].isna().all():
            dropped['ラボの点なし'] += 1
        else:
            ok.append(g)
    return ok, dropped


def design(g, with_lab):
    x = [np.log(g['オッズ'].to_numpy(float))]
    if with_lab:
        x.append(g['点z'].to_numpy(float))
    return np.column_stack(x)


def race_loglik(x, win, beta):
    s = x @ beta
    s = s - s.max()
    logp = s - np.log(np.exp(s).sum())
    return float(logp[win].mean())


def fit_clogit(races, with_lab, iters=50):
    """条件付きロジットの最尤推定（ニュートン法、L2 なし）。races：表のリスト。"""
    data = [(design(g, with_lab), g['勝ち'].to_numpy(bool)) for g in races]
    beta = np.zeros(data[0][0].shape[1])
    for _ in range(iters):
        grad, hess = np.zeros_like(beta), np.zeros((len(beta), len(beta)))
        for x, win in data:
            s = x @ beta
            p = np.exp(s - s.max())
            p /= p.sum()
            w = win / win.sum()
            mx = p @ x
            grad += w @ x - mx
            hess -= (x * p[:, None]).T @ x - np.outer(mx, mx)
        step = np.linalg.solve(hess, grad)
        beta = beta - step
        if np.abs(step).max() < 1e-9:
            break
    return beta


def top3_winner(races, beta, with_lab):
    hits = []
    for g in races:
        s = design(g, with_lab) @ beta
        order = np.argsort(-s, kind='stable')[:3]
        hits.append(bool(g['勝ち'].to_numpy(bool)[order].any()))
    return float(np.mean(hits)) if hits else np.nan


def boot_mean(d, seed=0, n=2000):
    d = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    s = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)]
    return {'レース数': len(d), '差': float(d.mean()), '下限': float(np.percentile(s, 2.5)), '上限': float(np.percentile(s, 97.5))}


def m1_eval(races, b1, b2):
    d = [race_loglik(design(g, True), g['勝ち'].to_numpy(bool), b2) - race_loglik(design(g, False), g['勝ち'].to_numpy(bool), b1)
         for g in races]
    return boot_mean(d)


# ---------- M2 ----------
def discord(g, k, m):
    """ラボの順位 k 位以内・人気 m 番人気以下（人気の数字が m 以上）の馬。"""
    r, p = pd.to_numeric(g['ラボ順位'], errors='coerce'), pd.to_numeric(g['人気'], errors='coerce')
    return g.loc[r.le(k) & p.ge(m)]


def race_bets(tab, infos, rule):
    """レースごとの賭け金と払戻（rule：('単勝'|'複勝', k, m) または ('1番人気'|'ラボ1位', None, None)）。払戻表のあるレースだけ。"""
    bet, k, m = rule
    rows = []
    for rid, g in tab.groupby('レースID', sort=False):
        pay = payout_table(infos[rid])
        if pay is None:
            continue
        if bet == '1番人気':
            picks, kind = g.loc[pd.to_numeric(g['人気'], errors='coerce').eq(1)], '単勝'
        elif bet == 'ラボ1位':
            picks, kind = g.loc[pd.to_numeric(g['ラボ順位'], errors='coerce').eq(1)], '単勝'
        else:
            picks, kind = discord(g, k, m), bet
        ret = sum(pay.get((kind, str(n)), 0.0) for n in picks['馬番'])
        rows.append({'レースID': rid, '賭け金': 100.0 * len(picks), '払戻': float(ret), '点数': len(picks)})
    return pd.DataFrame(rows, columns=['レースID', '賭け金', '払戻', '点数'])


def recovery(rb):
    stake = rb['賭け金'].sum()
    return float(rb['払戻'].sum() / stake) if stake else np.nan


def boot_recovery(rb, seed=0, n=2000):
    st, rt = rb['賭け金'].to_numpy(float), rb['払戻'].to_numpy(float)
    rng = np.random.default_rng(seed)
    s = []
    for _ in range(n):
        i = rng.integers(0, len(st), len(st))
        s.append(rt[i].sum() / st[i].sum() if st[i].sum() else np.nan)
    return {'レース数': len(rb), '対象の馬': int(rb['点数'].sum()), '回収率': recovery(rb),
            '下限': float(np.nanpercentile(s, 2.5)), '上限': float(np.nanpercentile(s, 97.5))}


def choose(table):
    """選択：回収率が最も高い候補（同じなら対象の馬が多い → 単勝→複勝 → k・m の小さい順）。table：候補→{回収率, 対象の馬}。"""
    def key(c):
        v = table[c]
        return (np.nan_to_num(v['回収率'], nan=-1.0), v['対象の馬'], -BETS.index(c[0]), -c[1], -c[2])
    return max(table, key=key)


# ---------- ラボの点 ----------
def jra_period(path, cache):
    import accuracy_experiment as ax
    import keiba_eval
    bt = keiba_eval.Backtest(path)
    data = ax.Data(bt)
    ids = [r for r in data.races.index if r in data.complete and not data.debut(r)]
    infos = {json.loads(p.read_text(encoding='utf-8'))['レースID']: json.loads(p.read_text(encoding='utf-8'))
             for p in (Path(path) / 'races').glob('*.json')}
    cache = Path(cache)
    if cache.exists():
        lab = pd.read_csv(cache, dtype={'レースID': str, '馬ID': str})
    else:
        lab = pd.concat([keiba_eval.frozen_rank(bt, data.races.loc[r].to_dict() | {'レースID': r}, 'common') for r in ids],
                        ignore_index=True)
        lab.to_csv(cache, index=False, encoding='utf-8-sig')
    tabs = []
    for rid, g in lab.groupby('レースID', sort=False):
        if rid not in ids:
            continue
        labd = {str(h): (s, r) for h, s, r in zip(g['馬ID'], pd.to_numeric(g['点数'], errors='coerce'),
                                                   pd.to_numeric(g['順位'], errors='coerce'))}
        tabs.append(race_rows(rid, bt.field(rid), labd, infos[rid]))
    return pd.concat(tabs, ignore_index=True), infos


def local_v1_scores(nd, ids, train_end):
    """nar-local-v1 の点（nar_backtest_test.local_v1_rank と同じ計算で、点も返す）。"""
    import nar_backtest_test as nb
    import nar_local as nl
    rows = nl.training_rows(nd.h.loc[nd.h['日付'] <= train_end])
    w = nl.fit(nl.standardize(rows, rows), rows['3着内'].to_numpy(float))
    out = {}
    for rid in ids:
        f = nd.features(rid, nb.params(nb.BASE))
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
        out[rid] = rank_dict(g['馬ID'], s)
    return out


def rank_dict(horses, s):
    g = pd.DataFrame({'馬ID': pd.Series(horses).astype(str).to_numpy(), '_s': np.asarray(s, float)})
    g = g.sort_values(['_s', '馬ID'], ascending=[False, True])
    g['順位'] = range(1, len(g) + 1)
    return {h: (s, r) for h, s, r in zip(g['馬ID'], g['_s'], g['順位'])}


def nar_period(dirs, venues, span, stage_kind, out):
    import keiba_model_v1 as v1
    import nar_backtest_test as nb
    nd = nb.NarData(nb.merged_dir(dirs, Path(out) / f'_merged_{stage_kind}'))
    tabs, used = [], {}
    for venue in venues:
        ids = nb.ids_between(nd, [venue], *span)
        if not ids:
            used[venue] = {'レース': 0}
            continue
        if venue == '大井':
            if stage_kind == 'select':
                p, beta = nb.fit(nd, nb.ids_between(nd, ['大井'], '2000-01-01', NAR_SELECT_TRAIN_END), nb.SETS['C2'])
                how = 'C2U（〜8/31 の大井で学習）'
            else:
                p, beta, _ = v1.load_artifact(ROOT / 'models' / 'nar_大井.json')
                how = 'models/nar_大井.json'
            pred = nb.predict(nd, ids, p, beta)
            labs = {rid: rank_dict(g['馬ID'], g['_s']) for rid, g in pred.groupby('レースID')}
        else:
            end = NAR_SELECT_TRAIN_END if stage_kind == 'select' else NAR_TEST_TRAIN_END
            labs = local_v1_scores(nd, ids, end)
            how = f'nar-local-v1（〜{end} の走で学習）'
        for rid in ids:
            tabs.append(race_rows(rid, nd.data.bt.field(rid), labs.get(rid, {}), nd.infos[rid]).assign(競馬場=venue))
        used[venue] = {'レース': len(ids), '主予測': how}
    return pd.concat(tabs, ignore_index=True), nd.infos, used


# ---------- 実行 ----------
def m2_table(tab, infos):
    return {c: {k: v for k, v in boot_recovery(race_bets(tab, infos, c)).items() if k in ('回収率', '対象の馬', 'レース数')}
            for c in CANDIDATES}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=('jra', 'nar'), required=True)
    ap.add_argument('--select-backtest', nargs='+', required=True)
    ap.add_argument('--test-backtest')
    ap.add_argument('--venue', default='浦和,大井,川崎,船橋')
    ap.add_argument('--out', required=True)
    ap.add_argument('--stage', choices=('select', 'final'), default='select',
                    help='select：選択の期間だけ（検証の期間は見ない）。final：事前登録どおり検証の期間を1回見る')
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    venues = a.venue.split(',')
    if a.kind == 'jra':
        parts = [jra_period(p, out / f'_lab_select_{i}.csv') for i, p in enumerate(a.select_backtest)]
        sel = pd.concat([t for t, _ in parts], ignore_index=True)
        sel_infos = {k: v for _, i in parts for k, v in i.items()}
        log = {'種類': 'JRA 芝（新馬戦以外）', '選択の期間': a.select_backtest}
    else:
        sel, sel_infos, used = nar_period(a.select_backtest, venues, NAR_SELECT, 'select', out)
        log = {'種類': '南関東4場', '選択の期間': NAR_SELECT, '選択の主予測': used}
    races, dropped = m1_races(sel)
    b1, b2 = fit_clogit(races, False), fit_clogit(races, True)
    log['M1 選択'] = {'レース': len(races), '除いたレース': dropped, '①の係数': b1.tolist(), '②の係数': b2.tolist(),
                     '①上位3勝ち馬率': top3_winner(races, b1, False), '②上位3勝ち馬率': top3_winner(races, b2, True)}
    with_pay = sel.loc[sel['払戻あり'], 'レースID'].nunique()
    log['M2 選択'] = {'払戻表のあるレース': int(with_pay), '全レース': int(sel['レースID'].nunique())}
    if with_pay == 0:
        raise SystemExit('選択の期間に払戻表のあるレースがない。計算する前に止める。')
    table = m2_table(sel.loc[sel['払戻あり']], sel_infos)
    best = choose(table)
    log['M2 選択']['候補'] = {f'{b} k={k} m={m}': v for (b, k, m), v in table.items()}
    log['M2 選択']['選んだ候補'] = list(best)
    log['M2 選択']['比べる相手'] = {n: boot_recovery(race_bets(sel, sel_infos, (n, None, None)))['回収率'] for n in ('1番人気', 'ラボ1位')}
    if a.stage == 'select':
        (out / 'run_log_select.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        print(json.dumps(log, ensure_ascii=False, indent=1, default=str))
        return log
    # ---- 検証の期間（1回だけ） ----
    if a.kind == 'jra':
        test, test_infos = jra_period(a.test_backtest, out / '_lab_test.csv')
    else:
        test, test_infos, used_t = nar_period([a.test_backtest], venues, NAR_TEST, 'test', out)
        log['検証の主予測'] = used_t
    t_races, t_dropped = m1_races(test)
    log['M1 検証'] = {'レース': len(t_races), '除いたレース': t_dropped, '②−①（1レースあたりの対数尤度）': m1_eval(t_races, b1, b2),
                     '①上位3勝ち馬率': top3_winner(t_races, b1, False), '②上位3勝ち馬率': top3_winner(t_races, b2, True)}
    log['M1 判定'] = 'ラボの点は人気に情報を足している' if log['M1 検証']['②−①（1レースあたりの対数尤度）']['下限'] > 0 else '足しているとは言えない'
    if a.kind == 'nar':
        log['M1 検証']['場ごと（記録だけ）'] = {v: m1_eval([g for g in t_races if g['競馬場'].iloc[0] == v], b1, b2)
                                         for v in venues if any(g['競馬場'].iloc[0] == v for g in t_races)}
    if not test['払戻あり'].any():
        log['M2 検証'] = '検証の期間に払戻表がない。計算せず止める。'
    else:
        tp = test.loc[test['払戻あり']]
        res = boot_recovery(race_bets(tp, test_infos, best))
        log['M2 検証'] = {'選んだ候補': list(best), **res,
                         '比べる相手': {n: boot_recovery(race_bets(tp, test_infos, (n, None, None))) for n in ('1番人気', 'ラボ1位')}}
        log['M2 判定'] = '確かめた穴候補の規則（表示する）' if res['下限'] > 1.0 else '表示しない（記録だけ）'
    from keiba_registry import code_version
    log['コード版'] = code_version()
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))
    return log


if __name__ == '__main__':
    main()
