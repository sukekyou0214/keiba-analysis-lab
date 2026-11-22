"""結果前の予想保存（主予測＋影の予測）と、結果後の照合。

- 週末一括分析のたびに、各レースの予想を記録へ保存する（keiba_registry、上書きなし）。
  対象：旧方式（現行の比較ルール）・全馬共通方式（主画面の既定）・候補モデル v1（影の予測。models/v1.json がある場合）。
  出走馬の構成（取消・除外を含む）が前回保存から変わった場合だけ、改訂として別ファイルに保存する。
- 出走表の変化は entries_history/ に時刻つきで残す（予想時点の出走馬構成の履歴）。
- レース後は結果ページを取得し、結果記録を別に保存して照合する。照合は「発走前に作られた最後の版」で行う。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from keiba_registry import LocalStore, RecordExists, compare, frame_hash, save_prediction, save_result

JST = timezone(timedelta(hours=9))
ARTIFACT = Path(__file__).resolve().parent / 'models' / 'v1.json'
ARTIFACT_V2 = Path(__file__).resolve().parent / 'models' / 'v2.json'
ARTIFACT_V3 = Path(__file__).resolve().parent / 'models' / 'v3.json'
ARTIFACT_V5 = Path(__file__).resolve().parent / 'models' / 'v5.json'
REPO_RECORDS = Path(__file__).resolve().parent / 'records' / 'predictions'


def _now():
    return datetime.now(JST).isoformat(timespec='seconds')


def target_time(info):
    post = info.get('発走') or '00:00'
    return f"{info['開催日']}T{post}:00+09:00"


def snapshot_entries(work, race_id, entries):
    """出走表の構成が変わった時だけ時刻つきで保存（履歴）。"""
    folder = Path(work) / 'entries_history' / race_id
    folder.mkdir(parents=True, exist_ok=True)
    table = entries[['馬ID', '馬名', '馬番', '状態']].sort_values('馬ID').reset_index(drop=True)
    digest = frame_hash(table)
    if (folder / f'{digest}.csv').exists():
        return digest
    table.to_csv(folder / f'{digest}.csv', index=False, encoding='utf-8-sig')
    with open(folder / 'log.csv', 'a', encoding='utf-8') as log:
        log.write(f'{_now()},{digest}\n')
    return digest


def _save(store, race_id, model, ranking, info, entries, data_version, feature_version, extra=None):
    """同じ構成なら保存済み（何もしない）。構成が変わっていれば改訂番号を上げて保存。"""
    composition = frame_hash(entries[['馬ID', '状態']].sort_values('馬ID').reset_index(drop=True))
    for revision in range(0, 50):
        key = race_id + (f'_r{revision}' if revision else '')
        meta = store.read(f'predictions/{model}/{key}.json')
        if meta is None:
            try:
                return save_prediction(store, race_id, ranking, target_time=target_time(info), source=info.get('取得元', ''),
                                       model_version=model, feature_version=feature_version, data_version=data_version,
                                       input_frame=entries[['馬ID', '状態']].sort_values('馬ID').reset_index(drop=True),
                                       extra={'出走構成': composition, **(extra or {})}, revision=revision,
                                       created_at=_now())
            except RecordExists:
                continue
        import json
        if json.loads(meta).get('出走構成') == composition:
            return None
    return None


def save_weekend_records(work, info, entries, per_pace, common, history_frame=None, raw_frame=None, pool_frame=None):
    """1レース分の予想を保存（旧方式M・全馬共通M・v1影の予測）。保存した版の名前を返す。"""
    from keiba_model_data import DATA_VERSION
    from keiba_registry import code_version
    store = LocalStore(Path(work) / 'records')
    race_id = info['レースID']
    snapshot_entries(work, race_id, entries)
    code = code_version()[:7]
    saved = []
    numbers = entries.set_index('馬ID')['馬番']
    names = entries.set_index('馬ID')['馬名']
    for label, table in (('old-M', per_pace.get('M')), ('common-M', common.get('M'))):
        if table is None:
            continue
        ranking = table.reset_index().rename(columns={'参考順位': '順位', '総合参考点': '点数'})
        ranking['レースID'] = race_id
        ranking['馬番'] = ranking['馬ID'].map(numbers)
        ranking['馬名'] = ranking['馬ID'].map(names)
        if _save(store, race_id, f'{label}@{code}', ranking, info, entries, DATA_VERSION, 'frozen'):
            saved.append(label)
    for path, label in ((ARTIFACT, 'shadow-v1'), (ARTIFACT_V2, 'shadow-v2'), (ARTIFACT_V3, 'shadow-v3'), (ARTIFACT_V5, 'shadow-v5')):
        if not path.exists() or history_frame is None:
            continue
        ranking, version, feature_version = _shadow_ranking(path, info, entries, history_frame, raw_frame, pool_frame)
        ranking['馬番'] = ranking['馬ID'].map(numbers)
        ranking['馬名'] = ranking['馬ID'].map(names)
        if _save(store, race_id, f'shadow-{version}', ranking, info, entries, DATA_VERSION, feature_version,
                 {'影の予測': True}):
            saved.append(label)
    return saved


def _shadow_ranking(path, info, entries, history_frame, raw_frame, pool_frame=None):
    """影の予測1つ分の順位。v2（種類 rank_average）は全馬共通（ペース項目なし）の順位と平均する。
    v3（新馬戦 jockey-v1）は、新馬戦だけ騎手点（この週末の出走馬の戦績から数える）で並べる。"""
    import keiba_model_v1 as v1
    params, beta, data = v1.load_artifact(path)
    version = v1.artifact_version(data)
    race_id = info['レースID']
    active = entries.loc[entries['状態'].eq('出走予定')]
    race_info = {**info, 'クラス': info.get('クラス') or _class_of(info)}
    debut = '新馬' in str(info.get('レース名', ''))
    if debut and data.get('新馬戦') == 'jockey-v1':
        import keiba_jockey as kj
        field = active[['馬ID', '騎手']] if '騎手' in active else active[['馬ID']].assign(騎手='')
        return kj.debut_ranking(field, kj.rides(pool_frame), info['開催日'], race_id), version, kj.VERSION
    if debut:  # 新馬戦は戦績がないので特徴量を作らず「判定不可」として保存する（照合では捕捉失敗に数える）
        feats = pd.DataFrame({'レースID': race_id, '馬ID': active['馬ID'].astype(str).to_numpy()})
        return v1.rank_race(feats, beta, params, debut_race=True), version, v1.FEATURE_VERSION
    feats = v1.build_features(active, history_frame, race_info, params)
    ranking = v1.rank_race(feats, beta, params)
    if data.get('種類') == 'rank_average':
        common = common_without_pace(race_info, active, raw_frame)
        ranking = v1.rank_average(ranking, ranking['馬ID'].map(common).to_numpy())
    return ranking, version, v1.FEATURE_VERSION


def common_without_pace(info, active, raw_frame):
    """過去検証と同じ「想定ペース項目なし」の全馬共通の順位（馬ID→順位）。戦績が無ければ空。"""
    from types import SimpleNamespace
    from keiba_eval import frozen_rank
    if raw_frame is None or raw_frame.empty:
        return {}
    field = active[['馬ID', '馬名']].assign(馬ID=active['馬ID'].astype(str))
    raw = raw_frame.assign(馬ID=raw_frame['馬ID'].astype(str))
    bt = SimpleNamespace(raw=raw, field=lambda rid: field)
    r = frozen_rank(bt, {'レースID': info['レースID'], '開催日': info['開催日'], '競馬場': info['競馬場'],
                         '距離': info['距離'], 'レース名': info.get('レース名', ''), 'クラス': info['クラス']}, 'common')
    return dict(zip(r['馬ID'].astype(str), pd.to_numeric(r['順位'], errors='coerce')))


def _class_of(info):
    from keiba_model_data import classify_class
    return classify_class(info.get('レース名', ''), info.get('一覧表記', ''), info.get('格', '')) or '不明'


def repo_records_for(dates, root=None):
    """リポジトリの records/predictions にある、指定日（YYYYMMDD）の予想記録（json）の一覧。
    サーバー再起動で作業フォルダが消えても、結果前にリポジトリへ保存した予想で照合できるようにする。"""
    import json
    root = Path(root or REPO_RECORDS)
    wanted = {f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in dates}
    out = []
    for path in sorted(root.glob('*/*.json')) if root.exists() else []:
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if str(meta.get('対象日時', ''))[:10] in wanted:
            out.append(path)
    return out


def restore_from_repo(work, dates, root=None):
    """作業フォルダに無い予想記録を、リポジトリの記録から戻す（内容はそのまま、上書きしない）。戻したレースIDを返す。"""
    import json
    store = LocalStore(Path(work) / 'records')
    restored = set()
    for meta_path in repo_records_for(dates, root):
        csv_path = meta_path.with_suffix('.csv')
        if not csv_path.exists():
            continue
        rel = f'predictions/{meta_path.parent.name}/{meta_path.stem}'
        data = {'.json': meta_path.read_bytes(), '.csv': csv_path.read_bytes()}
        current = {s: store.read(rel + s) for s in data}
        if all(current[s] is None for s in data):
            for s, body in data.items():
                store.write_new(rel + s, body)
        elif any(current[s] != body for s, body in data.items()):
            # 作業フォルダに同名・別内容の記録（再起動後の作り直しなど）があっても、結果前の記録を別名で並べて戻す
            import hashlib
            alt = rel + '_s' + hashlib.sha256(data['.json'] + data['.csv']).hexdigest()[:8]
            for s, body in data.items():
                try:
                    store.write_new(alt + s, body)
                except RecordExists:
                    pass
        restored.add(json.loads(meta_path.read_text(encoding='utf-8'))['レースID'])
    return sorted(restored)


def collect_results(work, race_ids, fetch_text):
    """レース後：結果ページを取得し、結果記録を保存する（保存済みのレースは取得しない）。"""
    from keiba_backtest import RESULT_URL, parse_result_page
    from keiba_model_data import finish_status
    store = LocalStore(Path(work) / 'records')
    saved, errors = [], {}
    for rid in race_ids:
        if store.read(f'results/{rid}.json') is not None:
            continue
        try:
            info, runners = parse_result_page(fetch_text(RESULT_URL + rid), rid)
        except ValueError as exc:
            errors[rid] = str(exc)[:160]
            continue
        parsed = runners['着順原文'].map(finish_status)
        runners['状態'] = parsed.map(lambda x: x[0])
        runners['着順'] = parsed.map(lambda x: x[1])
        save_result(store, rid, runners, confirmed_at=_now(), source=info['取得元'])
        saved.append(rid)
    return saved, errors


def comparison_table(work):
    """保存済みの全モデルについて、事前保存の予想と結果を照合した表と、モデル別のまとめ。"""
    store = LocalStore(Path(work) / 'records')
    root = Path(work) / 'records' / 'predictions'
    models = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    tables = [compare(store, m) for m in models]
    merged = pd.concat([t for t in tables if len(t)], ignore_index=True) if any(len(t) for t in tables) else pd.DataFrame()
    if merged.empty:
        return merged, pd.DataFrame()
    from keiba_eval import race_metrics, summarize
    rows = []
    for (model, rid), g in merged.groupby(['モデル版', 'レースID']):
        field = g[['馬ID', '状態', '着順']].assign(実出走=g['状態'].isin(('完走', '中止', '失格', '降着')))
        rows.append({'モデル版': model, 'レースID': rid, '事前保存': bool(g['事前保存'].iloc[0]),
                     **race_metrics(g[['馬ID', '順位']], field)})
    per_race = pd.DataFrame(rows)
    summary = per_race.loc[per_race['事前保存']].groupby('モデル版').apply(summarize, include_groups=False).reset_index()
    return per_race, summary
