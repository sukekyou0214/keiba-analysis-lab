"""予想記録と結果記録（別管理・追記のみ）。

- 予想記録：レースごとに1ファイル。作成後は上書きしない（同じレース・同じモデル版の再保存は拒否）。
  記録項目：レースID、馬ID、馬番、馬名、順位、点数、判定、作成日時、対象日時、取得元、コード版、
  データ版、特徴量版、モデル版、入力ハッシュ、予想ファイルのsha256。
- 結果記録：別フォルダ。予想とは別のファイルで、予想記録を書き換えない。
- 照合：予想の作成日時が結果の確定より前か（事前保存か）を区別して集計する。

保存先は Store 抽象（既定はローカルフォルダ）。Streamlit の /tmp は永続ではない。
永続化はローカル保存→ZIP→非公開保管、またはリポジトリの records/（予想のみ、生データなし）で行う。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

JST = timezone(timedelta(hours=9))
PRED_COLUMNS = ['レースID', '馬ID', '馬番', '馬名', '順位', '点数', '判定']


def code_version(root=None):
    root = Path(root or Path(__file__).resolve().parent)
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True,
                              timeout=5).stdout.strip() or 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def frame_hash(frame):
    """入力データの内容ハッシュ（列名・値・行順で決まる）。同じ入力なら同じ値。"""
    if frame is None or len(frame) == 0:
        return sha256_bytes(b'')
    data = frame.sort_index(axis=1).to_csv(index=False).encode('utf-8')
    return sha256_bytes(data)


class RecordExists(Exception):
    pass


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def write_new(self, relative, data: bytes):
        path = self.root / relative
        if path.exists():
            raise RecordExists(f'{relative} は保存済みです（上書きしません）')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def read(self, relative):
        path = self.root / relative
        return path.read_bytes() if path.exists() else None

    def list(self, prefix):
        base = self.root / prefix
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob('*.json')) if base.exists() else []


def save_prediction(store, race_id, ranking, *, target_time, source, model_version, feature_version,
                    data_version, input_frame=None, created_at=None, code=None, extra=None):
    """予想を保存する。同じレース×モデル版は1回だけ。"""
    created = created_at or datetime.now(JST).isoformat(timespec='seconds')
    table = ranking.reindex(columns=PRED_COLUMNS).copy()
    table['馬ID'] = table['馬ID'].astype(str)
    if table['馬ID'].duplicated().any():
        raise ValueError('同じ馬IDが重複しています（馬番だけで結合しない）')
    csv = table.to_csv(index=False).encode('utf-8-sig')
    meta = {'種別': '予想', 'レースID': str(race_id), '作成日時': created, '対象日時': str(target_time),
            '取得元': source, 'コード版': code or code_version(), 'データ版': data_version,
            '特徴量版': feature_version, 'モデル版': model_version,
            '入力ハッシュ': frame_hash(input_frame), '予想sha256': sha256_bytes(csv), **(extra or {})}
    base = f'predictions/{model_version}/{race_id}'
    store.write_new(base + '.csv', csv)
    store.write_new(base + '.json', json.dumps(meta, ensure_ascii=False, indent=1).encode('utf-8'))
    return meta


def save_result(store, race_id, result, *, confirmed_at, source):
    """結果を保存する（予想とは別）。result: 馬ID・着順原文・状態・着順。"""
    table = result.reindex(columns=['レースID', '馬ID', '馬番', '馬名', '着順原文', '状態', '着順']).copy()
    csv = table.to_csv(index=False).encode('utf-8-sig')
    meta = {'種別': '結果', 'レースID': str(race_id), '確定日時': str(confirmed_at), '取得元': source,
            '保存日時': datetime.now(JST).isoformat(timespec='seconds'), '結果sha256': sha256_bytes(csv)}
    store.write_new(f'results/{race_id}.csv', csv)
    store.write_new(f'results/{race_id}.json', json.dumps(meta, ensure_ascii=False, indent=1).encode('utf-8'))
    return meta


def load_prediction(store, model_version, race_id):
    import io
    meta = store.read(f'predictions/{model_version}/{race_id}.json')
    data = store.read(f'predictions/{model_version}/{race_id}.csv')
    if meta is None or data is None:
        return None, None
    meta = json.loads(meta)
    if sha256_bytes(data) != meta['予想sha256']:
        raise ValueError(f'{race_id}: 予想ファイルが保存時と一致しません')
    return meta, pd.read_csv(io.BytesIO(data), dtype={'馬ID': str, 'レースID': str})


def load_result(store, race_id):
    import io
    meta, data = store.read(f'results/{race_id}.json'), store.read(f'results/{race_id}.csv')
    if meta is None:
        return None, None
    return json.loads(meta), pd.read_csv(io.BytesIO(data), dtype={'馬ID': str, 'レースID': str})


def compare(store, model_version):
    """保存済みの予想と結果を馬IDで照合する。事前保存（作成日時 < 確定日時）かを列で示す。"""
    rows = []
    for rel in store.list(f'predictions/{model_version}'):
        race_id = Path(rel).stem
        meta, pred = load_prediction(store, model_version, race_id)
        rmeta, result = load_result(store, race_id)
        if result is None:
            continue
        merged = pred.merge(result[['馬ID', '状態', '着順']], on='馬ID', how='left', validate='one_to_one')
        merged['事前保存'] = str(meta['作成日時']) < str(rmeta['確定日時'])
        merged['モデル版'] = model_version
        rows.append(merged)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
