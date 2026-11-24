"""地方競馬の1レース取得（研究用）。浦和記念など、地方のダート重賞をラボで見るため。

- 取得は共通ゲート（取得先ごと1件ずつ・最小間隔・拒否が続けば停止）を通す。回避はしない。
- 手順：地方のレース一覧（その日）→ レース名でレースIDを探す → 出走表 → 各馬の戦績（db.netkeiba の馬ページ、JRAと同じ）。
- 出走表の形は JRA と同じ読み取りを試し、読めなかった場合も原文（HTML）を保存して形を確認できるようにする。
- 予想（順位）はここでは作らない。ダート版モデルは別に検証してから当てる。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pandas as pd

ROOT = Path('/tmp/keiba_analysis_v141/nar')
NAR_LIST_URL = 'https://nar.netkeiba.com/top/race_list_sub.html?kaisai_date='
NAR_ENTRY_URL = 'https://nar.netkeiba.com/race/shutuba.html?race_id='
NAR_RESULT_URL = 'https://nar.netkeiba.com/race/result.html?race_id='


def find_race(list_html, keyword):
    """一覧のHTMLから、レース名にキーワードを含むレースID（12桁）と表記を返す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(list_html, 'html.parser')
    hits = []
    for link in soup.select('a[href*="race_id="]'):
        rid = re.search(r'race_id=(\d{12})', link.get('href', ''))
        text = ' '.join(link.get_text(' ', strip=True).split())
        item = link.find_parent('li') or link
        whole = ' '.join(item.get_text(' ', strip=True).split())
        if rid and keyword in whole:
            hits.append({'レースID': rid[1], '一覧表記': whole or text})
    return pd.DataFrame(hits).drop_duplicates('レースID') if hits else pd.DataFrame(columns=['レースID', '一覧表記'])


def venue_races(list_html, venue):
    """一覧のHTMLから、その競馬場の全レース（レースID・一覧表記・発走）を返す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(list_html, 'html.parser')
    hits = []
    for box in soup.select('dl.RaceList_DataList'):
        head = ' '.join(box.get_text(' ', strip=True).split())[:40]
        if f'{venue}競馬場' not in head:
            continue
        for item in box.select('li'):
            link = item.select_one('a[href*="race_id="]')
            rid = re.search(r'race_id=(\d{12})', link.get('href', '')) if link else None
            if not rid:
                continue
            whole = ' '.join(item.get_text(' ', strip=True).split())
            time = re.search(r'(\d{1,2}:\d{2})', whole)
            hits.append({'レースID': rid[1], '一覧表記': whole, '発走': time[1] if time else ''})
    return pd.DataFrame(hits).drop_duplicates('レースID') if hits else pd.DataFrame(columns=['レースID', '一覧表記', '発走'])


def parse_entries(html, race_id):
    """出走表（地方）を読む。JRA と同じ表の作りなら parse_entry_page を使う。"""
    from bs4 import BeautifulSoup
    from keiba_weekend_batch import parse_entry_page
    try:
        info, entries = parse_entry_page(html, race_id)
        return info, entries
    except Exception:  # noqa: BLE001 - 形が違う場合は馬IDのリンクだけ読む
        soup = BeautifulSoup(html, 'html.parser')
        rows = []
        for tr in soup.select('tr'):
            link = tr.select_one('a[href*="/horse/"]')
            hit = re.search(r'/horse/(\w{10})', link.get('href', '')) if link else None
            if not hit:
                continue
            cells = [c.get_text(' ', strip=True) for c in tr.select('td')]
            number = next((c for c in cells[1:3] if c.isdecimal()), '')
            rows.append({'レースID': race_id, '馬ID': hit[1], '馬名': link.get('title') or link.get_text(strip=True),
                         '馬番': int(number) if number else pd.NA, '騎手': pd.NA, '状態': '出走予定'})
        entries = pd.DataFrame(rows).drop_duplicates('馬ID') if rows else pd.DataFrame()
        title = soup.select_one('.RaceName')
        return {'レースID': race_id, 'レース名': title.get_text(strip=True) if title else ''}, entries


def run_venue(work, date, venue, list_html, fetch_text):
    """その競馬場の、まだ発走していない全レース：出走表を先に全部、そのあと発走の早い順に各馬の戦績。"""
    from datetime import datetime, timedelta, timezone
    from keiba_weekend_batch import HistoryCache, fetch_histories
    races = venue_races(list_html, venue)
    now = datetime.now(timezone(timedelta(hours=9)))
    today = now.strftime('%Y%m%d')
    if date == today:
        races = races.loc[races['発走'].map(lambda t: not t or t.zfill(5) > now.strftime('%H:%M'))]
    if races.empty:
        write_status(work, 結果=f'{date} {venue} の対象レースがありません')
        return
    races = races.sort_values('発走')
    write_status(work, 段階=f'出走表 0/{len(races)}', 対象レース=len(races))
    loaded = []
    for i, row in enumerate(races.itertuples(), 1):
        path = work / f'entry_{row.レースID}.html'
        if not path.exists():
            path.write_text(fetch_text(NAR_ENTRY_URL + row.レースID), encoding='utf-8')
        info, entries = parse_entries(path.read_text(encoding='utf-8'), row.レースID)
        if entries.empty:
            continue
        entries.to_csv(work / f'entries_{row.レースID}.csv', index=False, encoding='utf-8-sig')
        (work / f'info_{row.レースID}.json').write_text(json.dumps({**info, '一覧表記': row.一覧表記, '発走': row.発走, '競馬場': venue,
                                                                  '開催日': f'{date[:4]}-{date[4:6]}-{date[6:]}'},
                                                                 ensure_ascii=False, default=str), encoding='utf-8')
        loaded.append((row.レースID, info, entries))
        write_status(work, 段階=f'出走表 {i}/{len(races)}')
    cache = HistoryCache(work / 'horses')
    for i, (rid, info, entries) in enumerate(loaded, 1):
        write_status(work, 段階=f'戦績 {i}/{len(loaded)}（{rid}）')
        fetch_histories(entries.loc[entries['状態'].eq('出走予定')], cache,
                        pd.DataFrame({'レースID': [rid], 'レース名': [info.get('レース名', '')]}))


def run_results(work, fetch_text):
    """取得済みの各レース（info_*.json）の結果ページ：着順と払戻を保存（1レース1回）。"""
    from keiba_backtest import parse_result_page
    ids = sorted(json.loads(f.read_text(encoding='utf-8'))['レースID'] for f in work.glob('info_*.json'))
    (work / 'results').mkdir(exist_ok=True)
    done = 0
    for i, rid in enumerate(ids, 1):
        write_status(work, 段階=f'結果 {i}/{len(ids)}')
        path = work / 'results' / f'{rid}.csv'
        if path.exists():
            done += 1
            continue
        html = fetch_text(NAR_RESULT_URL + rid)
        try:
            info, runners = parse_result_page(html, rid)
        except ValueError as exc:
            (work / 'results' / f'{rid}.error').write_text(str(exc)[:300], encoding='utf-8')
            (work / 'results' / f'{rid}.html').write_text(html, encoding='utf-8')
            continue
        runners.to_csv(path, index=False, encoding='utf-8-sig')
        (work / 'results' / f'{rid}_払戻.json').write_text(json.dumps(info.get('払戻', []), ensure_ascii=False), encoding='utf-8')
        done += 1
    write_status(work, 結果件数=done)


def write_status(work, **fields):
    from keiba_backtest import now_text
    path = Path(work) / 'status.json'
    current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    current.update(fields, 更新日時=now_text())
    path.write_text(json.dumps(current, ensure_ascii=False, default=str), encoding='utf-8')


def read_status(work):
    path = Path(work) / 'status.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def run(date, keyword, work, fetch_text=None):
    from keiba_fetch_policy import POLICY, PolicyStop
    from keiba_weekend_batch import HistoryCache, Stopped, fetch_histories, fetch_text as default_fetch
    fetch_text = fetch_text or default_fetch
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    write_status(work, 状態='実行中', 段階='レース一覧', 対象日=date, キーワード=keyword)
    try:
        with POLICY.budget(1500):
            list_path = work / f'list_{date}.html'
            if not list_path.exists():
                list_path.write_text(fetch_text(NAR_LIST_URL + date), encoding='utf-8')
            if keyword.startswith('結果'):
                run_results(work, fetch_text)
                write_status(work, 状態='完了', 段階='終了', 結果='結果取得 完了')
                return
            if keyword.startswith('場:'):
                run_venue(work, date, keyword[2:], list_path.read_text(encoding='utf-8'), fetch_text)
                write_status(work, 状態='完了', 段階='終了', 結果='完了')
                return
            found = find_race(list_path.read_text(encoding='utf-8'), keyword)
            if found.empty:
                write_status(work, 状態='停止', 段階='終了', 結果=f'{date} の地方の一覧に「{keyword}」が見つかりません')
                return
            race_id = found.iloc[0]['レースID']
            write_status(work, 段階='出走表', レースID=race_id, 一覧表記=found.iloc[0]['一覧表記'])
            entry_html = work / f'entry_{race_id}.html'
            entry_html.write_text(fetch_text(NAR_ENTRY_URL + race_id), encoding='utf-8')
            info, entries = parse_entries(entry_html.read_text(encoding='utf-8'), race_id)
            if entries.empty:
                write_status(work, 状態='停止', 段階='終了', 結果='出走表を読めませんでした（原文を保存済み）')
                return
            entries.to_csv(work / f'entries_{race_id}.csv', index=False, encoding='utf-8-sig')
            (work / f'info_{race_id}.json').write_text(json.dumps({**info, '一覧表記': found.iloc[0]['一覧表記'], '開催日':
                                                                  f'{date[:4]}-{date[4:6]}-{date[6:]}'}, ensure_ascii=False,
                                                                 default=str), encoding='utf-8')
            write_status(work, 段階='戦績', 対象馬=len(entries))
            cache = HistoryCache(work / 'horses')
            fetch_histories(entries.loc[entries['状態'].eq('出走予定')],
                            cache, pd.DataFrame({'レースID': [race_id], 'レース名': [info.get('レース名', '')]}))
        write_status(work, 状態='完了', 段階='終了', 結果='完了')
    except (Stopped, PolicyStop) as exc:
        write_status(work, 状態='停止', 段階='終了', 結果=f'停止：{str(exc)[:160]}（取得済み分は保存済み）')
    except Exception as exc:  # noqa: BLE001
        write_status(work, 状態='停止', 段階='終了', 結果=f'停止：{type(exc).__name__}: {str(exc)[:160]}')


def _thread_name(work):
    return f'keiba-nar:{work}'


def is_running(work):
    return any(t.name == _thread_name(work) and t.is_alive() for t in threading.enumerate())


def start_background(date, keyword, work):
    if is_running(work):
        return False
    threading.Thread(target=run, args=(date, keyword, work), daemon=True, name=_thread_name(work)).start()
    return True


def render_nar_single():
    """管理・検証（研究用）：地方の1レース取得。"""
    import streamlit as st
    from keiba_backtest import export_zip
    from keiba_fetch_policy import POLICY, format_time
    with st.expander('地方の1レース取得（研究用）', expanded=False):
        st.caption('地方競馬の1レース（浦和記念など）の出走表と各馬の戦績を、共通ゲートを通して集めます。予想はここでは作りません。'
                   'サーバーの再起動で消えるので、終わったらZIPを保存してください。')
        date = st.text_input('開催日（YYYYMMDD）', value='20261125', key='nar_date_v78')
        keyword = st.text_input('レース名（一部）。競馬場の全レースは「場:浦和」、取得済みレースの結果は「結果」', value='浦和記念', key='nar_keyword_v78')
        work = ROOT / re.sub(r'\D', '', date)
        running = is_running(work)
        until = POLICY.cooldown_until()
        if until and not running:
            st.warning(f'取得先の制限のため {format_time(until)} まで開始できません。')
        if st.button('地方の1レースを取得', disabled=running or bool(until) or not re.fullmatch(r'20\d{6}', date),
                     key='nar_start_v78', width='stretch'):
            st.success('開始しました。' if start_background(date, keyword, work) else 'すでに実行中です。')
            running = True
        st.button('状況を更新', key='nar_refresh_v78')
        status = read_status(work)
        if status:
            st.write(f"状態：**{'実行中' if running else status.get('状態', '—')}**　段階：{status.get('段階', '—')}　"
                     f"更新：{status.get('更新日時', '—')}")
            for key in ('レースID', '一覧表記', '対象馬', '結果'):
                if status.get(key):
                    st.caption(f'{key}：{status[key]}')
        if work.exists() and not running and status:
            zip_path = work.parent / f'nar_{work.name}.zip'
            if st.button('ZIPを作成', key='nar_zip_v78'):
                export_zip(work, zip_path)
            if zip_path.exists():
                st.download_button(zip_path.name, data=zip_path.read_bytes(), file_name=zip_path.name,
                                   mime='application/zip', key='nar_dl_v78', width='stretch')
