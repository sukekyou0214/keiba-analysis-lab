"""地方の結果照合：レース前に保存した順位（複数の方式）を、結果ページの着順と実際の払戻で採点する。

照合はレース前に保存した予想だけを使う。保存した時刻（--saved-at）より前に発走したレースは対象外。
出す数字：上位3頭に勝ち馬、予想1位の勝ち・3着以内、上位5頭ボックス（＝三連単5頭ボックス）の的中、
三連複（10点・1,000円）と三連単（60点・6,000円）の回収率、信頼度別、参考として当日の人気上位5頭のボックス。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_results(work):
    work = Path(work)
    out = {}
    for f in sorted(work.glob('info_*.json')):
        info = json.loads(f.read_text(encoding='utf-8'))
        rid = str(info['レースID'])
        res_path = work / 'results' / f'{rid}.csv'
        if not res_path.exists():
            continue
        res = pd.read_csv(res_path, dtype=str)
        res['着'] = pd.to_numeric(res['着順原文'], errors='coerce')
        res['番'] = pd.to_numeric(res['馬番'], errors='coerce')
        res['人'] = pd.to_numeric(res['人気'], errors='coerce')
        pay_path = work / 'results' / f'{rid}_払戻.json'
        pays = json.loads(pay_path.read_text(encoding='utf-8')) if pay_path.exists() else []
        out[rid] = (info, res, {(p['券種'], p['組番']): p['払戻'] for p in pays})
    return out


def score_race(order, res, pay):
    top3 = res.sort_values('着').loc[res['着'].le(3), '番'].dropna().astype(int).tolist()
    ok3 = len(top3) == 3
    box = ok3 and set(top3) <= set(order[:5])
    first = res.loc[res['番'].eq(order[0]), '着'] if order else pd.Series(dtype=float)
    first = float(first.iloc[0]) if len(first) and pd.notna(first.iloc[0]) else None
    fuku = pay.get(('三連複', '-'.join(map(str, sorted(top3))))) if ok3 else None
    tan = pay.get(('三連単', '-'.join(map(str, top3)))) if ok3 else None
    return {'着順': '-'.join(map(str, top3)), '上位5頭': '・'.join(map(str, order[:5])),
            '上位3に勝ち馬': bool(top3) and top3[0] in order[:3], '予想1位の着順': first,
            '5頭BOX的中': bool(box), '三連複払戻': (fuku or 0) if box else 0, '三連単払戻': (tan or 0) if box else 0,
            '払戻あり': fuku is not None and tan is not None}


def check(pred, results, rank_cols, tiers=None, saved_at=None):
    pred = pred.copy()
    if 'レースID' not in pred:  # 古い形式（11/24）：一覧表記で結びつける
        by_listing = {str(info.get('一覧表記', '')): rid for rid, (info, _, _) in results.items()}
        pred['レースID'] = pred['レース'].map(by_listing)
    pred['レースID'] = pred['レースID'].astype(str)
    tier_of = dict(zip(tiers['レースID'].astype(str), tiers['信頼度'])) if tiers is not None else {}
    rows = []
    for rid, (info, res, pay) in results.items():
        g = pred.loc[pred['レースID'].eq(rid)]
        if g.empty:
            continue
        post = str(info.get('発走', g['発走'].iloc[0] if '発走' in g else ''))
        excluded = bool(saved_at) and post < saved_at
        pop = res.dropna(subset=['人']).sort_values('人')['番'].astype(int).tolist()
        base = {'レースID': rid, 'R': str(info.get('一覧表記', '')).split()[0], '発走': post, '頭数': int(res['番'].notna().sum()),
                '信頼度': tier_of.get(rid, ''), '対象外（保存前に発走）': excluded}
        for col in rank_cols + ['人気']:
            order = pop if col == '人気' else g.sort_values(col)['馬番'].astype(int).tolist()
            rows.append(base | {'方式': '参考：当日の人気' if col == '人気' else col} | score_race(order, res, pay))
    return pd.DataFrame(rows)


def summarize(per):
    d = per.loc[~per['対象外（保存前に発走）']]
    out = []
    for (m, t), g in [((m, '全レース'), g) for m, g in d.groupby('方式', sort=False)] + \
                     [((m, t), g) for (m, t), g in d.groupby(['方式', '信頼度'], sort=False) if t]:
        n = len(g)
        out.append({'方式': m, '信頼度': t, 'レース': n, '上位3に勝ち馬': int(g['上位3に勝ち馬'].sum()),
                    '予想1位の勝ち': int((g['予想1位の着順'] == 1).sum()), '予想1位の3着内': int((g['予想1位の着順'] <= 3).sum()),
                    '5頭BOX的中': int(g['5頭BOX的中'].sum()),
                    '三連複回収率': g['三連複払戻'].sum() / (n * 1000) if n else None,
                    '三連単回収率': g['三連単払戻'].sum() / (n * 6000) if n else None})
    return pd.DataFrame(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred', required=True, help='レース前に保存した地方参考順位の CSV')
    ap.add_argument('--tiers', help='信頼度区分の CSV')
    ap.add_argument('--work', required=True, help='結果の取得フォルダ（info_*.json と results/）')
    ap.add_argument('--rank-cols', nargs='+', default=['順位'], help='採点する順位の列（例：順位 C3U順位）')
    ap.add_argument('--saved-at', help='予想を保存した時刻 HH:MM（これより前に発走したレースは対象外）')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    pred = pd.read_csv(a.pred, dtype={'レースID': str})
    tiers = pd.read_csv(a.tiers, dtype={'レースID': str}) if a.tiers else None
    per = check(pred, load_results(a.work), a.rank_cols, tiers, a.saved_at)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    per.to_csv(out, index=False, encoding='utf-8-sig')
    s = summarize(per)
    s.to_csv(out.with_name(out.stem + '_集計.csv'), index=False, encoding='utf-8-sig')
    print(per.to_string())
    print(s.to_string())


if __name__ == '__main__':
    main()
