import re
import json
from pathlib import Path
import time
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='競馬分析ラボ', page_icon='🏇', layout='wide')
st.title('🏇 競馬分析ラボ Ver.15.8.1')
st.caption('自動取得・上がり順位・ペース別適性・脚質分析')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
VENUES = ['札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉']
METRICS = ['勝率(%)', '連対率(%)', '複勝率(%)', '平均着順', '平均4角', '平均上がり', '上がり最速率(%)', '上がり3位以内率(%)', '平均上がり順位', '前方率(%)', '差し追込率(%)']
NUMERIC = ['距離_m', '着順_数値', '上がり3F_秒', '4角順位', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差']
ALIASES = {'馬名': ['馬名', '馬名_正規化'], '競馬場': ['競馬場', '場所', '開催場'], '馬場状態': ['馬場状態', '馬 場', '馬場'], '距離_m': ['距離_m', '距離', '距離(m)'], '着順_数値': ['着順_数値', '着順'], '上がり3F_秒': ['上がり3F_秒', '上がり3F', '上り', '上がり'], '4角順位': ['4角順位', '4角', '4角通過順位'], '上がり順位': ['上がり順位', '上り順位'], '日付': ['日付', '年月日', '開催日', 'レース日'], '頭数': ['頭数', '頭 数'], '前半3F': ['前半3F'], '後半3F': ['後半3F'], '前後半3F差': ['前後半3F差'], '推定ペース': ['推定ペース'], '推定脚質': ['推定脚質'], 'レース格': ['レース格', '格付け', 'グレード'], 'レース名': ['レース名'], '格付け根拠': ['格付け根拠']}



def tap_choice(label, options, key, format_func=None):
    """iPhone向け：検索欄のないポップオーバー内ラジオ選択。"""
    choices = list(options)
    if not choices:
        return None
    if key not in st.session_state or st.session_state[key] not in choices:
        st.session_state[key] = choices[0]
    current = st.session_state[key]
    shown = str(format_func(current) if format_func else current)
    with st.popover(f'{label}：{shown}', use_container_width=True):
        # 選択はラジオボタン。iPhoneの文字入力キーボードは開かない。
        radio_args = {'key': key, 'label_visibility': 'collapsed'}
        if format_func is not None:
            radio_args['format_func'] = format_func
        st.radio(label, choices, **radio_args)
    return st.session_state[key]


def tap_horses(label, options, key, limit=3):
    """比較馬も検索入力を使わずチェックボックスで選択する。"""
    choices = list(options)
    if key not in st.session_state:
        st.session_state[key] = choices[:2]
    prior = [x for x in st.session_state[key] if x in choices][:limit]
    with st.expander(f'{label}：' + ('、'.join(prior) if prior else '未選択')):
        selected = []
        for i, horse in enumerate(choices):
            checked = st.checkbox(horse, value=horse in prior, key=f'{key}_check_{i}')
            if checked:
                selected.append(horse)
        if len(selected) > limit:
            st.warning(f'{limit}頭まで選べます。先頭の{limit}頭を使用します。')
        selected = selected[:limit]
        st.session_state[key] = selected
    return st.session_state[key]


# サーバー上の簡易キャッシュ。Streamlit Cloudの再起動・再デプロイで消える場合があります。
CACHE_DIR = Path('/tmp/keiba_analysis_v141')
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_paths(rid):
    if not re.fullmatch(r'\d{12}', str(rid)):
        raise ValueError('不正なレースIDです')
    return CACHE_DIR / f'{rid}.csv.gz', CACHE_DIR / f'{rid}.json'


def save_race_cache(rid, horses, data):
    if not rid or data is None or data.empty:
        return
    csv_path, meta_path = cache_paths(rid)
    temp_csv = csv_path.with_suffix('.tmp')
    temp_meta = meta_path.with_suffix('.tmp')
    data.to_csv(temp_csv, index=False, compression='gzip')
    temp_csv.replace(csv_path)
    temp_meta.write_text(json.dumps(horses, ensure_ascii=False), encoding='utf-8')
    temp_meta.replace(meta_path)


def load_race_cache(rid):
    csv_path, meta_path = cache_paths(rid)
    if not (csv_path.exists() and meta_path.exists()):
        return None
    horses = json.loads(meta_path.read_text(encoding='utf-8'))
    data = pd.read_csv(csv_path, compression='gzip', dtype={'馬ID': str, 'レースID': str})
    return horses, prepare(data)


def saved_race_ids():
    return sorted([p.stem for p in CACHE_DIR.glob('*.json') if re.fullmatch(r'\d{12}', p.stem) and (CACHE_DIR / f'{p.stem}.csv.gz').exists()], reverse=True)




def get_saved_race_label(rid):
    """レース名を表示用に取得。元の保存済み成績は変更しない。"""
    label_file = CACHE_DIR / f'{rid}.label.json'
    if label_file.exists():
        try:
            label = json.loads(label_file.read_text(encoding='utf-8')).get('label', '')
            if label:
                return label
        except (ValueError, OSError, TypeError):
            pass

    # 古い保存データにも対応。取得できなければIDを併記して識別可能にする。
    year = rid[:4]
    fallback = f'{year}年のレース（{rid}）'
    try:
        res = requests.get(
            f'https://race.netkeiba.com/race/shutuba.html?race_id={rid}',
            headers=HEADERS, timeout=8
        )
        res.raise_for_status()
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        # 例：「天皇賞(秋)(GI) 出馬表 | 2026年11月1日 ...」
        race_name = re.split(r'\s*[|｜]\s*|\s+出馬表|\s+結果|\s+予想', title, maxsplit=1)[0].strip()
        race_name = re.sub(r'\s*[-－–]\s*netkeiba.*$', '', race_name, flags=re.I).strip()
        if race_name and not any(x in race_name.lower() for x in ['netkeiba', '競馬情報', '競馬データベース', '404', 'エラー']):
            date_match = re.search(r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日', title)
            if date_match:
                y, m, d = (int(v) for v in date_match.groups())
                date_label = f'{y}年{m}月{d}日'
            else:
                date_label = f'{year}年'
            label = f'{race_name}｜{date_label}（{rid}）'
            label_file.write_text(json.dumps({'label': label}, ensure_ascii=False), encoding='utf-8')
            return label
    except (requests.RequestException, OSError):
        pass
    return fallback


def classify_grade(text):
    """レース名とHTML上の明示的な格付け情報だけから判定。推測しない。"""
    value = str(text or '')
    normalized = (value.upper().replace('Ｇ', 'G').replace('Ⅰ', 'I').replace('Ⅱ', 'II').replace('Ⅲ', 'III')
                  .replace('１', '1').replace('２', '2').replace('３', '3'))
    # G1, GI, ＧⅠ, Jpn1, JpnI, [G2] などに対応
    hit = re.search(r'(?<![A-Z0-9])(?:G|JPN)\s*[-(（\[]?\s*(III|II|I|[123])(?=[^A-Z0-9]|$)', normalized)
    if hit:
        val = hit.group(1)
        return {'I': 'G1', '1': 'G1', 'II': 'G2', '2': 'G2', 'III': 'G3', '3': 'G3'}[val]
    # 明示的に重賞以外とわかる表記のみをその他とする
    if re.search(r'(?:\bOP\b|\bL\b|リステッド|オープン|\d勝クラス|[１２３]勝クラス|未勝利|新馬|特別|障害未勝利|500万下|1000万下|1600万下)', normalized):
        return 'その他'
    return '不明'


def parse_race_id(raw):
    value = raw.strip()
    if value.isdigit() and len(value) == 12:
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    if host == 'netkeiba.com' or host.endswith('.netkeiba.com'):
        rid = parse_qs(parsed.query).get('race_id', [''])[0]
        if rid.isdigit() and len(rid) == 12:
            return rid
    return ''


def clean_horse_name(raw):
    """馬番が混入したリンク文字列を正規化。省略形は採用しない。"""
    raw = re.sub(r'\s+', '', str(raw or ''))
    return re.sub(r'^[0-9０-９]{1,2}(?:番)?', '', raw).strip()


def horse_name_candidate(a):
    """出馬表の正式表記（リンクのtitleなど）を優先。"""
    names = [a.get('title', ''), a.get('aria-label', ''), a.get_text(' ', strip=True)]
    for el in [a.find('span', class_=re.compile('HorseName', re.I)), a.find('img')]:
        if el is not None:
            names.extend([el.get('title', ''), el.get('alt', '')])
    names = [clean_horse_name(x) for x in names]
    names = [x for x in names if re.fullmatch(r'[\u3040-\u30ff\u3400-\u9fffー・Ａ-Ｚａ-ｚA-Za-z]+', x)]
    return max(names, key=len, default='')


@st.cache_data(ttl=1800, show_spinner=False)
def get_horses(race_id):
    """馬名は正式表記の最良候補を採用し、同じ馬IDの後続短縮リンクで上書きしない。"""
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    horses, numbers = {}, {}
    # 出馬表の各馬の行を優先（馬番は枠番ではなく「馬番」欄から取得）。
    for row in soup.select('tr.HorseList, tr[class*="HorseList"]'):
        links = row.select('td.HorseInfo a[href*="/horse/"], td[class*="HorseInfo"] a[href*="/horse/"]')
        if not links:
            links = row.select('a[href*="/horse/"]')
        for a in links:
            hit = re.search(r'/horse/(\d{10})', a.get('href', ''))
            if not hit:
                continue
            hid = hit.group(1)
            name = horse_name_candidate(a)
            if name and (hid not in horses or len(name) > len(horses[hid])):
                horses[hid] = name
            cell = row.select_one('td.Umaban, td[class*="Umaban"], .Umaban')
            if cell is not None:
                txt = cell.get_text(' ', strip=True)
                num = re.search(r'(?<!\d)(1[0-9]|[1-9]|2[0-9])(?!\d)', txt)
                if num:
                    numbers[hid] = int(num.group(1))
            break
    # HTML構造が変わっても最低限取得できるようにする。
    for a in soup.select('a[href*="/horse/"]'):
        hit = re.search(r'/horse/(\d{10})', a.get('href', ''))
        if not hit:
            continue
        hid = hit.group(1)
        name = horse_name_candidate(a)
        if name and (hid not in horses or len(name) > len(horses[hid])):
            horses[hid] = name
    if not horses:
        raise ValueError('出走馬を取得できませんでした。ページ構造やアクセス制限を確認してください。')
    return horses, numbers


def update_cached_names(data, horses):
    """既存キャッシュに短縮馬名があっても馬IDを使って正式名へ復元。"""
    if data is None or '馬ID' not in data.columns or '馬名' not in data.columns:
        return data
    result = data.copy()
    ids = result['馬ID'].astype(str).str.replace(r'\.0$', '', regex=True)
    fixed = ids.map(horses)
    result['馬名'] = fixed.fillna(result['馬名'])
    return result


def horse_display(name, horses, numbers):
    hid = next((hid for hid, official in horses.items() if official == name), None)
    num = numbers.get(hid) if hid else None
    return f'{num} {name}' if num else name


def display_horse_column(df, horses, numbers):
    if '馬名' not in df.columns or not horses:
        return df
    shown = df.copy()
    shown['馬名'] = shown['馬名'].map(lambda n: horse_display(n, horses, numbers))
    return shown


@st.cache_data(ttl=3600, show_spinner=False)
def get_results(horse_id, horse_name):
    url = f'https://db.netkeiba.com/horse/result/{horse_id}/'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = 'EUC-JP'
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one('table.db_h_race_results')
    if table is None:
        raise ValueError('成績表が見つかりません')
    rows = table.select('tr')
    if len(rows) < 2:
        raise ValueError('成績データがありません')
    columns = [c.get_text(' ', strip=True) for c in rows[0].select('th, td')]
    records = []
    for row in rows[1:]:
        values = [c.get_text(' ', strip=True) for c in row.select('th, td')]
        if len(values) == len(columns):
            record = dict(zip(columns, values))
            record['馬名'] = horse_name
            record['馬ID'] = horse_id
            links = row.select("a[href*='race_id='], a[href*='/race/']")
            race_id = ''
            for link in links:
                href = link.get('href', '')
                match = re.search(r'(?:race_id=|/race/)(\d{12})', href)
                if match:
                    race_id = match.group(1)
                    break
            record['レースID'] = race_id
            # レース名の文字列だけでなく、アイコン等のtitle/altも参照する。
            race_cell = None
            if 'レース名' in columns:
                cells = row.select('th, td')
                idx = columns.index('レース名')
                if idx < len(cells):
                    race_cell = cells[idx]
            grade_text = record.get('レース名', '')
            if race_cell is not None:
                grade_text += ' ' + ' '.join(
                    str(tag.get(attr, '')) for tag in race_cell.select('[title], [alt]')
                    for attr in ('title', 'alt') if tag.get(attr)
                )
                # アイコンのCSS classに grade1/grade2 などが明示されていれば利用。
                grade_text += ' ' + ' '.join(
                    'G' + match.group(1)
                    for tag in race_cell.find_all(True)
                    for match in [re.search(r'(?:gradetype|grade)[_-]?([123])\b', ' '.join(tag.get('class', [])), re.I)]
                    if match
                )
            record['格付け根拠'] = grade_text.strip()
            record['レース格'] = classify_grade(grade_text)
            records.append(record)
    if not records:
        raise ValueError('成績の抽出件数が0件です')
    return pd.DataFrame(records)


@st.cache_data(ttl=86400, show_spinner=False)
def get_race_agari(race_id):
    """Return race-wide 3F ranks, keyed by horse ID or exact horse name.

    Uses the result table whose headers include 馬名 and 後3F.
    Missing / nonnumeric 3F times do not receive a rank.
    """
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one('#All_Result_Table')
    if table is None:
        raise ValueError('結果テーブルが見つかりません')

    # Header text sometimes contains spaces/newlines; normalize these.
    header_rows = table.select('thead tr')
    if not header_rows:
        header_rows = table.select('tr')[:2]
    header = None
    for tr in header_rows:
        labels = [re.sub(r'\s+', '', cell.get_text(' ', strip=True))
                  for cell in tr.find_all(['th', 'td'], recursive=False)]
        if '馬名' in labels and ('後3F' in labels or '上り' in labels):
            header = labels
            break
    if header is None:
        raise ValueError('馬名・後3Fの列が見つかりません')
    name_index = header.index('馬名')
    speed_index = header.index('後3F') if '後3F' in header else header.index('上り')

    rows = table.select('tbody tr')
    if not rows:
        rows = table.select('tr')[1:]
    times = []
    for tr in rows:
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) <= max(name_index, speed_index):
            continue
        name_cell = cells[name_index]
        name = name_cell.get_text(' ', strip=True)
        if not name:
            continue
        raw_time = cells[speed_index].get_text(' ', strip=True)
        raw_time = re.sub(r'\s+', '', raw_time)
        # Accept plain numeric seconds only; skip --, empty values, etc.
        if not re.fullmatch(r'\d{2}\.\d', raw_time):
            continue
        speed = float(raw_time)
        if not 20 <= speed <= 60:
            continue
        horse_link = name_cell.select_one('a[href*="/horse/"]')
        match = re.search(r'/horse/(\d{10})', horse_link.get('href', '')) if horse_link else None
        horse_id = match.group(1) if match else None
        times.append((horse_id, name, speed))

    if len(times) < 2:
        raise ValueError('後3Fの数値を2頭以上取得できません')
    result = {}
    for horse_id, name, speed in times:
        rank = 1 + sum(other_speed < speed for _, _, other_speed in times)
        result[f'name:{name}'] = rank
        if horse_id:
            result[horse_id] = rank
    return result


def add_recent_agari_ranks(data, max_per_horse=5):
    data = data.copy()
    if 'レースID' not in data.columns or '馬ID' not in data.columns:
        raise ValueError('レースID・馬IDがありません。Ver.13で成績を再取得してください。')
    data['上がり順位'] = pd.to_numeric(data['上がり順位'], errors='coerce')
    work = data.sort_values('日付', ascending=False, kind='stable').groupby('馬名', sort=False).head(max_per_horse)
    work = work[work['上がり順位'].isna() & work['レースID'].astype(str).str.fullmatch(r'\d{12}')]
    race_ids = work['レースID'].astype(str).drop_duplicates().tolist()
    if not race_ids:
        return data, 0, []
    progress = st.progress(0)
    status = st.empty()
    ranks_by_race = {}
    errors = []
    for i, rid in enumerate(race_ids, 1):
        status.write(f'上がり順位を照合中：{i}/{len(race_ids)}レース')
        try:
            ranks_by_race[rid] = get_race_agari(rid)
        except Exception as e:
            errors.append(f'{rid}：{e}')
        progress.progress(i / len(race_ids))
        if i < len(race_ids):
            time.sleep(0.5)
    status.empty()
    for idx, row in work.iterrows():
        race_ranks = ranks_by_race.get(str(row['レースID']), {})
        rank = race_ranks.get(str(row['馬ID']))
        if rank is None:
            rank = race_ranks.get(f"name:{row['馬名']}")
        if rank is not None:
            data.at[idx, '上がり順位'] = rank
    return data, len(race_ids), errors


@st.cache_data(ttl=86400, show_spinner=False)
def get_netkeiba_pace(race_id):
    """netkeibaの結果ページに明示されたS/M/Hだけを採用。独自推定しない。"""
    if not re.fullmatch(r"\d{12}", str(race_id)):
        raise ValueError('レースIDの形式が不正')
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    res = requests.get(url, headers=HEADERS, timeout=18)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    # 結果ページ内には走行データのS表示もあるため、ラップタイム節に限定。
    heading = next((x for x in soup.find_all(['h2', 'h3', 'h4'])
                    if 'ラップタイム' in x.get_text(' ', strip=True)), None)
    if heading is None:
        raise ValueError('ラップタイム欄が見つかりません')
    excerpts = []
    node = heading
    for _ in range(14):
        node = node.find_next_sibling()
        if node is None:
            break
        if node.name in ('h2', 'h3'):
            break
        excerpts.append(node.get_text(' ', strip=True))
        if sum(map(len, excerpts)) > 2400:
            break
    section = ' '.join(excerpts)
    m = re.search(r'ペース\s*[:：]\s*([SMH])(?=\s|$|[（(])', section, re.I)
    if not m:
        # HTML構造によって見出しの親要素に含まれる場合を想定。
        container = heading.parent
        parent_text = container.get_text(' ', strip=True) if container else ''
        start = parent_text.find('ラップタイム')
        if start >= 0:
            m = re.search(r'ペース\s*[:：]\s*([SMH])(?=\s|$|[（(])', parent_text[start:start+1400], re.I)
    if not m:
        raise ValueError('ラップ欄にS/M/Hの明示表記が見つかりません')
    return {'S': 'スロー寄り', 'M': 'ミドル寄り', 'H': 'ハイ寄り'}[m.group(1).upper()]


@st.cache_data(ttl=86400, show_spinner=False)
def get_lap_section_text(race_id):
    """netkeibaのラップ欄を原文で回収する実験用。ラップ数値の自動解析は行わない。"""
    if not re.fullmatch(r'\d{12}', str(race_id)):
        raise ValueError('レースID不正')
    response = requests.get(f'https://db.sp.netkeiba.com/race/{race_id}/',
                            headers=HEADERS, timeout=18)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, 'html.parser')
    heading = next((h for h in soup.find_all(['h2', 'h3', 'h4'])
                    if 'ラップタイム' in h.get_text(' ', strip=True)), None)
    if heading is None:
        raise ValueError('ラップタイム見出しが見つかりません')
    parts = []
    node = heading
    for _ in range(18):
        node = node.find_next_sibling()
        if node is None or node.name in ('h2', 'h3'):
            break
        parts.append(node.get_text(' ', strip=True))
        if sum(len(t) for t in parts) > 3500:
            break
    text = ' | '.join(parts).strip()
    if not text:
        raise ValueError('ラップ欄の本文が取得できません')
    return text[:3500]


def apply_netkeiba_pace(data, pace_by_race):
    result = data.copy()
    ids = result['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    if 'netkeibaペース' not in result.columns:
        result['netkeibaペース'] = ''
    for rid, pace in pace_by_race.items():
        result.loc[ids.eq(str(rid)), 'netkeibaペース'] = pace
    return prepare(result)


def venue_name(value):
    value = str(value)
    return next((v for v in VENUES if v in value), value)


def convert_auto(raw):
    data = raw.copy()
    required = ['距離', '馬 場', '開催', '着 順', '上り', '通過', '日付']
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError('取得データに必要な列がありません: ' + '、'.join(missing))
    dist = data['距離'].astype(str)
    data['距離_m'] = pd.to_numeric(dist.str.extract(r'(\d{3,4})')[0], errors='coerce')
    data['コース'] = dist.str.extract(r'(芝|ダ|障)')[0]
    data['馬場状態'] = data['馬 場'].astype(str).str.strip()
    data['競馬場'] = data['開催'].apply(venue_name)
    data['着順_数値'] = pd.to_numeric(data['着 順'], errors='coerce')
    data['上がり3F_秒'] = pd.to_numeric(data['上り'], errors='coerce')
    data['4角順位'] = pd.to_numeric(data['通過'].astype(str).str.extract(r'(\d+)\s*$')[0], errors='coerce')
    data['上がり順位'] = float('nan')
    data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
    data['頭数'] = pd.to_numeric(data.get('頭 数'), errors='coerce')
    # netkeibaの「ペース」は前半3F-後半3Fの表記。距離差があるため目安として扱う。
    pace_pair = data['ペース'].astype(str).str.extract(r'(\d{2}\.\d)\s*[-－ー〜～]\s*(\d{2}\.\d)') if 'ペース' in data.columns else pd.DataFrame(index=data.index, columns=[0, 1])
    data['前半3F'] = pd.to_numeric(pace_pair[0], errors='coerce')
    data['後半3F'] = pd.to_numeric(pace_pair[1], errors='coerce')
    data['前後半3F差'] = data['前半3F'] - data['後半3F']
    return data[['馬名', '馬ID', 'レースID', '日付', '競馬場', 'コース', '距離_m', '馬場状態', '着順_数値', '4角順位', '上がり3F_秒', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差', 'レース名', '格付け根拠', 'レース格']].copy()


def prepare(data):
    data = data.copy()
    data.columns = data.columns.astype(str).str.strip()
    for target, candidates in ALIASES.items():
        if target not in data.columns:
            for candidate in candidates:
                if candidate in data.columns:
                    data = data.rename(columns={candidate: target})
                    break
    missing = [c for c in ['馬名', '着順_数値'] if c not in data.columns]
    if missing:
        raise ValueError('必要な列がありません: ' + '、'.join(missing))
    for c in NUMERIC:
        if c not in data.columns:
            data[c] = float('nan')
        data[c] = pd.to_numeric(data[c], errors='coerce')
    for c in ['競馬場', '馬場状態']:
        if c not in data.columns:
            data[c] = '不明'
        data[c] = data[c].fillna('不明').astype(str).str.strip()
    # 旧CSVはレース格未収録。明示的なレース名があれば判定し、不明なものをその他に混ぜない。
    if 'レース格' not in data.columns:
        if 'レース名' in data.columns:
            data['レース格'] = data['レース名'].apply(classify_grade)
        else:
            data['レース格'] = '不明'
    else:
        valid_grades = ['G1', 'G2', 'G3', 'その他', '不明']
        grades = data['レース格'].fillna('').astype(str).str.strip()
        data['レース格'] = grades.where(grades.isin(valid_grades), grades.apply(classify_grade))
    for audit_col in ['レース名', '格付け根拠']:
        if audit_col not in data.columns:
            data[audit_col] = ''
        data[audit_col] = data[audit_col].fillna('').astype(str).replace('nan', '')
    data['馬名'] = data['馬名'].astype(str).str.strip()
    data = data[data['馬名'].ne('') & data['馬名'].ne('nan')].copy()
    if '日付' in data.columns:
        data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
    # 手元Excelにも同じ分類を付け、数字の根拠がない場合は「不明」にする。
    # 前後半3Fの数値が妥当な範囲にあり、距離が1200m以上の時だけ分類する。
    # 最初と最後の600mの比較であり、道中のペースは評価できない。
    valid_pace = (data['前半3F'].between(29, 45) & data['後半3F'].between(29, 45)
                  & data['距離_m'].ge(1200))
    diff = (data['前半3F'] - data['後半3F']).where(valid_pace)
    data['前後半3F差'] = diff
    data['暫定ペース'] = '不明'
    data.loc[diff.notna() & diff.lt(-1.0), '暫定ペース'] = 'ハイ寄り'
    data.loc[diff.notna() & diff.between(-1.0, 1.0), '暫定ペース'] = 'ミドル寄り'
    data.loc[diff.notna() & diff.gt(1.0), '暫定ペース'] = 'スロー寄り'
    if 'netkeibaペース' not in data.columns:
        data['netkeibaペース'] = ''
    net_pace = data['netkeibaペース'].fillna('').astype(str).str.strip()
    valid_net = net_pace.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])
    # 実戦のペースランキングにはnetkeibaの明示判定のみ使用する。
    # 独自計算は暫定ペースに別保存し、混ぜない。
    data['推定ペース'] = '不明'
    data['ペース判定元'] = '未取得・不明'
    data.loc[valid_net, '推定ペース'] = net_pace[valid_net]
    data.loc[valid_net, 'ペース判定元'] = 'netkeiba表示'
    pos = data['4角順位']
    field = data['頭数']
    # 4角位置を頭数で割った便宜的な分類。逃げの確定判定ではない。
    valid = pos.ge(1) & field.ge(2) & pos.le(field)
    data['推定脚質'] = '不明'
    data.loc[valid & pos.eq(1), '推定脚質'] = '先頭（4角）'
    data.loc[valid & pos.gt(1) & pos.le((field * 0.35).clip(lower=2)), '推定脚質'] = '先行寄り'
    data.loc[valid & pos.gt((field * 0.35).clip(lower=2)) & pos.le(field * 0.7), '推定脚質'] = '中団寄り'
    data.loc[valid & pos.gt(field * 0.7), '推定脚質'] = '後方寄り'
    return data


def read_upload(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        try:
            data = pd.read_csv(file, encoding='utf-8-sig')
        except UnicodeDecodeError:
            file.seek(0)
            data = pd.read_csv(file, encoding='cp932')
    else:
        book = pd.ExcelFile(file)
        sheet = next((n for n in ['分析データ', '分析用296走'] if n in book.sheet_names), book.sheet_names[0])
        data = pd.read_excel(book, sheet_name=sheet)
    return prepare(data)


def make_excel(data):
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        data.to_excel(writer, sheet_name='分析データ', index=False)
    return out.getvalue()


def sample_count_for_metric(group, metric):
    """指標の分母となる観測走数。値が不明な走は数えない。"""
    column_map = {
        '勝率(%)': '着順_数値', '連対率(%)': '着順_数値',
        '複勝率(%)': '着順_数値', '平均着順': '着順_数値',
        '平均4角': '4角順位', '平均上がり': '上がり3F_秒',
        '上がり最速率(%)': '上がり順位',
        '上がり3位以内率(%)': '上がり順位',
        '平均上がり順位': '上がり順位',
    }
    if metric in column_map:
        return int(group[column_map[metric]].notna().sum())
    if metric in ('前方率(%)', '差し追込率(%)'):
        return int(group['推定脚質'].ne('不明').sum())
    return len(group)


def sample_label(n):
    if n == 0:
        return 'データなし'
    if n <= 2:
        return '参考（1〜2走）'
    if n <= 4:
        return '少数（3〜4走）'
    return '5走以上'


def summarize(group):
    finish = group['着順_数値'].dropna()
    agari = group['上がり順位'].dropna()
    def rate(count, denominator):
        return round(count / denominator * 100, 2) if denominator else float('nan')
    return {'出走数': len(group), '平均着順': finish.mean(), '平均4角': group['4角順位'].mean(), '平均上がり': group['上がり3F_秒'].mean(), '勝率(%)': rate((finish == 1).sum(), len(finish)), '連対率(%)': rate((finish <= 2).sum(), len(finish)), '複勝率(%)': rate((finish <= 3).sum(), len(finish)), '上がり対象走数': len(agari), '上がり最速率(%)': rate((agari == 1).sum(), len(agari)), '上がり3位以内率(%)': rate((agari <= 3).sum(), len(agari)), '平均上がり順位': agari.mean(), '前方率(%)': rate(group['推定脚質'].isin(['先頭（4角）', '先行寄り']).sum(), group['推定脚質'].ne('不明').sum()), '差し追込率(%)': rate(group['推定脚質'].eq('後方寄り').sum(), group['推定脚質'].ne('不明').sum()), 'ペース判定対象走数': int(group['推定ペース'].ne('不明').sum()), '脚質判定対象走数': int(group['推定脚質'].ne('不明').sum())}


st.header('① レースを指定')
known_ids = saved_race_ids()
if 'race_input_v141' not in st.session_state:
    remembered = st.query_params.get('race_id', '')
    st.session_state.race_input_v141 = remembered if parse_race_id(remembered) else ''
if known_ids:
    cached_labels = {rid: get_saved_race_label(rid) for rid in known_ids}
    cached_choice = tap_choice('保存済みデータを再開', ['選択しない'] + known_ids, 'saved_race_choice_v153', format_func=lambda rid: cached_labels.get(rid, rid) if rid != '選択しない' else rid)
    if st.button('保存済みレースを開く', disabled=cached_choice == '選択しない', use_container_width=True):
        st.session_state.race_input_v141 = cached_choice
        st.rerun()
race_input = st.text_input('netkeiba出馬表URL または12桁のレースID', key='race_input_v141', placeholder='例：202605041111')
race_id = parse_race_id(race_input) if race_input.strip() else ''
if race_id:
    st.query_params['race_id'] = race_id
if race_input.strip():
    if race_id:
        st.success(f'レースID：{race_id}')
    else:
        st.warning('正しいURLまたは12桁のレースIDを入力してください。')

st.header('② 出走馬・過去成績を自動取得')
if 'auto_race_id' not in st.session_state:
    st.session_state.auto_race_id = ''
if 'auto_horses' not in st.session_state:
    st.session_state.auto_horses = {}
if 'auto_numbers' not in st.session_state:
    st.session_state.auto_numbers = {}
if 'auto_df' not in st.session_state:
    st.session_state.auto_df = None

if race_id and st.session_state.auto_race_id != race_id:
    st.session_state['analysis_visible_v1581'] = False
    st.session_state.auto_race_id = race_id
    st.session_state.auto_horses = {}
    st.session_state.auto_numbers = {}
    st.session_state.auto_df = None
    try:
        restored = load_race_cache(race_id)
        if restored is not None:
            st.session_state.auto_horses, st.session_state.auto_df = restored
    except Exception as e:
        st.warning(f'保存済みデータの復元に失敗しました：{e}')
if race_id and st.session_state.auto_df is not None:
    st.info(f'保存済みデータを利用中：{len(st.session_state.auto_df)}走。再取得せず分析できます。')

# URL入力から1タップで、出走馬・過去成績・上がり順位まで取得する。
# 保存済みの成績は再利用し、上がり順位がまだない場合は追加照合する。
if st.button('出走馬・過去成績・上がり順位をまとめて取得', type='primary',
             disabled=not race_id, use_container_width=True):
    horses_now = st.session_state.auto_horses.copy()
    data_now = st.session_state.auto_df
    try:
        # 出馬表の表記が変わった場合も正式馬名・馬番を更新。既存成績は再取得しない。
        try:
            latest_horses, latest_numbers = get_horses(race_id)
            horses_now = latest_horses
            st.session_state.auto_horses = latest_horses
            st.session_state.auto_numbers = latest_numbers
            if data_now is not None:
                data_now = update_cached_names(data_now, latest_horses)
                st.session_state.auto_df = data_now
                save_race_cache(race_id, latest_horses, data_now)
        except Exception as e:
            if not horses_now:
                raise
            st.warning(f'出馬表の更新情報は取得できませんでした：{e}')
        if not horses_now:
            with st.spinner('① 出馬表から出走馬を取得中…'):
                horses_now, nums_now = get_horses(race_id)
                st.session_state.auto_horses = horses_now
                st.session_state.auto_numbers = nums_now
        if data_now is None:
            datasets = []
            horse_errors = []
            progress = st.progress(0)
            status = st.empty()
            for i, (horse_id, name) in enumerate(horses_now.items(), 1):
                status.write(f'② 過去成績を取得中：{name}（{i}/{len(horses_now)}）')
                try:
                    datasets.append(get_results(horse_id, name))
                except Exception as e:
                    horse_errors.append(f'{name}：{e}')
                progress.progress(i / len(horses_now))
                if i < len(horses_now):
                    time.sleep(1)
            status.empty()
            progress.empty()
            if not datasets:
                raise ValueError('過去成績を1件も取得できませんでした')
            data_now = prepare(convert_auto(pd.concat(datasets, ignore_index=True)))
            st.session_state.auto_df = data_now
            save_race_cache(race_id, horses_now, data_now)
            st.success(f'過去成績：{len(data_now)}走を取得・保存しました')
            if horse_errors:
                st.warning('取得できなかった馬：' + ' / '.join(horse_errors))
        else:
            st.info(f'② 保存済みの過去成績 {len(data_now)}走を再利用します')

        # 1レースにつき最大1回の照合。失敗があっても、取得済みデータを保持する。
        try:
            with st.spinner('③ 直近5走の上がり順位を照合しています…'):
                updated, checked, agari_errors = add_recent_agari_ranks(data_now)
            st.session_state.auto_df = updated
            save_race_cache(race_id, horses_now, updated)
            count = int(updated['上がり順位'].notna().sum())
            st.success(f'取得完了！ 出走馬{len(horses_now)}頭・過去成績{len(updated)}走・上がり順位{count}走（今回照合{checked}レース）')
            if agari_errors:
                st.warning(f'上がり順位を取得できなかったレース：{len(agari_errors)}件。例：{agari_errors[0]}')
        except Exception as e:
            st.warning(f'過去成績は保存済みですが、上がり順位の照合は失敗しました：{e}。同じボタンで再試行できます。')
    except Exception as e:
        st.error(f'まとめて取得できませんでした：{e}')

horses = st.session_state.auto_horses
if horses:
    st.success(f'出走馬：{len(horses)}頭')
    st.write('、'.join(horse_display(name, horses, st.session_state.auto_numbers) for name in horses.values()))
if st.session_state.auto_df is not None:
    available = int(st.session_state.auto_df['上がり順位'].notna().sum())
    st.caption(f'保存済みの過去成績：{len(st.session_state.auto_df)}走／上がり順位取得済み：{available}走。上がり順位は各馬の直近5走について照合します。')

st.caption('取得データはサーバーに一時保存し、同じレースIDなら再利用します。Streamlit Cloudの停止・再起動・再デプロイでは消える場合があります。確実に残すには下のExcelをiPhoneに保存してください。取得データの利用条件も確認してください。')
manual = st.text_area('手入力する出走馬名（1行に1頭・任意）', height=110, placeholder='自動取得できない場合などに入力')
target_horses = list(dict.fromkeys([n.strip() for n in manual.splitlines() if n.strip()] or list(horses.values())))

st.header('③ 成績データを読み込む')
st.caption('自動取得データに加えて、Excel・CSVを複数アップロードできます。')
uploads = st.file_uploader('分析データを選択（任意・複数選択可）', type=['xlsx', 'csv'], accept_multiple_files=True)
datasets = []
if st.session_state.auto_df is not None:
    datasets.append(st.session_state.auto_df.copy())
for file in uploads or []:
    try:
        datasets.append(read_upload(file))
    except Exception as e:
        st.error(f'{file.name} の読み込みに失敗：{e}')
        st.stop()
if not datasets:
    st.info('出走馬・過去成績を自動取得するか、Excel・CSVをアップロードしてください。')
    st.stop()

df = pd.concat(datasets, ignore_index=True, sort=False)
before = len(df)
df = df.drop_duplicates().reset_index(drop=True)
st.success(f'統合完了！ {len(df)}走 / {df["馬名"].nunique()}頭')
st.write(f'読み込んだファイル：{len(uploads or [])}個 / 削除した完全重複行：{before - len(df)}行')
st.caption('列構成の違う同一レースは重複して残る場合があります。')
st.download_button('📥 統合データをExcelで保存', data=make_excel(df), file_name='競馬分析_統合データ.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)

st.subheader('netkeibaのS/M/Hを全レース取得（Ver.15.8）')
st.caption('保存済みの全過去成績から重複を除いたレースIDを対象にします。取得済みは再取得せず、10件ごとに結果を一時保存します。')
if st.session_state.auto_df is not None and 'レースID' in st.session_state.auto_df.columns:
    source_df = st.session_state.auto_df.copy()
    ids = source_df['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    valid_ids = ids[ids.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
    # リストはレース日順にして、通信に失敗しても何が残ったか確認しやすくする。
    if '日付' in source_df.columns:
        ordering = source_df.assign(_rid=ids, _date=pd.to_datetime(source_df['日付'], errors='coerce'))
        ordered = ordering.sort_values('_date', ascending=False, na_position='last')['_rid']
        valid_ids = ordered[ordered.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
    known = source_df['netkeibaペース'].fillna('').astype(str).str.strip() if 'netkeibaペース' in source_df.columns else pd.Series('', index=source_df.index)
    confirmed_ids = set(ids[known.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])])
    pending = [rid for rid in valid_ids if rid not in confirmed_ids]
    st.write(f'対象：**{len(valid_ids)}レース** ／ netkeiba判定取得済み：**{len(valid_ids)-len(pending)}** ／ 未取得：**{len(pending)}**')
    st.caption('レース単位で集計するため、同じレースに複数の出走馬がいても1回だけアクセスします。')
    if st.button('未取得レースのS/M/Hをまとめて取得', disabled=not pending, type='primary', use_container_width=True):
        working = st.session_state.auto_df.copy()
        success, failed = {}, []
        progress = st.progress(0)
        status = st.empty()
        for i, rid in enumerate(pending, 1):
            status.caption(f'取得中：{i}/{len(pending)}レース｜成功 {len(success)}件・取得不可 {len(failed)}件')
            try:
                success[rid] = get_netkeiba_pace(rid)
            except Exception as e:
                failed.append({'レースID': rid, '状態': '取得不可', '理由': str(e)[:180],
                               '結果ページ': f'https://db.sp.netkeiba.com/race/{rid}/'})
            if i % 10 == 0 or i == len(pending):
                if success:
                    working = apply_netkeiba_pace(working, success)
                    st.session_state.auto_df = working
                    save_race_cache(st.session_state.auto_race_id, st.session_state.auto_horses, working)
                    success.clear()
                st.session_state['pace_v157_failed'] = failed.copy()
            progress.progress(i / len(pending))
            if i < len(pending):
                time.sleep(0.35)
        progress.empty()
        status.empty()
        st.session_state.auto_df = working
        st.session_state['pace_v157_failed'] = failed
        received = int(working['netkeibaペース'].fillna('').astype(str).isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り']).groupby(working['レースID'].fillna('').astype(str)).any().sum())
        st.success(f'取得処理が完了！netkeiba判定取得済み：{received}/{len(valid_ids)}レース。今回取得不可：{len(failed)}レース。')
        if failed:
            st.warning('取得不可は「不明」として扱います。再実行すると取得済みを飛ばして再試行できます。')
    if st.session_state.get('pace_v157_failed'):
        fails_df = pd.DataFrame(st.session_state['pace_v157_failed'])
        with st.expander(f'取得不可のレース（{len(fails_df)}件）'):
            st.dataframe(fails_df, hide_index=True, use_container_width=True)
            st.download_button('取得不可一覧をCSVで保存', fails_df.to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_netkeiba_pace_fail_v157.csv', mime='text/csv', use_container_width=True)
    st.caption('ペース別成績はnetkeiba明示判定だけを使用。以前の±1秒の推定値は「暫定ペース」として検証表に残します。')
else:
    st.caption('過去成績を取得すると、全レースのS/M/H取得ができます。')

st.header('④ 出走馬との照合')
analysis_df = df.copy()
if target_horses:
    available = set(analysis_df['馬名'].unique())
    missing = [n for n in target_horses if n not in available]
    analysis_df = analysis_df[analysis_df['馬名'].isin(target_horses)].copy()
    st.write(f'照合できた出走馬：{analysis_df["馬名"].nunique()}頭')
    if missing:
        st.warning('データが見つからない馬：' + '、'.join(missing))
else:
    st.write(f'全{analysis_df["馬名"].nunique()}頭を分析対象にします。')
if analysis_df.empty:
    st.error('分析対象の馬の成績がありません。')
    st.stop()

st.header('⑤ レース格の判定チェック（Ver.14.4）')
st.caption('元のレース名と格付け根拠を見ながら、誤判定・判定不明を確認できます。ここで表示するのはフィルター適用前のデータです。')
audit = analysis_df.copy()
if '日付' in audit.columns:
    audit = audit.sort_values(['馬名', '日付'], ascending=[True, False], kind='stable')
audit_horses = ['全馬'] + sorted(audit['馬名'].unique().tolist())
with st.expander('過去レースの格付け一覧を確認', expanded=False):
    audit_horse = tap_choice('確認する馬', audit_horses, 'audit_horse_v153')
    audit_grade = tap_choice('表示する格付け', ['すべて', 'G1', 'G2', 'G3', 'その他', '不明'], 'audit_grade_v153')
    if audit_horse != '全馬':
        audit = audit[audit['馬名'] == audit_horse]
    if audit_grade != 'すべて':
        audit = audit[audit['レース格'] == audit_grade]
    audit_cols = ['馬名', '日付', 'レース名', 'レース格', '格付け根拠', '着順_数値', 'レースID']
    audit_view = audit[[c for c in audit_cols if c in audit.columns]].copy()
    if '日付' in audit_view.columns:
        audit_view['日付'] = pd.to_datetime(audit_view['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
    if 'レースID' in audit_view.columns:
        ids = audit_view['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
        audit_view['レース結果URL'] = ids.apply(lambda v: f'https://race.netkeiba.com/race/result.html?race_id={v}' if re.fullmatch(r'\d{12}', v) else '')
        audit_view = audit_view.drop(columns=['レースID'])
    st.write(f'表示：{len(audit_view)}走（元データ：{len(analysis_df)}走）')
    st.dataframe(display_horse_column(audit_view, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True,
                 column_config={'馬名': st.column_config.TextColumn('馬名', width='medium'),
                                'レース名': st.column_config.TextColumn('レース名', width='medium'),
                                '格付け根拠': st.column_config.TextColumn('格付け根拠', width='large'),
                                'レース結果URL': st.column_config.LinkColumn('レース結果を確認', display_text='結果を見る')})
    st.download_button('格付けチェック表をCSVで保存', display_horse_column(audit_view, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                       file_name='keiba_grade_audit.csv', mime='text/csv', use_container_width=True)
    missing_names = int(analysis_df['レース名'].fillna('').eq('').sum())
    if missing_names:
        st.warning(f'レース名がないデータが{missing_names}走あります。旧バージョンの保存データは、レース格を検証するために再取得が必要です。')
    st.caption('格付け判定は取得ページの表記に依存します。G1・G2・G3と表示されても、根拠欄や実際のレース名と一致しているか確認してください。')

st.header('⑥ 分析条件')
mode = st.radio('分析モード', ['ランキング', '馬を比較'], horizontal=True)
venue = tap_choice('競馬場', ['すべて'] + sorted(analysis_df['競馬場'].unique().tolist()), 'tap_競馬場_v153')
track = tap_choice('馬場状態', ['すべて'] + sorted(analysis_df['馬場状態'].unique().tolist()), 'tap_馬場状態_v153')
course_options = ['すべて'] + sorted(analysis_df['コース'].dropna().astype(str).unique().tolist()) if 'コース' in analysis_df.columns else ['すべて']
course = tap_choice('コース', course_options, 'tap_コース_v153')
distance = tap_choice('距離', ['すべて', '1200m以下', '1201〜1600m', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'], 'tap_距離_v153')
recent = tap_choice('対象レース', ['直近5走', '直近10走', '直近15走', '全走'], 'tap_対象レース_v153')
grade_filter = tap_choice('レース格', ['全レース', '重賞のみ（G1・G2・G3）', 'G1のみ', 'その他（L・OP・条件戦など）'], 'tap_レース格_v153')
st.caption('格付けが確認できない走は「不明」として保持。重賞・G1・その他の絞り込みから除外します。')
pace_filter = tap_choice('推定ペース', ['すべて', 'スロー寄り', 'ミドル寄り', 'ハイ寄り', '不明'], 'tap_推定ペース_v153')
st.caption('判定は前半600mと後半600mの差のみ（前半−後半）：−1秒未満＝前傾（ハイ寄り）、±1秒以内＝均衡（ミドル寄り）、＋1秒超＝後傾（スロー寄り）。道中のラップ、コース差、距離差は反映しない暫定分類です。')
st.caption('脚質は4角位置と頭数から便宜的に推定。先頭（4角）＝逃げ確定ではなく、差し追込率は後方位置の割合です。')
st.caption('集計順序：各馬の全成績から直近○走を確定 → 競馬場・馬場・距離・レース格・ペースで絞り込み。例：直近5走＋G1のみ＝直近5走の中にあるG1だけ。')
counts = analysis_df['レース格'].value_counts()
st.caption('判定済み：' + ' / '.join(f'{g} {int(counts.get(g, 0))}走' for g in ['G1', 'G2', 'G3', 'その他']) + f' / 不明 {int(counts.get("不明", 0))}走')
if int(counts.get('不明', 0)):
    st.info('レース格が不明な成績があります。以前のバージョンで取得したデータは格付け未保存の場合があります。必要に応じて新しく過去成績を取得し直してください。')
if mode == 'ランキング':
    metric = tap_choice('ランキング指標', METRICS, 'tap_ランキング指標_v153')
else:
    options = sorted(analysis_df['馬名'].unique().tolist())
    selected = tap_horses('比較する馬（2〜3頭）', options, 'compare_horses_v153')
view = st.radio('表示方法', ['横並び表', 'スマホ用カード'], horizontal=True)

st.header('⑦ 分析する')
# Streamlitはどのボタンでも再実行されるので、分析済み状態を保持する。
# ラップ取得・CSVダウンロード等を押しても分析結果を消さない。
if 'analysis_visible_v1581' not in st.session_state:
    st.session_state['analysis_visible_v1581'] = False
if st.button('分析する', type='primary', use_container_width=True):
    st.session_state['analysis_visible_v1581'] = True
if st.session_state['analysis_visible_v1581']:
    # Ver.14.6：先に各馬の「直近N走」を確定し、その範囲内で条件を適用する。
    # 日付が取得できない走は末尾に置き、元の並びを保持する。
    base = analysis_df.copy()
    if '日付' in base.columns:
        base = base.sort_values('日付', ascending=False, na_position='last', kind='stable')
    if recent != '全走':
        n = int(recent.replace('直近', '').replace('走', ''))
        base = base.groupby('馬名', sort=False).head(n).copy()
    base_counts = base.groupby('馬名').size().rename('直近対象走数')

    filtered = base.copy()
    if venue != 'すべて':
        filtered = filtered[filtered['競馬場'] == venue]
    if track != 'すべて':
        filtered = filtered[filtered['馬場状態'] == track]
    if course != 'すべて' and 'コース' in filtered.columns:
        filtered = filtered[filtered['コース'] == course]
    if grade_filter != '全レース':
        grade_targets = {'重賞のみ（G1・G2・G3）': ['G1', 'G2', 'G3'],
                         'G1のみ': ['G1'], 'その他（L・OP・条件戦など）': ['その他']}
        filtered = filtered[filtered['レース格'].isin(grade_targets[grade_filter])]
    # 比較用データはペース条件を掛ける直前に確保。距離条件はこの後両方に適用。
    pace_compare_base = filtered.copy()
    if pace_filter != 'すべて':
        filtered = filtered[filtered['推定ペース'] == pace_filter]
    ranges = {'1200m以下': (0, 1200), '1201〜1600m': (1201, 1600), '1601〜1800m': (1601, 1800), '1801〜2200m': (1801, 2200), '2201〜2400m': (2201, 2400), '2401m以上': (2401, float('inf'))}
    if distance in ranges:
        filtered = filtered[filtered['距離_m'].between(*ranges[distance])]
        pace_compare_base = pace_compare_base[pace_compare_base['距離_m'].between(*ranges[distance])]
    if mode == '馬を比較':
        if len(selected) < 2:
            st.warning('比較する馬を2頭以上選んでください。')
            st.stop()
        filtered = filtered[filtered['馬名'].isin(selected)]
        pace_compare_base = pace_compare_base[pace_compare_base['馬名'].isin(selected)]
    st.caption(f'直近走数を先に適用：{len(base)}走 → 条件絞り込み後：{len(filtered)}走（対象 {filtered["馬名"].nunique()}頭）')
    if filtered.empty:
        st.warning('指定した直近走数の範囲内に、条件に一致するレースがありません。対象レースを直近10走・全走に広げて確認してください。')
        st.stop()
    # この内訳はランキングと同じ filtered を使うため、表示・CSVの出走数と一致する。
    matched_counts = filtered.groupby('馬名').size().rename('条件一致走数')
    count_check = pd.concat([base_counts, matched_counts], axis=1).fillna(0).astype(int).reset_index()
    count_check = count_check.sort_values(['条件一致走数', '馬名'], ascending=[False, True])
    with st.expander('集計対象走数の確認（全馬）', expanded=False):
        st.caption('「直近対象走数」から、選んだレース格などに一致した「条件一致走数」だけをランキングに使用しています。0走の馬はランキングに出ません。')
        st.dataframe(display_horse_column(count_check, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True)
    rows = []
    for name, group in filtered.groupby('馬名'):
        row = summarize(group)
        row['馬名'] = name
        if mode == 'ランキング':
            count = sample_count_for_metric(group, metric)
            row['指標対象走数'] = count
            row['データ量'] = sample_label(count)
        rows.append(row)
    result = pd.DataFrame(rows)
    if mode == 'ランキング':
        ascending = metric in ['平均着順', '平均4角', '平均上がり', '平均上がり順位']
        result = result.sort_values(metric, ascending=ascending, na_position='last', kind='stable').reset_index(drop=True)
        result.insert(0, '順位', result[metric].rank(method='min', ascending=ascending, na_option='bottom').astype(int))
        st.subheader(f'🏆 {metric} ランキング')
        st.caption('指標対象走数＝選択したランキング指標を計算できた走数。1〜2走は参考値、3〜4走は少数例です。順位にはサンプル数による補正を掛けていません。')
        if int((result['指標対象走数'] <= 2).sum()):
            st.info(f'今回、{int((result["指標対象走数"] <= 2).sum())}頭が指標対象2走以下。高順位でも参考値として見てください。')
    else:
        result['表示順'] = result['馬名'].apply(selected.index)
        result = result.sort_values('表示順').drop(columns='表示順')
        st.subheader('🐎 馬の成績比較')
    result = result.round(2)
    if mode == 'ランキング':
        # 順位と馬名を常に先頭に。重要指標を続ける。
        front = ['順位', '馬名', '出走数', '指標対象走数', 'データ量', metric]
        result = result[[c for c in dict.fromkeys(front) if c in result.columns]
                        + [c for c in result.columns if c not in front]]
    else:
        result = result[['馬名'] + [c for c in result.columns if c != '馬名']]
    if view == '横並び表':
        if mode == 'ランキング':
            full_table = st.checkbox('詳細指標をすべて表示', value=False)
            key_cols = ['順位', '馬名', '出走数', '指標対象走数', 'データ量', metric, '複勝率(%)', '平均4角', '上がり最速率(%)']
            compact_cols = [c for c in dict.fromkeys(key_cols) if c in result.columns]
            show_table = result if full_table else result[compact_cols]
            st.dataframe(display_horse_column(show_table, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True,
                         column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
        else:
            st.dataframe(display_horse_column(result, horses, st.session_state.auto_numbers).set_index('馬名').T, use_container_width=True)
    else:
        for _, row in result.iterrows():
            with st.container(border=True):
                label = f'🐎 {horse_display(row["馬名"], horses, st.session_state.auto_numbers)}'
                if mode == 'ランキング':
                    label = f'{int(row["順位"])}位 {label}'
                st.markdown(f'### {label}')
                st.caption(f'対象：{int(row["出走数"])}走')
                if mode == 'ランキング':
                    st.caption(f'【{metric}】有効データ {int(row["指標対象走数"])}走 ／ {row["データ量"]}')
                for key in METRICS:
                    value = row[key]
                    shown = 'データなし' if pd.isna(value) else f'{value:.2f}' + ('%' if key.endswith('(%)') else '')
                    st.write(f'**{key}：** {shown}')
                st.caption(f'上がり順位：{int(row["上がり対象走数"])}走 / 推定ペース：{int(row["ペース判定対象走数"])}走 / 推定脚質：{int(row["脚質判定対象走数"])}走')
    with st.expander('今回の集計に入ったレース一覧', expanded=False):
        show_cols = ['馬名', '日付', 'レース名', 'レース格', '競馬場', '距離_m', '着順_数値', '推定ペース']
        used = filtered[[c for c in show_cols if c in filtered.columns]].copy()
        if '日付' in used.columns:
            used['日付'] = pd.to_datetime(used['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
        st.dataframe(display_horse_column(used, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True)
        st.download_button('集計対象レース一覧をCSVで保存', data=display_horse_column(used, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                           file_name='keiba_used_races_v146.csv', mime='text/csv', use_container_width=True)
    st.subheader('ペース別の成績比較（Ver.15.8）')
    st.caption('レース格・直近走数・距離などの条件はランキングと同じ。ここだけは「推定ペース」の絞り込みを外し、3種類を並べて比較します。')
    st.info('netkeibaのS/M/Hが取得できたレースのみで集計します。未取得レースは「不明」として除外し、独自推定は混ぜません。')
    # Ver.15.8: ペース判定のカバー率を必ず明示する（評価不能=苦手、ではない）。
    coverage_source = pace_compare_base.copy()
    coverage_source['判定状態'] = coverage_source['推定ペース'].apply(
        lambda x: 'netkeiba判定あり' if x in ('ハイ寄り', 'ミドル寄り', 'スロー寄り') else '未取得・不明')
    coverage_rows = []
    for h, hg in coverage_source.groupby('馬名', sort=False):
        known_h = hg[hg['判定状態'].eq('netkeiba判定あり')]
        total_h = len(hg)
        n_known = len(known_h)
        coverage_rows.append({'馬名': h, '条件一致走数': total_h,
                              'netkeiba判定あり': n_known, '未取得・不明': total_h - n_known,
                              '判定カバー率(%)': round(n_known / total_h * 100, 1) if total_h else 0,
                              'ハイ': int(known_h['推定ペース'].eq('ハイ寄り').sum()),
                              'ミドル': int(known_h['推定ペース'].eq('ミドル寄り').sum()),
                              'スロー': int(known_h['推定ペース'].eq('スロー寄り').sum())})
    if coverage_rows:
        coverage = pd.DataFrame(coverage_rows)
        all_count = len(coverage_source)
        known_count = int(coverage_source['判定状態'].eq('netkeiba判定あり').sum())
        st.caption(f'ペース判定のカバー率：{known_count}/{all_count}走'
                   f'（{known_count / all_count * 100:.1f}%）｜除外：{all_count-known_count}走')
        with st.expander('ペース判定の取得状況（馬別・Ver.15.8）', expanded=False):
            st.caption('選択した直近走数・格付け・距離などに合う過去成績が母数。'
                       '「不明」は苦手を意味しません。ハイ0走も能力の低さではありません。')
            st.dataframe(display_horse_column(coverage, horses, st.session_state.auto_numbers),
                         hide_index=True, use_container_width=True)
            st.download_button('ペース取得状況をCSVで保存',
                               display_horse_column(coverage, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_pace_coverage_v158.csv', mime='text/csv', use_container_width=True)
            unknown = coverage_source[coverage_source['判定状態'].eq('未取得・不明')].copy()
            if unknown.empty:
                st.success('現在の分析条件に該当する走はすべてnetkeiba判定を取得済みです。')
            else:
                columns = ['馬名','日付','レース名','レースID','競馬場','距離_m','レース格','ペース判定元']
                unknown = unknown[[c for c in columns if c in unknown.columns]]
                st.warning(f'未取得・不明が{len(unknown)}走あります。下のCSVで対象レースを確認できます。')
                st.dataframe(display_horse_column(unknown, horses, st.session_state.auto_numbers),
                             hide_index=True, use_container_width=True)
                st.download_button('ペース未判定の成績をCSVで保存',
                                   display_horse_column(unknown, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_pace_unknown_v158.csv', mime='text/csv', use_container_width=True)
    detail = pace_compare_base[pace_compare_base['推定ペース'].ne('不明')].copy()
    if detail.empty:
        st.info('今回の条件ではペースを分類できる成績がありません。')
    else:
        detail_rows = []
        for (h, p), g in detail.groupby(['馬名', '推定ペース']):
            finish = g['着順_数値'].dropna()
            n = len(finish)
            detail_rows.append({'馬名': h, '推定ペース': p, '対象走数': len(g), '着順有効走数': n,
                                'データ量': sample_label(n),
                                '勝率(%)': round(100 * (finish == 1).sum() / n, 1) if n else float('nan'),
                                '複勝率(%)': round(100 * (finish <= 3).sum() / n, 1) if n else float('nan'),
                                '平均4角': round(g['4角順位'].mean(), 2),
                                '上がり順位対象走数': int(g['上がり順位'].notna().sum()),
                                '平均上がり順位': round(g['上がり順位'].mean(), 2)})
        comparison = pd.DataFrame(detail_rows)
        order = {'ハイ寄り': 0, 'ミドル寄り': 1, 'スロー寄り': 2}
        comparison['_順'] = comparison['推定ペース'].map(order)
        comparison = comparison.sort_values(['馬名', '_順']).drop(columns='_順')
        st.dataframe(display_horse_column(comparison, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True,
                     column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
        st.download_button('ペース別成績をCSVで保存', display_horse_column(comparison, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                           file_name='keiba_pace_compare_v15.csv', mime='text/csv', use_container_width=True)
    with st.expander('ペース判定の根拠（レースごとの前後半3F）', expanded=False):
        st.caption('差＝前半3F−後半3F。負なら前半が速く、正なら後半が速い。各レースの元データと分類を照合できます。')
        cols = ['馬名', '日付', 'レース名', 'レース格', '競馬場', '距離_m', '前半3F', '後半3F', '前後半3F差', '暫定ペース', '推定ペース', 'ペース判定元', '着順_数値']
        pace_audit = pace_compare_base[[c for c in dict.fromkeys(cols) if c in pace_compare_base.columns]].copy()
        if '日付' in pace_audit.columns:
            pace_audit['日付'] = pd.to_datetime(pace_audit['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
        st.write(f'判定可能：{int(pace_audit["推定ペース"].ne("不明").sum())}走 ／ 全{len(pace_audit)}走')
        st.dataframe(display_horse_column(pace_audit, horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True,
                     column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
        st.download_button('ペース判定根拠をCSVで保存', display_horse_column(pace_audit, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'),
                           file_name='keiba_pace_audit_v15.csv', mime='text/csv', use_container_width=True)

    with st.expander('200mラップ欄の試験取得（最大5レース・Ver.15.8）', expanded=False):
        st.caption('まずページのラップタイム欄の文字を取得できるか確認します。'
                   '累積タイムと区間ラップが混ざる可能性があるため、このテストでは速度タイプの自動判定はしません。')
        sample_ids = []
        if 'レースID' in pace_compare_base.columns:
            sample_ids = (pace_compare_base['レースID'].fillna('').astype(str)
                          .str.replace(r'\.0$', '', regex=True))
            sample_ids = sample_ids[sample_ids.str.fullmatch(r'\d{12}')].drop_duplicates().head(5).tolist()
        if not sample_ids:
            st.info('試験取得できるレースIDがありません。')
        elif st.button('ラップ欄を5レースだけ試験取得', key='lap_raw_v158'):
            texts = []
            for rid in sample_ids:
                try:
                    texts.append({'レースID': rid, '状態': '取得', 'ラップ欄原文': get_lap_section_text(rid)})
                except Exception as e:
                    texts.append({'レースID': rid, '状態': '取得不可', 'ラップ欄原文': str(e)[:150]})
            st.session_state['lap_raw_v158_result'] = pd.DataFrame(texts)
        if 'lap_raw_v158_result' in st.session_state:
            raw_laps = st.session_state['lap_raw_v158_result']
            st.dataframe(raw_laps, hide_index=True, use_container_width=True)
            st.download_button('ラップ原文のテスト結果をCSVで保存',
                               raw_laps.to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_lap_raw_test_v158.csv', mime='text/csv', use_container_width=True)

    with st.expander('ペース判定の精度チェック（Ver.15.8）', expanded=False):
        st.caption('レース単位で重複を除き、判定の食い違い・距離別分布・判定できないレースを確認します。対象は現在選択中の直近走数・競馬場・馬場・コース・距離・レース格に従い、推定ペースの選択だけは無視します。')
        audit = pace_compare_base.copy()
        # レースIDが取得できた場合は最優先で使用。ない場合はレース日、競馬場、レース名、距離を組み合わせる。
        rid_series = (audit['レースID'].fillna('').astype(str).str.strip()
                      if 'レースID' in audit.columns else pd.Series('', index=audit.index))
        valid_rid = rid_series.str.fullmatch(r'\d{12}').fillna(False)
        day_series = pd.to_datetime(audit['日付'], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
        name_series = audit['レース名'].fillna('').astype(str) if 'レース名' in audit else pd.Series('', index=audit.index)
        venue_series = audit['競馬場'].fillna('').astype(str)
        dist_series = audit['距離_m'].astype('Int64').astype(str)
        backup = day_series + '|' + venue_series + '|' + name_series + '|' + dist_series
        # レースを識別できない場合、別レースを無理にまとめない。
        backup_ok = day_series.ne('') & name_series.str.strip().ne('') & venue_series.str.strip().ne('')
        audit['_race_key'] = ['ID:' + r if ok else '補完:' + b if bk else '行:' + str(i)
                              for i, (r, ok, b, bk) in enumerate(zip(rid_series, valid_rid, backup, backup_ok))]
        audit['_識別方法'] = ['レースID' if ok else '日付・場所・レース名' if bk else '識別不可（行単位）'
                              for ok, bk in zip(valid_rid, backup_ok)]
        pace_order = ['ハイ寄り', 'ミドル寄り', 'スロー寄り', '不明']
        dist_bins = [-float('inf'), 1600, 1800, 2200, 2400, float('inf')]
        dist_labels = ['1600m以下', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上']
        audit['_距離帯'] = pd.cut(audit['距離_m'], bins=dist_bins, labels=dist_labels)
        rows_audit = []
        for key, grp in audit.groupby('_race_key', sort=False):
            first = grp.iloc[0]
            classifications = sorted(set(grp['推定ペース'].fillna('不明').astype(str)))
            f3 = pd.to_numeric(grp['前半3F'], errors='coerce').dropna()
            l3 = pd.to_numeric(grp['後半3F'], errors='coerce').dropna()
            d3 = pd.to_numeric(grp['前後半3F差'], errors='coerce').dropna()
            # 同一レースで数値が異なる場合、分類が同じでもデータ不一致として検出する。
            numeric_conflict = any(len(vals) >= 2 and vals.max() - vals.min() > 0.11 for vals in [f3, l3, d3])
            status = ('識別不可' if first['_識別方法'] == '識別不可（行単位）'
                      else '不一致' if len(classifications) > 1 or numeric_conflict
                      else '判定不能' if classifications == ['不明'] else '一致')
            rid = str(first['レースID']).strip() if 'レースID' in grp.columns else ''
            rows_audit.append({'日付': day_series.loc[grp.index[0]], 'レース名': first['レース名'],
                               '競馬場': first['競馬場'], '距離_m': first['距離_m'],
                               '距離帯': str(first['_距離帯']) if pd.notna(first['_距離帯']) else '距離不明',
                               'レースID': rid if re.fullmatch(r'\d{12}', rid) else '',
                               '識別方法': first['_識別方法'], '登録馬数': len(grp),
                               '前半3F': round(f3.iloc[0], 1) if not f3.empty else float('nan'),
                               '後半3F': round(l3.iloc[0], 1) if not l3.empty else float('nan'),
                               '前後半3F差': round(d3.iloc[0], 1) if not d3.empty else float('nan'),
                               '推定ペース': ' / '.join(classifications), '判定元': ' / '.join(sorted(set(grp['ペース判定元'].fillna('不明').astype(str)))), '判定チェック': status,
                               'レース結果URL': f'https://race.netkeiba.com/race/result.html?race_id={rid}' if re.fullmatch(r'\d{12}', rid) else ''})
        race_audit = pd.DataFrame(rows_audit)
        if race_audit.empty:
            st.info('この分析条件では検証できるレースがありません。')
        else:
            total = len(race_audit)
            conflicts = int((race_audit['判定チェック'] == '不一致').sum())
            unknown = int((race_audit['判定チェック'] == '判定不能').sum())
            unidentified = int((race_audit['判定チェック'] == '識別不可').sum())
            c1, c2, c3 = st.columns(3)
            c1.metric('異なるレース数', total)
            c2.metric('判定不一致', conflicts)
            c3.metric('判定不能', unknown)
            if unidentified:
                st.warning(f'識別に必要な情報がない成績が{unidentified}件あり、行単位で扱っています。レース数は概算です。')
            if conflicts:
                st.error(f'同じレースなのにペースの分類または数値が違うレースが{conflicts}件あります。下の一覧で確認してください。')
            else:
                st.success('識別できたレースについて、馬ごとのペース判定の食い違いは見つかりませんでした。')
            st.markdown('**距離別のペース分布（重複レース除外）**')
            valid_once = race_audit[race_audit['判定チェック'] == '一致'].copy()
            counts_pace = pd.crosstab(valid_once['距離帯'], valid_once['推定ペース']).reindex(columns=pace_order[:3], fill_value=0)
            counts_pace['判定可能レース数'] = counts_pace.sum(axis=1)
            if unknown:
                missing_by_dist = race_audit[race_audit['判定チェック'] == '判定不能'].groupby('距離帯').size()
                counts_pace['判定不能'] = missing_by_dist.reindex(counts_pace.index, fill_value=0).astype(int)
            st.dataframe(counts_pace.reset_index(), hide_index=True, use_container_width=True)
            st.caption('ハイ寄りの割合が少なくても「ハイに弱い」ことは意味しません。距離ごとのサンプル数と判定不能数を見てください。')
            st.markdown('**判定基準を変えた場合の分布（感度チェック）**')
            st.caption('以下は前後半3F差の閾値を変えた参考比較。netkeibaのS/M/Hとは別の方法です。')
            sensitivity = []
            eligible = race_audit[(race_audit['判定チェック'] == '一致') &
                                  pd.to_numeric(race_audit['前後半3F差'], errors='coerce').notna()].copy()
            diff_values = pd.to_numeric(eligible['前後半3F差'], errors='coerce')
            for threshold in [0.5, 1.0, 1.5]:
                high = int((diff_values < -threshold).sum())
                slow = int((diff_values > threshold).sum())
                middle = int(len(diff_values) - high - slow)
                sensitivity.append({'閾値': f'±{threshold:.1f}秒' + ('（現行）' if threshold == 1.0 else ''),
                                    'ハイ寄り': high, 'ミドル寄り': middle, 'スロー寄り': slow,
                                    '判定可能レース': len(diff_values)})
            sensitivity_df = pd.DataFrame(sensitivity)
            st.dataframe(sensitivity_df, hide_index=True, use_container_width=True)
            st.download_button('閾値感度チェックをCSVで保存', sensitivity_df.to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_pace_threshold_v153.csv', mime='text/csv', use_container_width=True)
            st.info('今の取得データにはレース中盤の区間ラップがありません。中盤まで考慮したペース判定は、別途ラップ取得の実装・検証が必要です。今回は本番のペース判定基準は変更していません。')
            st.markdown('**各馬のペース経験数**')
            horse_pace = pd.crosstab(audit['馬名'], audit['推定ペース']).reindex(columns=pace_order, fill_value=0)
            horse_pace = horse_pace.reindex(sorted(audit['馬名'].dropna().unique()), fill_value=0)
            horse_pace['判定可能走数'] = horse_pace[pace_order[:3]].sum(axis=1)
            horse_pace['ハイ実績'] = horse_pace['ハイ寄り'].map(lambda n: 'データなし' if n == 0 else f'{n}走')
            horse_pace.index.name = '馬名'
            st.dataframe(display_horse_column(horse_pace.reset_index(), horses, st.session_state.auto_numbers), hide_index=True, use_container_width=True)
            st.markdown('**レース単位の判定一覧**')
            st.dataframe(race_audit, hide_index=True, use_container_width=True,
                         column_config={'レース名': st.column_config.TextColumn('レース名', width='medium'),
                                        'レース結果URL': st.column_config.LinkColumn('レース結果')})
            st.download_button('ペース精度チェックをCSVで保存', race_audit.to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_pace_validation_v151.csv', mime='text/csv', use_container_width=True)

    st.download_button('📥 分析結果をCSVで保存', data=display_horse_column(result, horses, st.session_state.auto_numbers).to_csv(index=False).encode('utf-8-sig'), file_name='keiba_analysis.csv', mime='text/csv', use_container_width=True)
