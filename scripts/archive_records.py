"""予想記録（ラボの順位・点数だけ）をリポジトリの records/ へ移す。生データ・結果は移さない。

使い方:
  python scripts/archive_records.py --weekend-zip ZIP          # 週末一括分析のZIP（records/predictions/** だけを取り込む）
  python scripts/archive_records.py --fixed-csv CSV --label L  # 事前固定の予想CSV（順位・点数列だけを取り出す）
既存ファイルは上書きしない（同名があれば内容一致を確認して終了）。
"""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / 'records'
KEEP = ['レースID', '馬ID', '馬番', '馬名', '状態', 'S順位', 'S参考点', 'M順位', 'M参考点', 'H順位', 'H参考点',
        '全馬共通S順位', '全馬共通S参考点', '全馬共通M順位', '全馬共通M参考点', '全馬共通H順位', '全馬共通H参考点',
        'S判定', 'M判定', 'H判定', '全馬共通判定', '比較対象']


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            sys.exit(f'{path} は内容が異なる既存記録です（上書きしません）')
        return False
    path.write_bytes(data)
    return True


def from_weekend_zip(zip_path):
    added = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.startswith('records/predictions/') and not name.endswith('/') and '..' not in name:
                added += write_once(ROOT / 'predictions' / name[len('records/predictions/'):], z.read(name))
    return added


def from_fixed_csv(csv_path, label):
    raw = Path(csv_path).read_bytes()
    table = pd.read_csv(io.BytesIO(raw), dtype={'レースID': str, '馬ID': str})
    table = table[[c for c in KEEP if c in table.columns]]
    data = table.to_csv(index=False).encode('utf-8-sig')
    meta = {'種別': '事前固定の予想（取り込み）', 'ラベル': label, '元ファイルsha256': hashlib.sha256(raw).hexdigest(),
            '取り込み後sha256': hashlib.sha256(data).hexdigest(), '列': list(table.columns), '行数': len(table)}
    a = write_once(ROOT / 'fixed' / label / 'predictions.csv', data)
    b = write_once(ROOT / 'fixed' / label / 'manifest.json', json.dumps(meta, ensure_ascii=False, indent=1).encode('utf-8'))
    return int(a) + int(b)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--weekend-zip')
    ap.add_argument('--fixed-csv')
    ap.add_argument('--label')
    args = ap.parse_args()
    if args.weekend_zip:
        print('追加', from_weekend_zip(args.weekend_zip))
    if args.fixed_csv:
        print('追加', from_fixed_csv(args.fixed_csv, args.label))
