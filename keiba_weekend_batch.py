"""週末の全芝レース一括分析（出走表・戦績・S/M/H → ラボの参考順位 → データ充足の信頼度）。

ラボ本体と同じ関数（keiba_core の取得・正規化、keiba_main_ranking.main_ranking）を使い、
netkeibaへの要求はすべて keiba_fetch_policy の共通ゲート（取得先ごと1件ずつ・最小間隔・
2連続拒否で停止）を通す。停止したら保存して終了し、同じコマンドで続きから再開する。

取得データは --work（非公開の作業フォルダ）にだけ保存する。リポジトリや公開画面へ置かない。

本番画面（管理・検証）からはバックグラウンドで実行し、CLI（scripts/weekend_turf_batch.py）からも実行できる。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
LIST_URL = 'https://race.netkeiba.com/top/race_list_sub.html?kaisai_date='
ENTRY_URL = 'https://race.netkeiba.com/race/shutuba.html?race_id='
PACE_LABEL = {'スロー寄り', 'ミドル寄り', 'ハイ寄り'}
GRADED = {'G1', 'G2', 'G3'}
SCORE_RULE = ('100点＝戦績確認率×25＋順位表示率(S/M/Hの最少)×30＋平均項目充足率×30＋(近5走平均有効数÷5)×15。'
              '上位選出：確認率100%・順位表示率80%以上・平均項目充足率70%以上。点数降順、同点は平均有効数降順、レースID昇順。'
              '高：90点以上、中：75点以上、それ以外は低。的中率や「崩れにくさ」を検証した指標ではない。')


def now_text():
    return datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')


class Stopped(Exception):
    """The shared gate stopped (cooldown or budget). Progress is saved; rerun later."""


def fetch_text(url, encoding=None):
    from keiba_core import HEADERS
    from keiba_fetch_policy import policy_get, PolicyStop
    try:
        res = policy_get(url, headers=HEADERS, timeout=20)
        res.raise_for_status()
    except (PolicyStop, requests.RequestException) as exc:
        raise Stopped(str(exc)[:180]) from exc
    res.encoding = encoding or res.apparent_encoding
    return res.text


# ---------------------------------------------------------------- race list
def parse_race_list(html, date):
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for item in soup.select('li.RaceList_DataItem'):
        link = item.select_one('a[href*="race_id="]')
        hit = re.search(r'race_id=(\d{12})', link.get('href', '')) if link else None
        if not hit:
            continue
        text = item.get_text(' ', strip=True)
        course = re.search(r'(芝|ダ|障)\s*(\d{3,4})m', text)
        rows.append({'日付': f'{date[:4]}-{date[4:6]}-{date[6:]}', 'レースID': hit[1], '一覧表記': text,
                     'コース': course[1] if course else '', '距離': int(course[2]) if course else pd.NA})
    return pd.DataFrame(rows).drop_duplicates('レースID') if rows else pd.DataFrame(
        columns=['日付', 'レースID', '一覧表記', 'コース', '距離'])


# ---------------------------------------------------------------- entries
def parse_entry_page(html, race_id):
    from keiba_race_metadata import parse_metadata
    from keiba_v22_collect import entry_rows
    url = ENTRY_URL + race_id
    meta = parse_metadata(html, url)
    soup = BeautifulSoup(html, 'html.parser')
    start = soup.select_one('.RaceData01')
    post = re.search(r'(\d{1,2}:\d{2})発走', start.get_text(' ', strip=True)) if start else None
    rows = []
    for tr in entry_rows(soup):
        link = tr.select_one('.HorseName a[href*="/horse/"]') or tr.select_one('a[href*="/horse/"]')
        hit = re.search(r'/horse/(\d{10})', link.get('href', '')) if link else None
        if not hit:
            raise ValueError(f'{race_id}: 出走馬IDを取得できません')
        cell = tr.select_one('td[class^="Umaban"], td[class*=" Umaban"]')
        number = cell.get_text(strip=True) if cell else ''
        jockey = tr.select_one('.Jockey')
        classes = ' '.join(tr.get('class') or [])
        text = tr.get_text(' ', strip=True)
        cancelled = 'Cancel' in classes or bool(re.search(r'取消|除外', text))
        rows.append({'レースID': race_id, '馬ID': hit[1], '馬名': link.get('title') or link.get_text(strip=True),
                     '馬番': int(number) if number.isdecimal() else pd.NA,
                     '騎手': jockey.get_text(' ', strip=True) if jockey else pd.NA,
                     '状態': '取消・除外' if cancelled else '出走予定'})
    entries = pd.DataFrame(rows)
    if entries.empty or entries['馬ID'].duplicated().any():
        raise ValueError(f'{race_id}: 出走馬の件数または重複に問題があります')
    info = {'レースID': race_id, 'レース名': meta.get('name', ''), '発走': post[1] if post else '',
            '競馬場': meta.get('venue', ''), 'コース': meta.get('surface', ''), '距離': meta.get('distance', pd.NA),
            '開催日': meta.get('race_date', ''), '格': meta.get('grade') or '不明',
            '取得元': url, '取得日時': now_text()}
    return info, entries


# ---------------------------------------------------------------- histories
class HistoryCache:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log_path = self.folder / 'horse_log.csv'
        self.log = pd.read_csv(self.log_path, dtype=str) if self.log_path.exists() else pd.DataFrame(
            columns=['馬ID', '馬名', '状態', '理由', '取得日時'])

    def status(self, hid):
        hit = self.log.loc[self.log['馬ID'].eq(hid)]
        return hit.iloc[-1]['状態'] if len(hit) else ''

    def record(self, hid, name, state, reason=''):
        self.log = pd.concat([self.log.loc[~self.log['馬ID'].eq(hid)],
                              pd.DataFrame([{'馬ID': hid, '馬名': name, '状態': state, '理由': reason,
                                             '取得日時': now_text()}])], ignore_index=True)
        self.log.to_csv(self.log_path, index=False, encoding='utf-8-sig')

    def path(self, hid):
        return self.folder / f'horse_{hid}.csv'

    def load(self, hid):
        path = self.path(hid)
        return pd.read_csv(path, dtype={'馬ID': str, 'レースID': str}) if path.exists() else pd.DataFrame()


def fetch_histories(entries, cache, races):
    """未取得の馬だけ取得。新馬戦の出走馬は戦績なしとして要求しない。"""
    from keiba_core import get_results
    debut = set(races.loc[races['レース名'].astype(str).str.contains('新馬'), 'レースID'])
    horses = entries.drop_duplicates('馬ID')
    for _, horse in horses.iterrows():
        hid, name = horse['馬ID'], horse['馬名']
        if cache.status(hid) in {'成功', '戦績なし確認'}:
            continue
        if horse['レースID'] in debut:
            cache.record(hid, name, '戦績なし確認', '新馬戦の出走馬（要求せず）')
            continue
        try:
            raw = get_results(hid, name)
        except Stopped:
            raise
        except ValueError as exc:
            # 成績表そのものが無い馬（新馬など）は「戦績なし確認」。
            cache.record(hid, name, '戦績なし確認' if '成績' in str(exc) else '取得不可', str(exc)[:180])
            continue
        except Exception as exc:  # noqa: BLE001
            from keiba_fetch_policy import PolicyStop
            cache.record(hid, name, '取得不可', str(exc)[:180])
            if isinstance(exc, PolicyStop):
                raise Stopped(str(exc)) from exc
            continue
        raw.to_csv(cache.path(hid), index=False, encoding='utf-8-sig')
        cache.record(hid, name, '成功', f'{len(raw)}行')


def race_history(entries, cache):
    from keiba_core import convert_auto, prepare
    frames = [cache.load(h) for h in entries['馬ID'].drop_duplicates()]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return prepare(convert_auto(pd.concat(frames, ignore_index=True)))


# ---------------------------------------------------------------- pace
def pace_targets(history, race_date, graded):
    """直近芝5走（対象日前）。重賞は直近重賞5走も補充。"""
    if history.empty:
        return set()
    h = history.loc[pd.to_datetime(history['日付'], errors='coerce').lt(pd.Timestamp(race_date))]
    ids = h['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    h = h.assign(_rid=ids).loc[ids.str.fullmatch(r'\d{12}')].sort_values('日付', ascending=False)
    turf = h.loc[h.get('コース', pd.Series('', index=h.index)).eq('芝')]
    out = set(turf.groupby('馬ID', sort=False).head(5)['_rid'])
    if graded and 'レース格' in h:
        out |= set(h.loc[h['レース格'].isin(GRADED)].groupby('馬ID', sort=False).head(5)['_rid'])
    return out


def fetch_paces(race_ids, pace_path):
    from keiba_core import get_netkeiba_pace
    from keiba_main_integration import PACE_UNAVAILABLE
    from keiba_fetch_policy import PolicyStop, POLICY
    table = pd.read_csv(pace_path, dtype=str) if pace_path.exists() else pd.DataFrame(
        columns=['レースID', 'netkeibaペース', '状態', '理由', '取得日時'])
    done = set(table.loc[table['状態'].isin(['成功', '取得不可']), 'レースID'])
    loader = get_netkeiba_pace
    for rid in sorted(set(race_ids) - done):
        try:
            value = loader(rid)
            row = {'レースID': rid, 'netkeibaペース': value if value in PACE_LABEL else '',
                   '状態': '成功' if value in PACE_LABEL else '取得不可', '理由': '' if value in PACE_LABEL else str(value)}
        except PolicyStop as exc:
            raise Stopped(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:180]
            permanent = isinstance(exc, ValueError) and message in PACE_UNAVAILABLE
            row = {'レースID': rid, 'netkeibaペース': '', '状態': '取得不可' if permanent else '再取得待ち', '理由': message}
            if POLICY.cooldown_until():
                table = pd.concat([table.loc[~table['レースID'].eq(rid)], pd.DataFrame([{**row, '取得日時': now_text()}])])
                table.to_csv(pace_path, index=False, encoding='utf-8-sig')
                raise Stopped(message) from exc
        table = pd.concat([table.loc[~table['レースID'].eq(rid)], pd.DataFrame([{**row, '取得日時': now_text()}])],
                          ignore_index=True)
        table.to_csv(pace_path, index=False, encoding='utf-8-sig')
    return table


# ---------------------------------------------------------------- ranking & confidence
def rank_race(info, entries, history, paces):
    from keiba_core import apply_netkeiba_pace
    from keiba_main_conditions import checked_context
    from keiba_main_ranking import main_ranking
    active = entries.loc[entries['状態'].eq('出走予定')]
    horses = dict(zip(active['馬ID'], active['馬名']))
    context = checked_context(info['レースID'], {'race_date': info['開催日'], 'venue': info['競馬場'],
                                                 'surface': info['コース'], 'distance': info['距離'],
                                                 'name': info['レース名'], 'grade': info['格'] if info['格'] in GRADED else None,
                                                 'source': '出走表から取得'})
    if context is None:
        raise ValueError(f"{info['レースID']}: 開催条件を確認できません")
    graded = info['格'] in GRADED
    data = history.loc[history['馬ID'].astype(str).isin(horses)].copy() if not history.empty else history
    known = paces.loc[paces['状態'].eq('成功')]
    if not data.empty and len(known):
        data = apply_netkeiba_pace(data, dict(zip(known['レースID'], known['netkeibaペース'])))
    meta = active.rename(columns={'騎手': '予定騎手'}).assign(取得元=ENTRY_URL + info['レースID'], 想定人気=float('nan'))
    per_pace = {}
    for pace in 'SMH':
        if data.empty:
            r = pd.DataFrame({'馬ID': list(horses), '馬名': list(horses.values()), '参考順位': pd.NA,
                              '総合参考点': float('nan'), '評価項目充足率': float('nan'), '近5走有効数': float('nan'),
                              '判定': '戦績なし（新馬など）'})
        else:
            r = main_ranking(data, horses, context, meta, pace, graded_only=graded)
        per_pace[pace] = r.set_index('馬ID')
    return context, graded, per_pace


def confidence(entries, cache, per_pace):
    active = entries.loc[entries['状態'].eq('出走予定')]
    n = len(active)
    if not n:
        return {}
    states = active['馬ID'].map(cache.status)
    checked = states.isin(['成功', '戦績なし確認']).mean()
    shown = {p: per_pace[p]['参考順位'].notna().sum() / n for p in 'SMH'}
    coverage = pd.concat([pd.to_numeric(per_pace[p]['評価項目充足率'], errors='coerce').reindex(active['馬ID'])
                          for p in 'SMH']).fillna(0).mean() / 100
    valid = pd.to_numeric(per_pace['M']['近5走有効数'], errors='coerce').reindex(active['馬ID']).fillna(0).mean()
    min_shown = min(shown.values())
    score = checked * 25 + min_shown * 30 + coverage * 30 + (valid / 5) * 15
    if min_shown == 0:
        label = '全展開での総合評価は保留（' + '・'.join(f'{p}{shown[p]*n:.0f}頭' for p in 'SMH') + '）'
    else:
        label = '高' if score >= 90 else '中' if score >= 75 else '低'
    return {'頭数': n, '戦績確認率': checked, 'S順位表示率': shown['S'], 'M順位表示率': shown['M'],
            'H順位表示率': shown['H'], '順位表示率(最少)': min_shown, '平均項目充足率': coverage,
            '近5走平均有効数': valid, '信頼度点': score, '信頼度': label,
            '戦績あり': int(states.eq('成功').sum()), '戦績なし確認': int(states.eq('戦績なし確認').sum()),
            '取得不可': int((~states.isin(['成功', '戦績なし確認'])).sum())}


def top_names(ranking, entries, k):
    numbers = entries.set_index('馬ID')['馬番']
    ranked = ranking.loc[ranking['参考順位'].notna()].sort_values(['参考順位', '総合参考点'], ascending=[True, False])
    out = []
    for hid, row in ranked.head(k).iterrows():
        num = numbers.get(hid)
        out.append(f"{'' if pd.isna(num) else str(int(num)) + ' '}{row['馬名']}（{int(row['参考順位'])}位 {row['総合参考点']:.1f}）")
    return '／'.join(out) if out else '全頭保留'


# ---------------------------------------------------------------- run
RUN_REQUEST_CAP = 2500  # 1回の実行で送る要求の上限（間隔・拒否時停止は共通ゲートのまま）
FRESH_HOURS = 6  # 出走表・一覧はこれより古ければ取り直す（確定・馬番・取消の反映）。戦績とS/M/Hは再利用。


def fresh(path, hours=FRESH_HOURS):
    import time
    return path.exists() and time.time() - path.stat().st_mtime < hours * 3600


def write_status(work, **fields):
    path = Path(work) / 'status.json'
    current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    current.update(fields, 更新日時=now_text())
    path.write_text(json.dumps(current, ensure_ascii=False, default=str), encoding='utf-8')
    return current


def read_status(work):
    path = Path(work) / 'status.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def run(dates, work, out, races=None, report_only=False, cap=RUN_REQUEST_CAP):
    """取得（未取得分のみ）→ 集計 → 出力。停止しても途中まで保存し、同じ引数で再開できる。"""
    from keiba_fetch_policy import POLICY
    work, out = Path(work), Path(out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    cache = HistoryCache(work / 'horses')
    status = '完了'
    infos, entries_all = [], pd.DataFrame()
    write_status(work, 状態='実行中', 段階='レース一覧', 開始日時=now_text(), 対象日=list(dates))
    try:
        with POLICY.budget(cap):
            lists = []
            for date in dates:
                path = work / f'race_list_{date}.csv'
                if not fresh(path) and not report_only:
                    parse_race_list(fetch_text(LIST_URL + date), date).to_csv(path, index=False, encoding='utf-8-sig')
                if path.exists():
                    lists.append(pd.read_csv(path, dtype={'レースID': str}))
            candidates = pd.concat(lists, ignore_index=True) if lists else pd.DataFrame(columns=['レースID', 'コース'])
            candidates = candidates.loc[candidates['コース'].eq('芝')]
            if races:
                candidates = candidates.loc[candidates['レースID'].isin(races)]
            entry_dir = work / 'entries'
            entry_dir.mkdir(exist_ok=True)
            for i, rid in enumerate(candidates['レースID'], 1):
                write_status(work, 段階=f'出走表 {i}/{len(candidates)}')
                if not fresh(entry_dir / f'{rid}.json') and not report_only:
                    info, entries = parse_entry_page(fetch_text(ENTRY_URL + rid), rid)
                    listed = candidates.loc[candidates['レースID'].eq(rid), '日付'].iloc[0]
                    if not info['開催日']:
                        info['開催日'] = listed  # 一覧ページの開催日で補う（出走表に日付表記がない場合）
                    elif info['開催日'] != listed:
                        raise Stopped(f'{rid}: 出走表の開催日{info["開催日"]}と一覧の{listed}が一致しません')
                    entries.to_csv(entry_dir / f'{rid}.csv', index=False, encoding='utf-8-sig')
                    (entry_dir / f'{rid}.json').write_text(json.dumps(info, ensure_ascii=False, default=str), encoding='utf-8')
            frames = []
            for rid in candidates['レースID']:
                if (entry_dir / f'{rid}.json').exists():
                    info = json.loads((entry_dir / f'{rid}.json').read_text(encoding='utf-8'))
                    if info.get('コース') != '芝':
                        continue  # 一覧と出走表の両方で芝のみ（障害・ダートを除外）
                    infos.append(info)
                    frames.append(pd.read_csv(entry_dir / f'{rid}.csv', dtype={'馬ID': str, 'レースID': str}))
            entries_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            race_frame = pd.DataFrame(infos)
            if not report_only and not entries_all.empty:
                write_status(work, 段階='戦績', 対象レース=len(infos), 対象馬=int(entries_all['馬ID'].nunique()))
                fetch_histories(entries_all.loc[entries_all['状態'].eq('出走予定')], cache, race_frame)
            wanted = set()
            for info in infos:
                ent = entries_all.loc[entries_all['レースID'].eq(info['レースID'])]
                wanted |= pace_targets(race_history(ent, cache), info['開催日'], info['格'] in GRADED)
            if not report_only:
                write_status(work, 段階='S/M/H', 照合対象=len(wanted))
                fetch_paces(wanted, work / 'pace.csv')
    except Stopped as exc:
        until = POLICY.cooldown_until()
        status = (f'停止：{exc}' + (f'（{__import__("keiba_fetch_policy").format_time(until)} 以降に再開できます）' if until else '')
                  + '。取得済み分は保存済みで、同じ操作で続きから再開します')
    except Exception as exc:  # noqa: BLE001 - reported to the screen, partial data kept
        status = f'エラーで停止：{type(exc).__name__}: {str(exc)[:200]}'
    paces = pd.read_csv(work / 'pace.csv', dtype=str) if (work / 'pace.csv').exists() else pd.DataFrame(
        columns=['レースID', 'netkeibaペース', '状態', '理由'])
    write_status(work, 段階='集計')
    try:
        report(infos, entries_all, cache, paces, out, work, status, list(dates))
    except Exception as exc:  # noqa: BLE001
        status = f'{status}／集計エラー：{type(exc).__name__}: {str(exc)[:200]}'
    write_status(work, 状態='完了' if status == '完了' else '停止', 段階='終了', 結果=status, 終了日時=now_text())
    return status


_START_LOCK = threading.Lock()


def _thread_name(work):
    return 'keiba-weekend:' + str(Path(work))


def is_running(work):
    """スレッド名で判定（モジュールの再読込後も二重実行しない）。"""
    name = _thread_name(work)
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def start_background(dates, work, out, races=None):
    """サーバー上で実行（画面を閉じても続行）。同じ作業フォルダの二重実行はしない。"""
    with _START_LOCK:
        if is_running(work):
            return False
        thread = threading.Thread(target=run, args=(list(dates), work, out, races), daemon=True, name=_thread_name(work))
        thread.start()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dates', nargs='+', required=True, help='YYYYMMDD')
    ap.add_argument('--work', required=True, help='非公開の作業フォルダ（取得データ・再開用）')
    ap.add_argument('--out', required=True, help='出力フォルダ')
    ap.add_argument('--races', nargs='*', default=None, help='対象レースIDを限定')
    ap.add_argument('--report-only', action='store_true')
    args = ap.parse_args(argv)
    status = run(args.dates, args.work, args.out, args.races, args.report_only)
    print(status)
    return 0 if status == '完了' else 3


def report(infos, entries_all, cache, paces, out, work, status, dates):
    race_rows, horse_rows, top_rows = [], [], []
    for info in infos:
        rid = info['レースID']
        ent = entries_all.loc[entries_all['レースID'].eq(rid)]
        history = race_history(ent.loc[ent['状態'].eq('出走予定')], cache)
        try:
            context, graded, per_pace = rank_race(info, ent, history, paces)
        except ValueError as exc:
            race_rows.append({**info, '注意点': f'参考順位を保留：{exc}'})
            continue
        conf = confidence(ent, cache, per_pace)
        wanted = pace_targets(history, info['開催日'], graded)
        got = paces.loc[paces['レースID'].isin(wanted)]
        notes = []
        if conf['取得不可']:
            notes.append(f"戦績取得不可{conf['取得不可']}頭")
        if conf['戦績なし確認']:
            notes.append(f"戦績なし{conf['戦績なし確認']}頭（新馬など）")
        if ent['馬番'].isna().any():
            notes.append('馬番未確定あり')
        if (ent['状態'] != '出走予定').any():
            notes.append('取消・除外あり')
        race_rows.append({
            '日付': info['開催日'], '発走': info['発走'], '競馬場': info['競馬場'], 'R': int(rid[-2:]), 'レース名': info['レース名'],
            'レースID': rid, 'コース': f"芝{info['距離']}m", '格': info['格'], '比較対象': 'G1〜G3' if graded else '全クラス',
            **{k: conf[k] for k in ['頭数', '戦績あり', '戦績なし確認', '取得不可']},
            'S/M/H照合対象レース': len(wanted), 'S/M/H確認済み': int(got['状態'].eq('成功').sum()),
            'S/M/H取得不可': int(got['状態'].eq('取得不可').sum()),
            **{f'{p}順位表示': int(per_pace[p]['参考順位'].notna().sum()) for p in 'SMH'},
            **{f'{p}上位3': top_names(per_pace[p], ent, 3) for p in 'SMH'},
            **{f'{p}上位5': top_names(per_pace[p], ent, 5) for p in 'SMH'},
            '戦績確認率(%)': round(conf['戦績確認率'] * 100, 1), '順位表示率最少(%)': round(conf['順位表示率(最少)'] * 100, 1),
            '平均項目充足率(%)': round(conf['平均項目充足率'] * 100, 1), '近5走平均有効数': round(conf['近5走平均有効数'], 2),
            '信頼度点': round(conf['信頼度点'], 1), '信頼度': conf['信頼度'], '_score': conf['信頼度点'],
            '_valid': conf['近5走平均有効数'], '_ok': conf['戦績確認率'] >= 1 and conf['順位表示率(最少)'] >= .8 and conf['平均項目充足率'] >= .7,
            '注意点': '・'.join(notes) or '—'})
        numbers = ent.set_index('馬ID')['馬番']
        jockeys = ent.set_index('馬ID')['騎手']
        for hid in ent['馬ID']:
            row = {'日付': info['開催日'], '競馬場': info['競馬場'], 'R': int(rid[-2:]), 'レース名': info['レース名'], 'レースID': rid,
                   '馬番': numbers.get(hid), '馬名': ent.set_index('馬ID')['馬名'].get(hid), '騎手': jockeys.get(hid),
                   '状態': ent.set_index('馬ID')['状態'].get(hid), '戦績取得': cache.status(hid) or '未取得'}
            for p in 'SMH':
                r = per_pace[p].loc[hid] if hid in per_pace[p].index else None
                row[f'{p}順位'] = r['参考順位'] if r is not None else pd.NA
                row[f'{p}参考点'] = r['総合参考点'] if r is not None else pd.NA
                row[f'{p}充足率(%)'] = r['評価項目充足率'] if r is not None else pd.NA
                row[f'{p}判定'] = r['判定'] if r is not None else '取消・除外'
            r = per_pace['M'].loc[hid] if hid in per_pace['M'].index else None
            row['近5走有効数'] = r['近5走有効数'] if r is not None else pd.NA
            row['比較対象'] = 'G1〜G3' if graded else '全クラス'
            horse_rows.append(row)
        for p in 'SMH':
            ranked = per_pace[p].loc[per_pace[p]['参考順位'].notna()].sort_values(['参考順位', '総合参考点'], ascending=[True, False]).head(5)
            if ranked.empty:
                top_rows.append({'レースID': rid, 'レース名': info['レース名'], '想定ペース': p, '順位': '—', '馬番': '—',
                                 '馬名': '全頭保留', '参考点': '—'})
            for hid, r in ranked.iterrows():
                top_rows.append({'レースID': rid, 'レース名': info['レース名'], '想定ペース': p, '順位': int(r['参考順位']),
                                 '馬番': numbers.get(hid), '馬名': r['馬名'], '参考点': r['総合参考点']})
    races = pd.DataFrame(race_rows)
    if not races.empty and '_score' in races:
        sel = races.loc[races['_ok'].fillna(False).astype(bool)].sort_values(['_score', '_valid', 'レースID'], ascending=[False, False, True]).head(3)
        sel = sel.assign(データ信頼度順=range(1, len(sel) + 1))
        races = races.sort_values(['日付', '発走', '競馬場', 'R']).drop(columns=['_score', '_valid', '_ok'])
        sel = sel.drop(columns=['_score', '_valid', '_ok'])
    else:
        sel = pd.DataFrame()
    stamp = '_'.join(dates)
    files = {
        f'{stamp}_全芝レース一覧.csv': races,
        f'{stamp}_全頭展開別順位.csv': pd.DataFrame(horse_rows),
        f'{stamp}_展開別上位5頭.csv': pd.DataFrame(top_rows),
        f'{stamp}_分析信頼度上位.csv': sel,
    }
    for name, frame in files.items():
        frame.to_csv(out / name, index=False, encoding='utf-8-sig')
    from keiba_fetch_policy import POLICY
    meta = {'作成日時': now_text(), '状態': status, '対象日': dates, '対象レース数': len(infos),
            '比較': '条件戦・オープンは全クラス、G1〜G3は重賞実績で比較。対象日以降の戦績は使わない。',
            'S/M/H': 'netkeibaのラップ欄に明示された判定のみ。直近芝5走（重賞は直近重賞5走も）を照合。補完しない。',
            '参考点': '着順の頭数補正40%、同場同距離20%、同距離15%、同場10%、想定ペース別15%。近5走有効3走以上・充足率60%以上で順位。',
            '信頼度': SCORE_RULE, '取得先の状況': POLICY.snapshot()}
    (out / f'{stamp}_条件と取得状況.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    write_html(out / f'{stamp}_芝レース分析.html', races, sel, meta)
    with zipfile.ZipFile(out / f'{stamp}_ラボ取得データ.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(work.rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(work))


def write_html(path, races, sel, meta):
    from html import escape
    def table(frame, cols):
        if frame.empty:
            return '<p>該当なし</p>'
        head = ''.join(f'<th>{escape(c)}</th>' for c in cols)
        body = ''.join('<tr>' + ''.join(f'<td>{escape(str(r.get(c, "")))}</td>' for c in cols) + '</tr>' for _, r in frame.iterrows())
        return f'<div class="w"><table><tr>{head}</tr>{body}</table></div>'
    cols = ['日付', '発走', '競馬場', 'R', 'レース名', 'コース', '比較対象', '頭数', 'S上位3', 'M上位3', 'H上位3', '信頼度点', '信頼度', '注意点']
    html = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>芝レース分析</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#ddd;--head:#f3f3f3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--mute:#aaa;--line:#333;--head:#222}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Hiragino Sans",sans-serif}}
h1{{font-size:20px}}h2{{font-size:17px;margin-top:24px}}p,li{{color:var(--mute)}}
.w{{overflow-x:auto}}table{{border-collapse:collapse;min-width:100%}}td,th{{border:1px solid var(--line);padding:6px 8px;vertical-align:top;white-space:nowrap}}
th{{background:var(--head);position:sticky;top:0}}</style></head><body>
<h1>芝レース分析（ラボ参考順位）</h1><p>作成：{escape(meta['作成日時'])}／状態：{escape(meta['状態'])}</p>
<h2>データ信頼度 上位3レース</h2>{table(sel, ['データ信頼度順'] + cols)}
<h2>全芝レース</h2>{table(races, cols)}
<h2>条件</h2><ul>{''.join(f'<li>{escape(k)}：{escape(str(v))}</li>' for k, v in meta.items() if k in ('比較', 'S/M/H', '参考点', '信頼度'))}</ul>
<p>参考点・信頼度は実績とデータ充足の比較用で、勝率・期待値・的中率は検証していません。当日の馬場・取消・オッズは反映していません。</p>
</body></html>'''
    path.write_text(html, encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
