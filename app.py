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
st.title('🏇 競馬分析ラボ Ver.14.5')
st.caption('自動取得・上がり順位・ペース別適性・脚質分析')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
VENUES = ['札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉']
METRICS = ['勝率(%)', '連対率(%)', '複勝率(%)', '平均着順', '平均4角', '平均上がり', '上がり最速率(%)', '上がり3位以内率(%)', '平均上がり順位', '前方率(%)', '差し追込率(%)']
NUMERIC = ['距離_m', '着順_数値', '上がり3F_秒', '4角順位', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差']
ALIASES = {'馬名': ['馬名', '馬名_正規化'], '競馬場': ['競馬場', '場所', '開催場'], '馬場状態': ['馬場状態', '馬 場', '馬場'], '距離_m': ['距離_m', '距離', '距離(m)'], '着順_数値': ['着順_数値', '着順'], '上がり3F_秒': ['上がり3F_秒', '上がり3F', '上り', '上がり'], '4角順位': ['4角順位', '4角', '4角通過順位'], '上がり順位': ['上がり順位', '上り順位'], '日付': ['日付', '年月日', '開催日', 'レース日'], '頭数': ['頭数', '頭 数'], '前半3F': ['前半3F'], '後半3F': ['後半3F'], '前後半3F差': ['前後半3F差'], '推定ペース': ['推定ペース'], '推定脚質': ['推定脚質'], 'レース格': ['レース格', '格付け', 'グレード'], 'レース名': ['レース名'], '格付け根拠': ['格付け根拠']}


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


@st.cache_data(ttl=1800, show_spinner=False)
def get_horses(race_id):
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    horses = {}
    for a in soup.select('a[href*="/horse/"]'):
        name = a.get_text(' ', strip=True)
        match = re.search(r'/horse/(\d{10})', a.get('href', ''))
        if match and name:
            horses[match.group(1)] = name
    if not horses:
        raise ValueError('出走馬を取得できませんでした。ページ構造やアクセス制限を確認してください。')
    return horses


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
    diff = data['前後半3F差']
    data['推定ペース'] = '不明'
    data.loc[diff.notna() & diff.lt(-1.0), '推定ペース'] = 'ハイ寄り'
    data.loc[diff.notna() & diff.between(-1.0, 1.0), '推定ペース'] = 'ミドル寄り'
    data.loc[diff.notna() & diff.gt(1.0), '推定ペース'] = 'スロー寄り'
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
    cached_choice = st.selectbox(
        '保存済みデータを再開（このサーバーに残っている分）',
        ['選択しない'] + known_ids,
        format_func=lambda rid: cached_labels.get(rid, rid) if rid != '選択しない' else rid
    )
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
if 'auto_df' not in st.session_state:
    st.session_state.auto_df = None

if race_id and st.session_state.auto_race_id != race_id:
    st.session_state.auto_race_id = race_id
    st.session_state.auto_horses = {}
    st.session_state.auto_df = None
    try:
        restored = load_race_cache(race_id)
        if restored is not None:
            st.session_state.auto_horses, st.session_state.auto_df = restored
    except Exception as e:
        st.warning(f'保存済みデータの復元に失敗しました：{e}')
if race_id and st.session_state.auto_df is not None:
    st.info(f'保存済みデータを利用中：{len(st.session_state.auto_df)}走。再取得せず分析できます。')

if st.button('出走馬を自動取得', disabled=not race_id, use_container_width=True):
    try:
        with st.spinner('出馬表を確認しています…'):
            st.session_state.auto_horses = get_horses(race_id)
            st.session_state.auto_df = None
    except Exception as e:
        st.error(f'出走馬の取得に失敗しました：{e}')

horses = st.session_state.auto_horses
if horses:
    st.success(f'出走馬：{len(horses)}頭を取得しました')
    st.write('、'.join(horses.values()))
    if st.button('全馬の過去成績を取得', use_container_width=True):
        datasets = []
        errors = []
        progress = st.progress(0)
        status = st.empty()
        for i, (horse_id, name) in enumerate(horses.items(), 1):
            status.write(f'取得中：{name}（{i}/{len(horses)}）')
            try:
                datasets.append(get_results(horse_id, name))
            except Exception as e:
                errors.append(f'{name}：{e}')
            progress.progress(i / len(horses))
            if i < len(horses):
                time.sleep(1)
        if datasets:
            try:
                st.session_state.auto_df = prepare(convert_auto(pd.concat(datasets, ignore_index=True)))
                save_race_cache(race_id, horses, st.session_state.auto_df)
                st.success(f'過去成績：{len(st.session_state.auto_df)}走を取得・一時保存しました')
            except Exception as e:
                st.error(f'成績の変換に失敗しました：{e}')
        if errors:
            st.warning('取得できなかった馬：' + ' / '.join(errors))
        status.empty()

if st.session_state.auto_df is not None:
    st.subheader('上がり順位の追加取得（Ver.14）')
    available = int(st.session_state.auto_df['上がり順位'].notna().sum())
    st.write(f'現在、上がり順位が判明している成績：{available}走')
    st.caption('各馬の直近5走を対象に、レース全体の上がりタイムから同タイム同順位で算出します。レース結果ページの仕様によって取得できない場合があります。')
    if st.button('直近5走の上がり順位を取得', use_container_width=True):
        try:
            updated, checked, errors = add_recent_agari_ranks(st.session_state.auto_df)
            st.session_state.auto_df = updated
            save_race_cache(race_id, horses, updated)
            count = int(updated['上がり順位'].notna().sum())
            st.success(f'照合したレース：{checked}件 / 上がり順位を取得済み：{count}走')
            if errors:
                st.warning(f'確認できなかったレース：{len(errors)}件。最初の例：{errors[0]}')
        except Exception as e:
            st.error(f'上がり順位の取得に失敗：{e}')

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
    audit_horse = st.selectbox('確認する馬', audit_horses, key='audit_horse_v144')
    audit_grade = st.selectbox('表示する格付け', ['すべて', 'G1', 'G2', 'G3', 'その他', '不明'], key='audit_grade_v144')
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
    st.dataframe(audit_view, hide_index=True, use_container_width=True,
                 column_config={'馬名': st.column_config.TextColumn('馬名', width='medium'),
                                'レース名': st.column_config.TextColumn('レース名', width='medium'),
                                '格付け根拠': st.column_config.TextColumn('格付け根拠', width='large'),
                                'レース結果URL': st.column_config.LinkColumn('レース結果を確認', display_text='結果を見る')})
    st.download_button('格付けチェック表をCSVで保存', audit_view.to_csv(index=False).encode('utf-8-sig'),
                       file_name='keiba_grade_audit.csv', mime='text/csv', use_container_width=True)
    missing_names = int(analysis_df['レース名'].fillna('').eq('').sum())
    if missing_names:
        st.warning(f'レース名がないデータが{missing_names}走あります。旧バージョンの保存データは、レース格を検証するために再取得が必要です。')
    st.caption('格付け判定は取得ページの表記に依存します。G1・G2・G3と表示されても、根拠欄や実際のレース名と一致しているか確認してください。')

st.header('⑥ 分析条件')
mode = st.radio('分析モード', ['ランキング', '馬を比較'], horizontal=True)
venue = st.selectbox('競馬場', ['すべて'] + sorted(analysis_df['競馬場'].unique().tolist()))
track = st.selectbox('馬場状態', ['すべて'] + sorted(analysis_df['馬場状態'].unique().tolist()))
course_options = ['すべて'] + sorted(analysis_df['コース'].dropna().astype(str).unique().tolist()) if 'コース' in analysis_df.columns else ['すべて']
course = st.selectbox('コース', course_options)
distance = st.selectbox('距離', ['すべて', '1200m以下', '1201〜1600m', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'])
recent = st.selectbox('対象レース', ['直近5走', '直近10走', '直近15走', '全走'])
grade_filter = st.selectbox('レース格', ['全レース', '重賞のみ（G1・G2・G3）', 'G1のみ', 'その他（L・OP・条件戦など）'])
st.caption('格付けが確認できない走は「不明」として保持。重賞・G1・その他の絞り込みから除外します。')
pace_filter = st.selectbox('推定ペース', ['すべて', 'スロー寄り', 'ミドル寄り', 'ハイ寄り', '不明'])
st.caption('前半3Fと後半3Fの差が±1秒以内をミドル寄り、前半が1秒超速ければハイ寄り、遅ければスロー寄りとする簡易分類。公式のペース判定ではありません。距離や馬場条件で比較し、少数サンプルに注意してください。')
st.caption('脚質は4角位置と頭数から便宜的に推定。先頭（4角）＝逃げ確定ではなく、差し追込率は後方位置の割合です。')
st.caption('集計順序：各馬の全成績から直近○走を確定 → 競馬場・馬場・距離・レース格・ペースで絞り込み。例：直近5走＋G1のみ＝直近5走の中にあるG1だけ。')
counts = analysis_df['レース格'].value_counts()
st.caption('判定済み：' + ' / '.join(f'{g} {int(counts.get(g, 0))}走' for g in ['G1', 'G2', 'G3', 'その他']) + f' / 不明 {int(counts.get("不明", 0))}走')
if int(counts.get('不明', 0)):
    st.info('レース格が不明な成績があります。以前のバージョンで取得したデータは格付け未保存の場合があります。必要に応じて新しく過去成績を取得し直してください。')
if mode == 'ランキング':
    metric = st.selectbox('ランキング指標', METRICS)
else:
    options = sorted(analysis_df['馬名'].unique().tolist())
    selected = st.multiselect('比較する馬（2〜3頭）', options, default=options[:2], max_selections=3)
view = st.radio('表示方法', ['スマホ用カード', '横並び表'], horizontal=True)

st.header('⑦ 分析する')
if st.button('分析する', type='primary', use_container_width=True):
    # Ver.14.5：先に各馬の「直近N走」を確定し、その範囲内で条件を適用する。
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
    if pace_filter != 'すべて':
        filtered = filtered[filtered['推定ペース'] == pace_filter]
    ranges = {'1200m以下': (0, 1200), '1201〜1600m': (1201, 1600), '1601〜1800m': (1601, 1800), '1801〜2200m': (1801, 2200), '2201〜2400m': (2201, 2400), '2401m以上': (2401, float('inf'))}
    if distance in ranges:
        filtered = filtered[filtered['距離_m'].between(*ranges[distance])]
    if mode == '馬を比較':
        if len(selected) < 2:
            st.warning('比較する馬を2頭以上選んでください。')
            st.stop()
        filtered = filtered[filtered['馬名'].isin(selected)]
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
        st.dataframe(count_check, hide_index=True, use_container_width=True)
    rows = []
    for name, group in filtered.groupby('馬名'):
        row = summarize(group)
        row['馬名'] = name
        rows.append(row)
    result = pd.DataFrame(rows)
    if mode == 'ランキング':
        ascending = metric in ['平均着順', '平均4角', '平均上がり', '平均上がり順位']
        result = result.sort_values(metric, ascending=ascending, na_position='last', kind='stable').reset_index(drop=True)
        result.insert(0, '順位', result[metric].rank(method='min', ascending=ascending, na_option='bottom').astype(int))
        st.subheader(f'🏆 {metric} ランキング')
    else:
        result['表示順'] = result['馬名'].apply(selected.index)
        result = result.sort_values('表示順').drop(columns='表示順')
        st.subheader('🐎 馬の成績比較')
    result = result.round(2)
    if mode == 'ランキング':
        # 順位と馬名を常に先頭に。重要指標を続ける。
        front = ['順位', '馬名', '出走数', metric]
        result = result[[c for c in dict.fromkeys(front) if c in result.columns]
                        + [c for c in result.columns if c not in front]]
    else:
        result = result[['馬名'] + [c for c in result.columns if c != '馬名']]
    if view == '横並び表':
        if mode == 'ランキング':
            full_table = st.checkbox('詳細指標をすべて表示', value=False)
            key_cols = ['順位', '馬名', '出走数', metric, '複勝率(%)', '平均4角', '上がり最速率(%)']
            compact_cols = [c for c in dict.fromkeys(key_cols) if c in result.columns]
            show_table = result if full_table else result[compact_cols]
            st.dataframe(show_table, hide_index=True, use_container_width=True,
                         column_config={'馬名': st.column_config.TextColumn('馬名', width='medium')})
        else:
            st.dataframe(result.set_index('馬名').T, use_container_width=True)
    else:
        for _, row in result.iterrows():
            with st.container(border=True):
                label = f'🐎 {row["馬名"]}'
                if mode == 'ランキング':
                    label = f'{int(row["順位"])}位 {label}'
                st.markdown(f'### {label}')
                st.caption(f'対象：{int(row["出走数"])}走')
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
        st.dataframe(used, hide_index=True, use_container_width=True)
        st.download_button('集計対象レース一覧をCSVで保存', data=used.to_csv(index=False).encode('utf-8-sig'),
                           file_name='keiba_used_races_v145.csv', mime='text/csv', use_container_width=True)
    st.subheader('ペース別の成績と4角位置')
    st.caption('下表は分析条件のうち推定ペース以外の条件にもとづく参考比較ではなく、現在の絞り込み結果のみを使用します。')
    detail = filtered[filtered['推定ペース'].ne('不明')].copy()
    if detail.empty:
        st.info('ペースを分類できる成績がありません。過去成績の自動取得を使うと「ペース」列を取り込めます。')
    else:
        if mode == '馬を比較':
            detail = detail[detail['馬名'].isin(selected)]
        detail_rows = []
        for (h, p), g in detail.groupby(['馬名', '推定ペース']):
            valid_finishes = g['着順_数値'].dropna()
            detail_rows.append({'馬名': h, '推定ペース': p, '対象走数': len(g), '勝率(%)': round(100 * (valid_finishes == 1).mean(), 1) if len(valid_finishes) else float('nan'), '複勝率(%)': round(100 * (valid_finishes <= 3).mean(), 1) if len(valid_finishes) else float('nan'), '平均4角': round(g['4角順位'].mean(), 2), '平均上がり順位': round(g['上がり順位'].mean(), 2)})
        st.dataframe(pd.DataFrame(detail_rows).sort_values(['馬名', '推定ペース']), hide_index=True, use_container_width=True)
    st.download_button('📥 分析結果をCSVで保存', data=result.to_csv(index=False).encode('utf-8-sig'), file_name='keiba_analysis.csv', mime='text/csv', use_container_width=True)
