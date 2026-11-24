"""過去レースの検証データ（全出走馬・結果・各馬の戦績）を集める。研究用。

- 対象：指定日のJRA芝レース（障害を除く）。レースごとに結果ページの全出走馬を保存する。
- 各馬の戦績は馬ID単位で1回だけ取得し、週末一括分析と同じ形式で保存する（対象日での切り取りは分析側で行う）。
- 対象レースの公表ペース（S/M/H）は展開予測の検証にだけ使う。予測特徴量には使わない。
- netkeibaへの要求はすべて共通ゲート（keiba_fetch_policy）を通す。止まったら保存して終了し、同じ指定で再開する。

取得データは非公開の作業フォルダにだけ保存する（リポジトリ・公開画面へ置かない）。
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
RESULT_URL = 'https://race.netkeiba.com/race/result.html?race_id='
ROOT = Path('/tmp/keiba_analysis_v141/backtest')
RUN_REQUEST_CAP = 4000
FORMAT = 'keiba-backtest-v1'


def now_text():
    return datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')


def _text(node):
    return node.get_text(' ', strip=True) if node is not None else ''


def parse_result_page(html, race_id):
    """結果ページから条件の原文と全出走馬を読む。値は原文のまま残し、数値化は分析側で行う。"""
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.select_one('h1.RaceName') or soup.select_one('.RaceName')
    data1, data2 = _text(soup.select_one('.RaceData01')), _text(soup.select_one('.RaceData02'))
    icons = sorted({c for span in (title.select('.Icon_GradeType') if title else []) for c in (span.get('class') or [])
                    if c.startswith('Icon_GradeType') and c != 'Icon_GradeType'})
    course = re.search(r'(芝|ダ|障)\s*(\d{3,4})m', data1)
    going = re.search(r'馬場\s*[:：]\s*(\S+)', data1) or re.search(r'/\s*(良|稍重|稍|重|不良|不)\s*/', data1)
    info = {'レースID': race_id, 'レース名': _text(title), '条件欄1': data1, '条件欄2': data2, 'グレード表示': ' '.join(icons),
            'コース': course[1] if course else '', '距離': int(course[2]) if course else None,
            '馬場': going[1] if going else '', '取得元': RESULT_URL + race_id, '取得日時': now_text(), '形式': FORMAT}
    table = soup.select_one('#All_Result_Table')
    if table is None:
        raise ValueError(f'{race_id}: 結果表が見つかりません')
    header = [re.sub(r'\s+', '', _text(th)) for th in table.select('thead th') or table.select('tr th')]
    rows = []
    for tr in table.select('tbody tr'):
        cells = tr.select('td')
        if len(cells) < 5:
            continue
        values = {header[i] if i < len(header) else f'列{i}': _text(td) for i, td in enumerate(cells)}
        link = tr.select_one('a[href*="/horse/"]')
        hit = re.search(r'/horse/(\d{10})', link.get('href', '')) if link else None
        if not hit:
            raise ValueError(f'{race_id}: 出走馬IDを読めない行があります')
        rows.append({'レースID': race_id, '馬ID': hit[1], '馬名': _text(link), '着順原文': values.get('着順', ''),
                     '枠': values.get('枠', ''), '馬番': values.get('馬番', ''), '性齢': values.get('性齢', ''),
                     '斤量': values.get('斤量', ''), '騎手': values.get('騎手', ''), 'タイム': values.get('タイム', ''),
                     '着差': values.get('着差', ''), '人気': values.get('人気', ''), '単勝': values.get('単勝オッズ', ''),
                     '後3F': values.get('後3F', ''), '通過': values.get('コーナー通過順', ''),
                     '馬体重': values.get('馬体重(増減)', '')})
    runners = pd.DataFrame(rows)
    if runners.empty or runners['馬ID'].duplicated().any():
        raise ValueError(f'{race_id}: 出走馬の件数または重複に問題があります')
    info['頭数'] = len(runners)
    info['払戻'] = parse_payouts(html)
    return info, runners


BET_TYPES = ('単勝', '複勝', '枠連', '馬連', 'ワイド', '馬単', '三連複', '三連単')
BET_SIZE = {'単勝': 1, '複勝': 1, '枠連': 2, '馬連': 2, 'ワイド': 2, '馬単': 2, '三連複': 3, '三連単': 3}


def parse_payouts(html):
    """結果ページの払戻表を読む。[{券種, 組番, 払戻}]（払戻は100円あたりの円）。読めなければ空のリスト。
    同じページの中身なので、取得の回数は増えない。原文も残す（形が変わったときに確かめるため）。"""
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for tr in soup.select('table.Payout_Detail_Table tr, .Result_Pay_Back table tr'):
        kind = re.sub(r'\s+', '', _text(tr.select_one('th')))
        kind = {'3連複': '三連複', '3連単': '三連単'}.get(kind, kind)
        if kind not in BET_TYPES:
            continue
        nums = re.findall(r'\d+', _text(tr.select_one('td.Result')))
        pays = [int(x.replace(',', '')) for x in re.findall(r'([\d,]+)\s*円', _text(tr.select_one('td.Payout')))]
        k = BET_SIZE[kind]
        combos = ['-'.join(nums[i:i + k]) for i in range(0, len(nums) - len(nums) % k, k)]
        if kind in ('単勝', '複勝') or len(combos) != len(pays):
            combos = combos[:len(pays)] if len(combos) >= len(pays) else combos + [''] * (len(pays) - len(combos))
        for c, y in zip(combos, pays):
            out.append({'券種': kind, '組番': c, '払戻': y, '原文': _text(tr)[:120]})
    seen, unique = set(), []
    for row in out:
        key = (row['券種'], row['組番'], row['払戻'])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def race_ids_for(list_html, surface='芝'):
    """レース一覧（race_list_sub）から芝（または surface='ダ' でダート）・平地のレースIDと一覧表記を返す。"""
    soup = BeautifulSoup(list_html, 'html.parser')
    out = []
    for item in soup.select('li.RaceList_DataItem'):
        link = item.select_one('a[href*="race_id="]')
        hit = re.search(r'race_id=(\d{12})', link.get('href', '')) if link else None
        text = _text(item)
        course = re.search(r'(芝|ダ|障)\s*(\d{3,4})m', text)
        if hit and course and course[1] == surface and '障害' not in text:
            out.append({'レースID': hit[1], '一覧表記': text})
    return pd.DataFrame(out).drop_duplicates('レースID') if out else pd.DataFrame(columns=['レースID', '一覧表記'])


def write_status(work, **fields):
    path = Path(work) / 'status.json'
    current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    current.update(fields, 更新日時=now_text())
    path.write_text(json.dumps(current, ensure_ascii=False, default=str), encoding='utf-8')


def read_status(work):
    path = Path(work) / 'status.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def run(dates, work, cap=RUN_REQUEST_CAP, fetch_text=None, history_loader=None, pace_loader=None, surface=None):
    """収集を進める（未取得分のみ）。止まっても保存済みから再開できる。戻り値は状態の文字列。"""
    from keiba_fetch_policy import POLICY, PolicyStop, format_time
    from keiba_weekend_batch import HistoryCache, Stopped, LIST_URL, fetch_text as default_fetch
    fetch_text = fetch_text or default_fetch
    if history_loader is None:
        from keiba_core import get_results as history_loader
    if pace_loader is None:
        from keiba_core import get_netkeiba_pace as pace_loader
    from keiba_main_integration import PACE_UNAVAILABLE
    work = Path(work)
    (work / 'races').mkdir(parents=True, exist_ok=True)
    cache = HistoryCache(work / 'horses')
    status = '完了'
    surface = read_status(work).get('コース') or surface or '芝'  # 再開時は最初に決めたコースを使う
    write_status(work, 状態='実行中', 段階='レース一覧', 対象日=list(dates), 開始日時=now_text(), 形式=FORMAT, コース=surface)
    try:
        with POLICY.budget(cap):
            targets = []
            for date in dates:
                path = work / f'list_{date}.csv'
                if not path.exists():
                    race_ids_for(fetch_text(LIST_URL + date), surface).assign(日付=f'{date[:4]}-{date[4:6]}-{date[6:]}').to_csv(
                        path, index=False, encoding='utf-8-sig')
                targets.append(pd.read_csv(path, dtype={'レースID': str}))
            targets = pd.concat(targets, ignore_index=True) if targets else pd.DataFrame(columns=['レースID', '日付'])
            for i, row in enumerate(targets.itertuples(), 1):
                rid = row.レースID
                write_status(work, 段階=f'結果 {i}/{len(targets)}', 対象レース=len(targets))
                if (work / 'races' / f'{rid}.json').exists() or (work / 'races' / f'{rid}.error').exists():
                    continue
                try:
                    info, runners = parse_result_page(fetch_text(RESULT_URL + rid), rid)
                except ValueError as exc:
                    (work / 'races' / f'{rid}.error').write_text(str(exc)[:300], encoding='utf-8')
                    continue
                info.update(開催日=row.日付, 一覧表記=row.一覧表記)
                runners.to_csv(work / 'races' / f'{rid}.csv', index=False, encoding='utf-8-sig')
                (work / 'races' / f'{rid}.json').write_text(json.dumps(info, ensure_ascii=False, default=str), encoding='utf-8')
            infos = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((work / 'races').glob('*.json'))]
            runners = pd.concat([pd.read_csv(work / 'races' / f"{x['レースID']}.csv", dtype=str) for x in infos],
                                ignore_index=True) if infos else pd.DataFrame(columns=['馬ID', '馬名', 'レースID'])
            debut = {x['レースID'] for x in infos if '新馬' in x.get('レース名', '') + x.get('条件欄2', '')}
            horses = runners.drop_duplicates('馬ID')
            write_status(work, 段階='戦績', 対象馬=len(horses))
            for _, horse in horses.iterrows():
                hid, name = horse['馬ID'], horse['馬名']
                if cache.status(hid) in ('成功', '戦績なし確認'):
                    continue
                races_of = set(runners.loc[runners['馬ID'].eq(hid), 'レースID'])
                if races_of <= debut:
                    cache.record(hid, name, '戦績なし確認', '新馬戦のみ（対象日前の戦績なし）')
                    continue
                try:
                    raw = history_loader(hid, name)
                except PolicyStop:
                    raise
                except ValueError as exc:
                    cache.record(hid, name, '戦績なし確認' if '成績' in str(exc) else '取得不可', str(exc)[:180])
                    continue
                except Exception as exc:  # noqa: BLE001 - recorded; the gate decides whether to stop
                    cache.record(hid, name, '取得不可', str(exc)[:180])
                    if POLICY.cooldown_until():
                        raise Stopped(str(exc)) from exc
                    continue
                raw.to_csv(cache.path(hid), index=False, encoding='utf-8-sig')
                cache.record(hid, name, '成功', f'{len(raw)}行')
            pace_path = work / 'target_pace.csv'
            paces = pd.read_csv(pace_path, dtype=str) if pace_path.exists() else pd.DataFrame(columns=['レースID', '公表ペース', '状態', '理由'])
            done = set(paces['レースID'])
            write_status(work, 段階='対象レースの公表ペース')
            for info in infos:
                rid = info['レースID']
                if rid in done:
                    continue
                try:
                    row = {'レースID': rid, '公表ペース': pace_loader(rid), '状態': '成功', '理由': ''}
                except PolicyStop:
                    raise
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)[:180]
                    row = {'レースID': rid, '公表ペース': '', '理由': message,
                           '状態': '取得不可' if isinstance(exc, ValueError) and message in PACE_UNAVAILABLE else '再取得待ち'}
                    if POLICY.cooldown_until():
                        raise Stopped(message) from exc
                paces = pd.concat([paces.loc[~paces['レースID'].eq(rid)], pd.DataFrame([row])], ignore_index=True)
                paces.to_csv(pace_path, index=False, encoding='utf-8-sig')
    except (Stopped, PolicyStop) as exc:
        until = POLICY.cooldown_until()
        status = f'停止：{str(exc)[:160]}' + (f'（{format_time(until)} 以降に再開できます）' if until else '') + '。取得済み分は保存済み'
    except Exception as exc:  # noqa: BLE001
        status = f'エラーで停止：{type(exc).__name__}: {str(exc)[:200]}'
    write_status(work, 状態='完了' if status == '完了' else '停止', 段階='終了', 結果=status, 終了日時=now_text())
    return status


def export_zip(work, path):
    import zipfile
    work = Path(work)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in sorted(work.rglob('*')):
            if item.is_file():
                z.write(item, item.relative_to(work))
    return path


def _thread_name(work):
    return 'keiba-backtest:' + str(Path(work))


def is_running(work):
    return any(t.name == _thread_name(work) and t.is_alive() for t in threading.enumerate())


_LOCK = threading.Lock()


def start_background(dates, work, surface='芝'):
    with _LOCK:
        if is_running(work):
            return False
        threading.Thread(target=run, args=(list(dates), work), kwargs={'surface': surface}, daemon=True,
                         name=_thread_name(work)).start()
    return True


def render_backtest_collector():
    """管理・検証（研究用）：過去レースの検証データ収集。"""
    import streamlit as st
    from keiba_fetch_policy import POLICY, format_time
    with st.expander('過去レースの検証データ収集（研究用）', expanded=False):
        st.caption('指定日のJRA芝（またはダート）レースについて、結果ページの全出走馬と各馬の戦績、対象レースの公表ペースを集めます。'
                   '取得は共通ゲート（取得先ごと1件ずつ・最小間隔・拒否が続けば停止）を通し、サーバー上で進みます。'
                   'サーバーの再起動で保存データは消えるので、終わったらZIPを保存してください。')
        text = st.text_area('対象日（YYYYMMDD、カンマ区切り）', key='backtest_dates_v51', height=80)
        dates = [d for d in re.findall(r'20\d{6}', text)]
        name = st.text_input('作業名（英数字）', value='autumn2026', key='backtest_name_v51')
        surface = {'芝': '芝', 'ダート': 'ダ'}[st.radio('コース', ['芝', 'ダート'], horizontal=True, key='backtest_surface_v77')]
        work = ROOT / re.sub(r'[^A-Za-z0-9_-]', '', name or 'default')
        running = is_running(work)
        until = POLICY.cooldown_until()
        status = read_status(work)
        if until and not running:
            st.warning(f'取得先の制限のため {format_time(until)} まで開始できません。')
        if st.button('収集を開始／続きから再開', disabled=running or bool(until) or not dates, key='backtest_start_v51',
                     width='stretch'):
            st.success('開始しました。' if start_background(dates, work, surface) else 'すでに実行中です。')
            running = True
        st.button('状況を更新', key='backtest_refresh_v51')
        if status:
            st.write(f"状態：**{'実行中' if running else status.get('状態', '—')}**　段階：{status.get('段階', '—')}　"
                     f"更新：{status.get('更新日時', '—')}")
            if status.get('結果'):
                st.caption('結果：' + str(status['結果']))
        if work.exists() and not running and status:
            zip_path = work.parent / f'{work.name}.zip'
            if st.button('ZIPを作成', key='backtest_zip_v51'):
                export_zip(work, zip_path)
            if zip_path.exists():
                st.download_button(zip_path.name, data=zip_path.read_bytes(), file_name=zip_path.name,
                                   mime='application/zip', key='backtest_dl_v51', width='stretch')
