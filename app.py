import re
import time
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='競馬分析ラボ', page_icon='🏇', layout='wide')
st.title('🏇 競馬分析ラボ Ver.13')
st.caption('過去成績の自動取得・上がり順位の追加分析・Excel比較')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
VENUES = ['札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉']
METRICS = ['勝率(%)', '連対率(%)', '複勝率(%)', '平均着順', '平均4角', '平均上がり', '上がり最速率(%)', '上がり3位以内率(%)', '平均上がり順位']
NUMERIC = ['距離_m', '着順_数値', '上がり3F_秒', '4角順位', '上がり順位']
ALIASES = {'馬名': ['馬名', '馬名_正規化'], '競馬場': ['競馬場', '場所', '開催場'], '馬場状態': ['馬場状態', '馬 場', '馬場'], '距離_m': ['距離_m', '距離', '距離(m)'], '着順_数値': ['着順_数値', '着順'], '上がり3F_秒': ['上がり3F_秒', '上がり3F', '上り', '上がり'], '4角順位': ['4角順位', '4角', '4角通過順位'], '上がり順位': ['上がり順位', '上り順位'], '日付': ['日付', '年月日', '開催日', 'レース日']}


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
            records.append(record)
    if not records:
        raise ValueError('成績の抽出件数が0件です')
    return pd.DataFrame(records)


@st.cache_data(ttl=86400, show_spinner=False)
def get_race_agari(race_id):
    """Returns horse-id -> finishing-speed rank, or raises on an unknown layout."""
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one('table.RaceTable01')
    if table is None:
        raise ValueError('レース結果表が見つかりません')
    header = table.select_one('thead tr')
    if header is None:
        raise ValueError('見出しが見つかりません')
    headings = [re.sub(r'\s+', '', c.get_text(' ', strip=True)) for c in header.select('th,td')]
    speed_col = next((i for i, name in enumerate(headings) if name in ('上り', '上がり', '上り3F', '上がり3F')), None)
    horse_col = next((i for i, name in enumerate(headings) if name in ('馬名',)), None)
    if speed_col is None or horse_col is None:
        raise ValueError('馬名・上がり列を特定できません')
    speeds = {}
    for tr in table.select('tbody tr'):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) <= max(speed_col, horse_col):
            continue
        a = cells[horse_col].select_one('a[href*="/horse/"]')
        if a is None:
            continue
        match = re.search(r'/horse/(\d{10})', a.get('href', ''))
        if not match:
            continue
        speed = pd.to_numeric(cells[speed_col].get_text(' ', strip=True), errors='coerce')
        if pd.notna(speed) and 20 <= float(speed) <= 60:
            speeds[match.group(1)] = float(speed)
    if len(speeds) < 2:
        raise ValueError('上がりタイムが十分にありません')
    sorted_speeds = sorted(speeds.values())
    return {horse_id: 1 + sum(v < speed for v in sorted_speeds) for horse_id, speed in speeds.items()}


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
        rank = ranks_by_race.get(str(row['レースID']), {}).get(str(row['馬ID']))
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
    return data[['馬名', '馬ID', 'レースID', '日付', '競馬場', 'コース', '距離_m', '馬場状態', '着順_数値', '4角順位', '上がり3F_秒', '上がり順位']].copy()


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
    data['馬名'] = data['馬名'].astype(str).str.strip()
    data = data[data['馬名'].ne('') & data['馬名'].ne('nan')].copy()
    if '日付' in data.columns:
        data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
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
    return {'出走数': len(group), '平均着順': finish.mean(), '平均4角': group['4角順位'].mean(), '平均上がり': group['上がり3F_秒'].mean(), '勝率(%)': rate((finish == 1).sum(), len(finish)), '連対率(%)': rate((finish <= 2).sum(), len(finish)), '複勝率(%)': rate((finish <= 3).sum(), len(finish)), '上がり対象走数': len(agari), '上がり最速率(%)': rate((agari == 1).sum(), len(agari)), '上がり3位以内率(%)': rate((agari <= 3).sum(), len(agari)), '平均上がり順位': agari.mean()}


st.header('① レースを指定')
race_input = st.text_input('netkeiba出馬表URL または12桁のレースID', placeholder='例：202605041111')
race_id = parse_race_id(race_input) if race_input.strip() else ''
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
                st.success(f'過去成績：{len(st.session_state.auto_df)}走を取得しました')
            except Exception as e:
                st.error(f'成績の変換に失敗しました：{e}')
        if errors:
            st.warning('取得できなかった馬：' + ' / '.join(errors))
        status.empty()

if st.session_state.auto_df is not None:
    st.subheader('上がり順位の追加取得（Ver.13）')
    available = int(st.session_state.auto_df['上がり順位'].notna().sum())
    st.write(f'現在、上がり順位が判明している成績：{available}走')
    st.caption('各馬の直近5走を対象に、レース全体の上がりタイムから同タイム同順位で算出します。レース結果ページの仕様によって取得できない場合があります。')
    if st.button('直近5走の上がり順位を取得', use_container_width=True):
        try:
            updated, checked, errors = add_recent_agari_ranks(st.session_state.auto_df)
            st.session_state.auto_df = updated
            count = int(updated['上がり順位'].notna().sum())
            st.success(f'照合したレース：{checked}件 / 上がり順位を取得済み：{count}走')
            if errors:
                st.warning(f'確認できなかったレース：{len(errors)}件。最初の例：{errors[0]}')
        except Exception as e:
            st.error(f'上がり順位の取得に失敗：{e}')

st.caption('自動取得は接続先の仕様・アクセス制限によって失敗する場合があります。取得データの利用条件も確認してください。')
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

st.header('⑤ 分析条件')
mode = st.radio('分析モード', ['ランキング', '馬を比較'], horizontal=True)
venue = st.selectbox('競馬場', ['すべて'] + sorted(analysis_df['競馬場'].unique().tolist()))
track = st.selectbox('馬場状態', ['すべて'] + sorted(analysis_df['馬場状態'].unique().tolist()))
course_options = ['すべて'] + sorted(analysis_df['コース'].dropna().astype(str).unique().tolist()) if 'コース' in analysis_df.columns else ['すべて']
course = st.selectbox('コース', course_options)
distance = st.selectbox('距離', ['すべて', '1200m以下', '1201〜1600m', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'])
recent = st.selectbox('対象レース', ['直近5走', '直近10走', '直近15走', '全走'])
st.caption('直近走数は、競馬場・馬場・距離などで絞り込んだ後に適用します。')
if mode == 'ランキング':
    metric = st.selectbox('ランキング指標', METRICS)
else:
    options = sorted(analysis_df['馬名'].unique().tolist())
    selected = st.multiselect('比較する馬（2〜3頭）', options, default=options[:2], max_selections=3)
view = st.radio('表示方法', ['スマホ用カード', '横並び表'], horizontal=True)

st.header('⑥ 分析する')
if st.button('分析する', type='primary', use_container_width=True):
    filtered = analysis_df.copy()
    if venue != 'すべて':
        filtered = filtered[filtered['競馬場'] == venue]
    if track != 'すべて':
        filtered = filtered[filtered['馬場状態'] == track]
    if course != 'すべて' and 'コース' in filtered.columns:
        filtered = filtered[filtered['コース'] == course]
    ranges = {'1200m以下': (0, 1200), '1201〜1600m': (1201, 1600), '1601〜1800m': (1601, 1800), '1801〜2200m': (1801, 2200), '2201〜2400m': (2201, 2400), '2401m以上': (2401, float('inf'))}
    if distance in ranges:
        filtered = filtered[filtered['距離_m'].between(*ranges[distance])]
    if '日付' in filtered.columns:
        filtered = filtered.sort_values('日付', ascending=False, kind='stable')
    if recent != '全走':
        n = int(recent.replace('直近', '').replace('走', ''))
        filtered = filtered.groupby('馬名', sort=False).head(n)
    if mode == '馬を比較':
        if len(selected) < 2:
            st.warning('比較する馬を2頭以上選んでください。')
            st.stop()
        filtered = filtered[filtered['馬名'].isin(selected)]
    if filtered.empty:
        st.warning('条件に一致するレースがありません。')
        st.stop()
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
    if view == '横並び表':
        st.dataframe(result.set_index('馬名').T if mode == '馬を比較' else result, hide_index=mode == 'ランキング', use_container_width=True)
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
                st.caption(f'上がり順位の集計対象：{int(row["上がり対象走数"])}走')
    st.download_button('📥 分析結果をCSVで保存', data=result.to_csv(index=False).encode('utf-8-sig'), file_name='keiba_analysis.csv', mime='text/csv', use_container_width=True)
