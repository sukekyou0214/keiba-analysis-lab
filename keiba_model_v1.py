"""説明可能な基準モデル（v1）：少数の特徴量＋レース内の条件付きロジット。

- 特徴量はすべて対象レースの開催日より前の戦績から作る（keiba_model_data.before）。
- 率の特徴量は分子／分母を必ず残し、縮小推定：(成功数 + k×基準率) / (標本数 + k)。
  基準率と k は学習期間の行だけで決める。推定値は「推定」であり、取得済み実績として保存しない。
- 欠損（芝の比較可能な走がない等）は学習期間の中央値で補い、欠損フラグを特徴量として持つ。
  補った馬は判定に「推定（芝実績なし）」等と表示し、機械的に最下位へ置かない。
- 新馬戦（全頭が戦績なし）は「判定不可（新馬）」として順位を出さない。
- 点数は順位付けのための線形予測子。校正していないので、勝率として表示しない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from keiba_model_data import before, comparable, going_key, ran

FEATURE_VERSION = 'feat-v1'
MODEL_VERSION = 'v1'
LEVELS = {'新馬': 0.0, '未勝利': 0.0, '1勝': 1.0, '2勝': 2.0, '3勝': 3.0, 'OP': 4.0, 'L': 4.5, 'G3': 5.0, 'G2': 6.0, 'G1': 7.0}
ALL_FEATURES = ('着順', '着差', 'クラス水準', 'クラス差', '距離適性', '同場適性', '馬場適性', '休養日数', '芝休養日数',
                '直近ダート数', '芝実績なし')


@dataclass
class Params:
    features: tuple = ('着順',)
    window: int = 5            # 直近芝の窓（取消・除外は数えない）
    half_life: float | None = None  # 着順・着差の鮮度減衰（日）。None は減衰なし
    shrink_k: float = 5.0      # 率の縮小の強さ
    band: int = 200            # 距離帯（±m）
    l2: float = 1.0            # 条件付きロジットのL2
    priors: dict = field(default_factory=dict)  # 学習期間で決める基準率・中央値
    surface: str = '芝'        # 対象のコース（'芝' か 'ダ'）。特徴量はこのコースの戦績で作る


def jpn_class(name):
    """地方の交流重賞（JpnI〜III）の格。ダート版でだけ使う。分からなければ None。"""
    import re
    text = str(name or '')
    if re.search(r'JpnIII|Jpn3|Ｊｐｎ３', text):
        return 'G3'
    if re.search(r'JpnII|Jpn2|Ｊｐｎ２', text):
        return 'G2'
    if re.search(r'JpnI|Jpn1|Ｊｐｎ１', text):
        return 'G1'
    return None


def horse_rows(history, cutoff, info, params):
    """1頭の特徴量（原データの分子・分母つき）。history は正規化済み・その馬の行。"""
    h = before(history, cutoff)
    out = {'最終出走日': pd.NaT, '最終芝出走日': pd.NaT}
    started = ran(h)
    if len(started):
        out['最終出走日'] = started['日付'].max()
        out['休養日数'] = (pd.Timestamp(cutoff) - out['最終出走日']).days
        out['直近ダート数'] = int(started.head(5)['コース'].eq('ダ').sum())
        recent = started.head(5)
        if '人気' in recent:  # 近走人気：過去5走の人気（頭数で補正、1に近いほど人気）。その時点で分かっている情報
            pop = pd.to_numeric(recent['人気'], errors='coerce')
            field = pd.to_numeric(recent['頭数'], errors='coerce')
            share = (1 - (pop - 1) / (field - 1)).where(field.gt(1)).dropna()
            if len(share):
                out['近走人気'] = float(share.clip(0, 1).mean())
    turf = started.loc[started['コース'].eq(params.surface)]
    if len(turf):
        out['最終芝出走日'] = turf['日付'].max()
        out['芝休養日数'] = (pd.Timestamp(cutoff) - out['最終芝出走日']).days
    win = turf.head(params.window)
    comp = comparable(win)
    out['芝有効走数'] = len(comp)
    out['芝評価期間開始'] = win['日付'].min() if len(win) else pd.NaT
    if len(comp):
        age = (pd.Timestamp(cutoff) - comp['日付']).dt.days.to_numpy(float)
        w = np.ones(len(comp)) if not params.half_life else 0.5 ** (age / params.half_life)
        pct = (1 - (comp['着順'] - 1) / (comp['頭数'] - 1)).to_numpy(float)
        out['着順'] = float(np.average(pct, weights=w))
        margin = comp['着差'].clip(lower=0, upper=3).to_numpy(float)
        ok = ~np.isnan(margin)
        out['着差'] = float(-np.average(margin[ok], weights=w[ok])) if ok.any() else np.nan
    levels = win['クラス'].where(win['クラス'].ne('不明'), win['レース名'].map(jpn_class) if params.surface == 'ダ' else win['クラス'])
    levels = levels.map(LEVELS).dropna()
    if len(levels):
        out['クラス水準'] = float(levels.mean())
        target = LEVELS.get(info.get('クラス'))
        out['クラス差'] = float(levels.mean() - target) if target is not None else np.nan
    allturf = comparable(turf)
    p = params.priors.get('top3', 0.21)
    for name, mask in (('距離適性', (allturf['距離'] - info['距離']).abs().le(params.band)),
                       ('同場適性', allturf['競馬場'].eq(info['競馬場'])),
                       ('馬場適性', allturf['馬場'].eq(going_key(info.get('馬場'))) if going_key(info.get('馬場')) else pd.Series(False, index=allturf.index))):
        sub = allturf.loc[mask.fillna(False)] if len(allturf) else allturf
        success, n = int(sub['着順'].le(3).sum()), len(sub)
        out[name + '_分子'], out[name + '_分母'] = success, n
        out[name] = (success + params.shrink_k * p) / (n + params.shrink_k)  # n=0 なら基準率（推定）
    out['芝実績なし'] = float(len(comp) == 0)
    return out


def build_features(field, history, info, params):
    rows = []
    for _, horse in field.iterrows():
        mine = history.loc[history['馬ID'].eq(horse['馬ID'])] if not history.empty else history
        rows.append({'レースID': info['レースID'], '馬ID': horse['馬ID'], '馬名': horse.get('馬名', ''),
                     **horse_rows(mine, info['開催日'], info, params)})
    return pd.DataFrame(rows)


def design(features, params):
    """欠損を学習期間の中央値で補い、学習期間の平均・標準偏差で標準化した行列。"""
    cols = list(params.features)
    x = pd.DataFrame(index=features.index)
    for c in cols:
        v = pd.to_numeric(features.get(c, pd.Series(np.nan, index=features.index)), errors='coerce')
        med = params.priors.get('median', {}).get(c, 0.0)
        x[c] = v.fillna(med)
    mean = pd.Series(params.priors.get('mean', {})).reindex(cols).fillna(0.0)
    std = pd.Series(params.priors.get('std', {})).reindex(cols).fillna(1.0).replace(0, 1.0)
    return ((x - mean) / std).to_numpy(float)


def fit_priors(train_features, params):
    """基準率・中央値・標準化を学習期間の行だけで決める（評価期間を見ない）。"""
    cols = list(params.features)
    num = {c: pd.to_numeric(train_features.get(c), errors='coerce') for c in cols}
    params.priors['median'] = {c: float(num[c].median()) if num[c].notna().any() else 0.0 for c in cols}
    filled = {c: num[c].fillna(params.priors['median'][c]) for c in cols}
    params.priors['mean'] = {c: float(filled[c].mean()) for c in cols}
    params.priors['std'] = {c: float(filled[c].std()) or 1.0 for c in cols}
    return params


def fit_logit(x, race_index, winner, l2=1.0, iters=50):
    """条件付きロジット（レース内softmaxで勝ち馬を当てる）をニュートン法で推定。"""
    beta = np.zeros(x.shape[1])
    races = [np.flatnonzero(race_index == r) for r in np.unique(race_index)]
    races = [idx for idx in races if winner[idx].sum() == 1]
    for _ in range(iters):
        grad = -l2 * beta
        hess = -l2 * np.eye(len(beta))
        for idx in races:
            xi = x[idx]
            s = xi @ beta
            p = np.exp(s - s.max())
            p /= p.sum()
            mean = p @ xi
            grad += xi[winner[idx].astype(bool)][0] - mean
            hess -= (xi * p[:, None]).T @ xi - np.outer(mean, mean)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return beta


def rank_race(features, beta, params, debut_race=False):
    """点数と順位。新馬戦は順位を出さない。同点は馬IDで並べる（再現可能）。"""
    out = features[['レースID', '馬ID']].copy()
    if debut_race:
        out['点数'], out['順位'], out['判定'] = np.nan, pd.NA, '判定不可（新馬・戦績なし）'
        return out
    out['点数'] = design(features, params) @ beta
    order = out.sort_values(['点数', '馬ID'], ascending=[False, True]).index
    out['順位'] = pd.Series(range(1, len(out) + 1), index=order).reindex(out.index).astype('Int64')
    flag = features['芝実績なし'].eq(1)
    few = pd.to_numeric(features['芝有効走数'], errors='coerce').between(1, 2)
    out['判定'] = np.where(flag, '推定（芝の比較可能な実績なし）', np.where(few, '暫定（芝の有効1〜2走）', '基準モデル'))
    return out


# ---------------------------------------------------------------- 学習済み設定（影の予測用）
ENTRY_FEATURES = ('斤量差', '牝馬', '年齢差')


def entry_features(field):
    """出走表の基本項目（v6）：斤量差・牝馬（牡・セン馬もいるレース）・年齢差。field: 馬ID・性齢・斤量（出走する馬だけ）。"""
    import re
    out = pd.DataFrame({'馬ID': field['馬ID'].astype(str).to_numpy()})
    weight = pd.to_numeric(field.get('斤量', pd.Series(np.nan, index=field.index)), errors='coerce').to_numpy(float)
    sexage = field.get('性齢', pd.Series('', index=field.index)).fillna('').astype(str).to_numpy()
    sex = np.array([s[:1] for s in sexage])
    age = np.array([float(m[0]) if (m := re.search(r'\d+', s)) else np.nan for s in sexage])
    out['斤量差'] = weight - np.nanmean(weight) if np.isfinite(weight).any() else np.nan
    mixed = bool(np.isin(sex, ('牡', 'セ')).any())
    out['牝馬'] = np.where(sex == '牝', float(mixed), np.where(np.isin(sex, ('牡', 'セ')), 0.0, np.nan))
    out['年齢差'] = age - np.nanmean(age) if np.isfinite(age).any() else np.nan
    return out


def rank_average(ranking, common_rank):
    """v2（C2）：全馬共通（ペース項目なし）と v1 の順位の平均で並べ直す。共通の順位がない馬は v1 の順位で補う。
    同点は v1 の点数、さらに馬ID（再現可能）。"""
    r = ranking.copy()
    v1_rank = pd.to_numeric(r['順位'], errors='coerce').to_numpy(float)
    common = pd.to_numeric(pd.Series(common_rank, index=r.index), errors='coerce').to_numpy(float)
    filled = np.where(np.isnan(common), v1_rank, common)
    r['平均順位'] = (v1_rank + filled) / 2
    order = r.sort_values(['平均順位', '点数', '馬ID'], ascending=[True, False, True]).index
    r['順位'] = pd.Series(range(1, len(r) + 1), index=order).reindex(r.index).astype('Int64')
    return r


def save_artifact(path, params, beta, extra=None):
    """学習済みの設定を保存する（係数・基準率・標準化の値だけ。生データは含めない）。"""
    import json
    from pathlib import Path
    data = {'model_version': MODEL_VERSION, 'feature_version': FEATURE_VERSION, 'features': list(params.features),
            'window': params.window, 'half_life': params.half_life, 'shrink_k': params.shrink_k, 'band': params.band,
            'l2': params.l2, 'priors': params.priors, 'beta': [float(b) for b in beta], 'surface': params.surface,
            **(extra or {})}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    return data


def load_artifact(path):
    import json
    from pathlib import Path
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    params = Params(features=tuple(data['features']), window=data['window'], half_life=data['half_life'],
                    shrink_k=data['shrink_k'], band=data['band'], l2=data['l2'], priors=data['priors'],
                    surface=data.get('surface', '芝'))
    return params, np.array(data['beta'], float), data


def artifact_version(data):
    import hashlib
    import json
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:10]
    return f"{data['model_version']}-{digest}"
