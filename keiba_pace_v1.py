"""展開予測の基準（ルール方式 v1）。

- 各馬の位置取り：直近（対象日前）の実出走5走について、通過順の最初の値 ÷ 頭数（0に近いほど前）。
  通過順の最初の値は「最初に記録されたコーナー」であり、発走直後の位置とは限らない。
  4角順位は使わない。距離・コースで最初のコーナーの位置は違うので、値は位置の目安（範囲）として扱う。
- 先行圧力：位置の目安が 0.25 以下の馬（前に行く傾向）の頭数と、その確からしさ（走数）から作る。
- 判定：圧力が高ければ H、低ければ S、それ以外は M。しきい値は学習期間（inner）で決める。
- 公表のS/M/H（netkeibaのラップ欄の表示）は出典つきで別に保持し、この独自判定と混ぜない。
- 判定が「いつも最多の判定を答える」単純予測を外側期間で上回らない場合、画面では「暫定・根拠不足」と表示する。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from keiba_model_data import before, first_corner, ran

PACE_VERSION = 'pace-v1'
LABELS = {'スロー寄り': 'S', 'ミドル寄り': 'M', 'ハイ寄り': 'H'}


@dataclass
class PaceParams:
    front: float = 0.25      # 前に行く傾向とみなす位置の目安
    window: int = 5
    low: float = 1.5         # 先行圧力がこれ以下なら S
    high: float = 3.0        # これ以上なら H


def horse_position(history, cutoff, params):
    """1頭の位置の目安（平均・範囲）と根拠の走数。"""
    if history is None or history.empty or '状態' not in history:
        return {'位置目安': np.nan, '位置下限': np.nan, '位置上限': np.nan, '位置根拠走数': 0}
    h = ran(before(history, cutoff)).head(params.window)
    ratios = []
    for passing, field in zip(h['通過_原文'], h['頭数']):
        corner = first_corner(passing)
        if corner and field and field > 1:
            ratios.append(min(corner / field, 1.0))
    if not ratios:
        return {'位置目安': np.nan, '位置下限': np.nan, '位置上限': np.nan, '位置根拠走数': 0}
    return {'位置目安': float(np.mean(ratios)), '位置下限': float(np.min(ratios)), '位置上限': float(np.max(ratios)),
            '位置根拠走数': len(ratios)}


def race_pressure(positions, params):
    """先行圧力：前に行く傾向の馬の数（根拠の走数で重み：1走0.4、2走0.7、3走以上1.0）。"""
    p = positions.dropna(subset=['位置目安'])
    weight = p['位置根拠走数'].map(lambda n: 0.4 if n == 1 else 0.7 if n == 2 else 1.0)
    front = p['位置目安'].le(params.front)
    return float(weight[front].sum()), int(front.sum()), int(len(p)), int(len(positions))


def predict_pace(field, history_by_horse, cutoff, params):
    rows = []
    for hid in field['馬ID']:
        rows.append({'馬ID': hid, **horse_position(history_by_horse.get(hid, pd.DataFrame(columns=['日付'])), cutoff, params)})
    positions = pd.DataFrame(rows)
    pressure, front, known, total = race_pressure(positions, params)
    label = 'H' if pressure >= params.high else 'S' if pressure <= params.low else 'M'
    reason = f'前に行く傾向の馬 {front}頭（位置の根拠あり {known}/{total}頭）、先行圧力 {pressure:.1f}'
    if known < max(3, total // 2):
        label, reason = None, reason + '。位置の根拠が不足のため判定保留'
    return {'独自判定': label, '先行圧力': pressure, '根拠': reason, '版': PACE_VERSION}, positions


def choose_thresholds(pressures, published):
    """学習期間で、判定一致率が最大のしきい値（low<high）を選ぶ。同率は小さい値を優先（再現可能）。"""
    best = None
    for low in np.arange(0.0, 4.01, 0.5):
        for high in np.arange(low + 0.5, 6.01, 0.5):
            pred = np.where(pressures >= high, 'H', np.where(pressures <= low, 'S', 'M'))
            acc = float(np.mean(pred == published))
            if best is None or acc > best[0] + 1e-12:
                best = (acc, float(low), float(high))
    return best


def from_prepared(data):
    """本体の保存データ（prepare 済み）を、位置取りの計算に必要な形へ。
    本体データは取消・除外と中止を区別できない（着順が数値でない走）ので、着順が数値の走だけを使う。"""
    if data is None or data.empty:
        return pd.DataFrame(columns=['馬ID', '日付', '状態', '通過_原文', '頭数'])
    d = pd.DataFrame({'馬ID': data['馬ID'].astype(str), '日付': pd.to_datetime(data['日付'], errors='coerce'),
                      '通過_原文': data.get('通過順', pd.Series('', index=data.index)).fillna('').astype(str),
                      '頭数': pd.to_numeric(data['頭数'], errors='coerce')})
    d['状態'] = pd.to_numeric(data['着順_数値'], errors='coerce').notna().map({True: '完走', False: '不明'})
    return d.sort_values(['馬ID', '日付'], ascending=[True, False])


STATUS_NOTE = '展開予測は暫定の目安です。'


def describe(data, horses, race_date, status_note=None, distance=None):
    """主画面用：想定展開の暫定判定と根拠。判定できない場合は保留。
    models/pace_v2.json があり距離が分かれば pace-v2（多項ロジット）、なければ pace-v1（しきい値）。"""
    h = from_prepared(data)
    by = {k: g for k, g in h.groupby('馬ID')}
    field = pd.DataFrame({'馬ID': [str(x) for x in horses]})
    res, positions = predict_pace(field, by, race_date, load_params())
    model = load_v2()
    if model is not None and distance and res['独自判定'] is not None:
        pressure, front, known, total = race_pressure(positions, PaceParams())
        res['独自判定'] = v2_label(model, pressure, front, known, total, float(distance))
        res['版'] = 'pace-v2'
    names = {'S': 'スロー寄り', 'M': 'ミドル寄り', 'H': 'ハイ寄り'}
    head = f"想定展開（暫定）：{names[res['独自判定']]}" if res['独自判定'] else '想定展開：判定保留'
    return f"{head}。{res['根拠']}。{status_note or STATUS_NOTE}最初に記録されたコーナーの位置を目安にしており、発走直後の位置とは限りません。"


V2_PATH = __import__('pathlib').Path(__file__).resolve().parent / 'models' / 'pace_v2.json'
V2_LABELS = ['S', 'M', 'H']


def load_v2(path=None):
    import json
    from pathlib import Path
    try:
        data = json.loads(Path(path or V2_PATH).read_text(encoding='utf-8'))
        return data if data.get('種類') == '多項ロジット' else None
    except (OSError, ValueError):
        return None


def v2_label(model, pressure, front, known, total, distance):
    """pace-v2：先行圧力・前に行く頭数・根拠ありの割合・出走頭数・距離の多項ロジットで最も高い判定。"""
    values = {'先行圧力': pressure, '前に行く頭数': front, '根拠ありの割合': known / max(total, 1), '出走頭数': total,
              '距離': distance}
    x = np.array([values[f] for f in model['特徴量']], float)
    z = np.r_[1.0, (x - np.array(model['平均'])) / np.array(model['標準偏差'])]
    return V2_LABELS[int((z @ np.array(model['係数'])).argmax())]


PARAMS_PATH = __import__('pathlib').Path(__file__).resolve().parent / 'models' / 'pace_v1.json'


def load_params(path=None):
    """inner期間で選んだしきい値（models/pace_v1.json）。無ければ既定値（未検証）。"""
    import json
    from pathlib import Path
    path = Path(path or PARAMS_PATH)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return PaceParams(low=float(data['low']), high=float(data['high']))
    except (OSError, ValueError, KeyError):
        return PaceParams()
