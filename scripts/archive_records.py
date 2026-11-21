"""予想記録（ラボの順位・点数だけ）をリポジトリの records/ へ移す。生データ・結果は移さない。

使い方:
  python scripts/archive_records.py --weekend-zip ZIP          # 週末一括分析のZIP（records/predictions/** だけを取り込む）
  python scripts/archive_records.py --fixed-csv CSV --label L  # 事前固定の予想CSV（順位・点数列だけを取り出す）
  python scripts/archive_records.py --comparisons DIR --label L  # 結果照合CSV（レース単位の指標だけ）
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
    """records/predictions/** を取り込む。同じ名前で中身が違う記録（サーバー再起動後の作り直しなど）は、
    上書きせず「_s<sha先頭8桁>」を付けた別名で並べて保存する（照合では発走前に作られた最後の版を使う）。"""
    added = 0
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.startswith('records/predictions/') and not n.endswith('/') and '..' not in n]
        stems = sorted({n.rsplit('.', 1)[0] for n in names})
        for stem in stems:
            parts = {ext: z.read(f'{stem}.{ext}') for ext in ('json', 'csv') if f'{stem}.{ext}' in names}
            rel = stem[len('records/predictions/'):]
            target = ROOT / 'predictions' / rel
            same = all((target.parent / f'{target.name}.{ext}').exists() and
                       (target.parent / f'{target.name}.{ext}').read_bytes() == data for ext, data in parts.items())
            if same:
                continue
            exists = any((target.parent / f'{target.name}.{ext}').exists() for ext in parts)
            if exists:
                digest = hashlib.sha256(parts.get('json', b'') + parts.get('csv', b'')).hexdigest()[:8]
                target = target.parent / f'{target.name}_s{digest}'
            for ext, data in parts.items():
                added += write_once(target.parent / f'{target.name}.{ext}', data)
    return added


def comparisons_from_weekend_zip(zip_path, label):
    """週末出力ZIP（またはフォルダ）の結果照合CSV（レース単位の指標・モデル別まとめ）だけを records/comparisons/ へ。"""
    added = 0
    source = Path(zip_path)
    if source.is_dir():
        items = [(p.name, p.read_bytes()) for p in sorted(source.glob('*_結果照合_*.csv'))]
    else:
        with zipfile.ZipFile(source) as z:
            items = [(Path(n).name, z.read(n)) for n in z.namelist() if '_結果照合_' in n and n.endswith('.csv')]
    for name, data in items:
        added += write_once(ROOT / 'comparisons' / label / name, data)
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
    ap.add_argument('--comparisons', help='結果照合CSVのあるフォルダまたはZIP（--label 必須）')
    args = ap.parse_args()
    if args.comparisons:
        print('追加', comparisons_from_weekend_zip(args.comparisons, args.label))
    if args.weekend_zip:
        print('追加', from_weekend_zip(args.weekend_zip))
    if args.fixed_csv:
        print('追加', from_fixed_csv(args.fixed_csv, args.label))
