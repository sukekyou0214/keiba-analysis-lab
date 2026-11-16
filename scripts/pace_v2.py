"""展開予測 v2 の選択と検証（docs/accuracy/00 の 2026-11-16 追記「展開 v2」どおり）。

学習＝2025年秋＋2026年秋、検証＝2026年夏（1回だけ）。候補 P1（しきい値の選び直し）・P2（多項ロジット）。
使い方: python scripts/pace_v2.py --autumn2025 DIR --autumn2026 DIR --summer2026 DIR --out DIR [--save models/pace_v2.json]
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
import keiba_pace_v1 as pv  # noqa: E402

LABELS = ['S', 'M', 'H']
FEATURES = ['先行圧力', '前に行く頭数', '根拠ありの割合', '出走頭数', '距離']


def race_rows(path):
    import accuracy_experiment as ax
    data = ax.Data(keiba_eval.Backtest(path))
    published = data.bt.pace.loc[data.bt.pace['状態'].eq('成功')].set_index('レースID')['公表ペース'].map(pv.LABELS)
    rows = []
    for rid in data.races.index:
        if rid not in published.index or data.debut(rid) or rid not in data.complete:
            continue
        info = data.races.loc[rid]
        res, positions = pv.predict_pace(data.bt.field(rid), data.by_horse, info['開催日'], pv.PaceParams())
        pressure, front, known, total = pv.race_pressure(positions, pv.PaceParams())
        rows.append({'レースID': rid, '公表': published[rid], '先行圧力': pressure, '前に行く頭数': front,
                     '根拠ありの割合': known / max(total, 1), '出走頭数': total, '距離': float(info['距離']),
                     '保留': res['独自判定'] is None})
    return pd.DataFrame(rows)


def p1_fit(train):
    t = train.loc[~train['保留']]
    acc, low, high = pv.choose_thresholds(t['先行圧力'].to_numpy(), t['公表'].to_numpy())
    return {'種類': 'しきい値', 'low': low, 'high': high}


def p1_predict(model, frame):
    p = frame['先行圧力'].to_numpy()
    pred = np.where(p >= model['high'], 'H', np.where(p <= model['low'], 'S', 'M'))
    return np.where(frame['保留'].to_numpy(), None, pred)


def p2_fit(train, l2=1.0, iters=500, lr=0.5):
    t = train.loc[~train['保留']]
    x = t[FEATURES].to_numpy(float)
    mean, std = x.mean(0), x.std(0)
    std[std == 0] = 1.0
    z = np.c_[np.ones(len(x)), (x - mean) / std]
    y = np.array([LABELS.index(v) for v in t['公表']])
    onehot = np.eye(3)[y]
    w = np.zeros((z.shape[1], 3))
    for _ in range(iters):
        s = z @ w
        p = np.exp(s - s.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        grad = z.T @ (p - onehot) / len(z) + l2 * np.r_[np.zeros((1, 3)), w[1:]] / len(z)
        w -= lr * grad
    return {'種類': '多項ロジット', '特徴量': FEATURES, '平均': mean.tolist(), '標準偏差': std.tolist(), '係数': w.tolist()}


def p2_predict(model, frame):
    x = frame[model['特徴量']].to_numpy(float)
    z = np.c_[np.ones(len(x)), (x - np.array(model['平均'])) / np.array(model['標準偏差'])]
    pred = np.array(LABELS)[(z @ np.array(model['係数'])).argmax(1)]
    return np.where(frame['保留'].to_numpy(), None, pred)


def accuracy(pred, truth):
    return float(np.mean([p == t for p, t in zip(pred, truth)]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--autumn2025', required=True)
    ap.add_argument('--autumn2026', required=True)
    ap.add_argument('--summer2026', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--save')
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    a25, a26, s26 = race_rows(args.autumn2025), race_rows(args.autumn2026), race_rows(args.summer2026)
    log = {'レース数': {'2025秋': len(a25), '2026秋': len(a26), '2026夏': len(s26)}}
    # 選択：2025年秋で学習 → 2026年秋の一致率
    sel = {'P1': accuracy(p1_predict(p1_fit(a25), a26), a26['公表']),
           'P2': accuracy(p2_predict(p2_fit(a25), a26), a26['公表'])}
    log['選択（2026秋の一致率）'] = sel
    chosen = 'P2' if sel['P2'] > sel['P1'] else 'P1'
    log['選択'] = chosen
    train = pd.concat([a25, a26], ignore_index=True)
    model = p2_fit(train) if chosen == 'P2' else p1_fit(train)
    pred = (p2_predict if chosen == 'P2' else p1_predict)(model, s26)
    majority = train['公表'].mode().iloc[0]
    hit = np.array([p == t for p, t in zip(pred, s26['公表'])], float)
    base = (s26['公表'] == majority).to_numpy(float)
    v1_pred = p1_predict({'low': pv.load_params().low, 'high': pv.load_params().high}, s26)
    rng = np.random.default_rng(0)
    diff = hit - base
    samples = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)]
    log['2026夏'] = {'レース数': len(s26), 'v2の一致率': float(hit.mean()), '常に' + majority + 'の一致率': float(base.mean()),
                   '現行pace-v1の一致率': accuracy(v1_pred, s26['公表']),
                   '差（v2−常に最多）': float(diff.mean()), '下限': float(np.percentile(samples, 2.5)),
                   '上限': float(np.percentile(samples, 97.5)),
                   '公表の分布': s26['公表'].value_counts().to_dict(),
                   'v2の予測分布': pd.Series(pred).value_counts(dropna=False).to_dict()}
    log['判定'] = '画面の展開予測を v2 に替える' if log['2026夏']['下限'] > 0 else '替えない（根拠不足と明記）'
    model.update({'版': 'pace-v2', '学習期間': '2025-09-13〜11-30・2026-09-12〜11-08', '最多判定': majority,
                  '検証': {k: log['2026夏'][k] for k in ('レース数', 'v2の一致率', '常に' + majority + 'の一致率', '下限', '上限')}})
    if args.save:
        Path(args.save).write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding='utf-8')
    s26.assign(v2=pred, v1=v1_pred).to_csv(out / 'summer_predictions.csv', index=False, encoding='utf-8-sig')
    (out / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
