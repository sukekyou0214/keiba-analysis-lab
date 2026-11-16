"""v5 の本番用係数（影の予測）を、手元の全期間で学習して保存する。
使い方: python scripts/fit_v5.py --backtest DIR [DIR ...] --save models/v5.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import keiba_model_v1 as v1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    import v2_candidates as vc
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', nargs='+', required=True)
    ap.add_argument('--save', required=True)
    args = ap.parse_args(argv)
    seasons = [vc.Season(p) for p in args.backtest]
    base, _, _ = v1.load_artifact(ROOT / 'models' / 'v1.json')
    params = vc.spec(base, list(base.features) + ['近走人気'])
    beta = vc.fit([(s, s.ids) for s in seasons], params)
    data = v1.save_artifact(args.save, params, beta, {
        'model_version': 'v5', '種類': 'rank_average', '新馬戦': 'jockey-v1', '候補': 'v3＋近走人気',
        '学習期間': '2025-09-13〜11-30・2026-03-28〜05-31・2026-06-06〜09-06・2026-09-12〜11-08',
        '学習レース': sum(len(s.ids) for s in seasons), '採用状態': '影の予測（主予測ではない）'})
    print(v1.artifact_version(data), dict(zip(params.features, map(float, beta))))


if __name__ == '__main__':
    main()
