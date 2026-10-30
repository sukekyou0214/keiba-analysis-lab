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
st.title('🏇 競馬分析ラボ Ver.19.2')
st.caption('一括取得 → 総合診断 → 序盤の位置取りと逃げ候補の競合から想定ペースを暫定予測。')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
VENUES = ['札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉']
METRICS = ['勝率(%)', '連対率(%)', '複勝率(%)', '平均着順', '平均4角', '平均上がり', '上がり最速率(%)', '上がり3位以内率(%)', '平均上がり順位', '前方率(%)', '差し追込率(%)']
NUMERIC = ['距離_m', '着順_数値', '上がり3F_秒', '4角順位', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差']
ALIASES = {'馬名': ['馬名', '馬名_正規化'], '競馬場': ['競馬場', '場所', '開催場'], '馬場状態': ['馬場状態', '馬 場', '馬場'], '距離_m': ['距離_m', '距離', '距離(m)'], '着順_数値': ['着順_数値', '着順'], '上がり3F_秒': ['上がり3F_秒', '上がり3F', '上り', '上がり'], '4角順位': ['4角順位', '4角', '4角通過順位'], '上がり順位': ['上がり順位', '上り順位'], '日付': ['日付', '年月日', '開催日', 'レース日'], '頭数': ['頭数', '頭 数'], '前半3F': ['前半3F'], '後半3F': ['後半3F'], '前後半3F差': ['前後半3F差'], '推定ペース': ['推定ペース'], '推定脚質': ['推定脚質'], 'レース格': ['レース格', '格付け', 'グレード'], 'レース名': ['レース名'], '格付け根拠': ['格付け根拠'], '通過順': ['通過順', '通過']}



def interpret_running_pattern(row):
    """好走/凡走した *レース全体* のラップ差を説明する。能力スコアではない。"""
    ng = int(row.get('好走走数', 0) or 0)
    nb = int(row.get('凡走走数', 0) or 0)
    if ng == 0:
        return '好走データなし', '判定不可', '3着以内に入ったラップ確認レースがありません。'
    if nb == 0:
        return '凡走データなし', '判定不可', '4着以下のラップ確認レースがないため、好走・凡走の比較はできません。'
    if min(ng, nb) < 2:
        confidence = '参考（片側1走）'
    elif min(ng, nb) < 3:
        confidence = '暫定（片側2走）'
    else:
        confidence = '傾向（各3走以上）'
    def diff(k):
        x = row.get(k + '_差', float('nan'))
        try:
            x = float(x)
            return x if pd.notna(x) else None
        except (ValueError, TypeError):
            return None
    end, early, width = diff('終盤加速'), diff('残1000m加速'), diff('残1000mラップ幅')
    labels, explanations = [], []
    # 0.25秒/200mという差は表示上の仮閾値。統計的有意性を意味しない。
    if end is not None and end >= 0.25:
        labels.append('終盤加速型')
        explanations.append('好走時は凡走時より終盤600mの加速が大きい')
    elif end is not None and end <= -0.25:
        labels.append('急加速に頼らない型')
        explanations.append('好走時は凡走時ほど終盤の急加速を伴わない')
    if early is not None and early >= 0.25:
        labels.append('早め加速型')
        explanations.append('好走時は残り1000mからのペースアップが大きい')
    elif early is not None and early <= -0.25:
        labels.append('早仕掛け不要型')
        explanations.append('好走時は残り1000mからのペースアップが小さい')
    if width is not None and width <= -0.30:
        labels.append('一定ラップ型')
        explanations.append('好走時は残り1000mのラップ変動が小さい')
    elif width is not None and width >= 0.30:
        labels.append('緩急対応型')
        explanations.append('好走時は残り1000mのラップ変動が大きい')
    if not labels:
        return '特徴差が小さい', confidence, '今回の集計では好走時と凡走時に明瞭なラップ差がありません。'
    # ラベルは多くても2つに整理。複数指標を総合し、元データを確認できるようにする。
    ranked = list(zip(labels, explanations))
    # 展開を説明する順番: 終盤加速 → 早め加速 → 持続性。3つ目は補足文だけに残す。
    title = '＋'.join(x[0] for x in ranked[:2])
    reason = '。'.join(x[1] for x in ranked) + '。'
    if confidence.startswith('参考'):
        reason += ' ※片側1走のため特に不確実です。'
    return title, confidence, reason



def diagnose_horse_v17(horse_df, lap_label=''):
    """位置・末脚は馬別、展開はレース全体。欠測をゼロ評価にしない。"""
    d = horse_df.copy()
    if d.empty:
        return {'走りの総合診断': '判定材料なし', '位置取り': '不明', '末脚': '不明',
                '展開': '不明', 'S/M/H': '不明', '信頼度': '判定不可', '対象走数': 0,
                '好走走数': 0, '位置取得': 0, '上がり取得': 0, '根拠': '該当レースなし'}
    d['着順_数値'] = pd.to_numeric(d['着順_数値'], errors='coerce')
    d = d.loc[d['着順_数値'].ge(1)].copy()
    good = d.loc[d['着順_数値'].le(3)].copy()
    def numeric(k):
        return pd.to_numeric(good[k], errors='coerce') if k in good.columns else pd.Series(dtype=float)
    n_good = len(good)
    pos = numeric('4角順位')
    field = numeric('頭数')
    valid_pos = (pos.ge(1) & field.ge(2) & pos.le(field))
    fractions = (pos[valid_pos] / field[valid_pos]).dropna()
    n_pos = len(fractions)
    pos_label = '不明'
    if n_pos:
        med = fractions.median()
        pos_label = '前方' if med <= .35 else ('中団' if med <= .70 else '後方')
    pace_values = good['推定ペース'].dropna().astype(str) if '推定ペース' in good.columns else pd.Series(dtype=str)
    pace_values = pace_values[pace_values.isin(['スロー寄り', 'ミドル寄り', 'ハイ寄り'])]
    pace_summary = '・'.join(f'{k.replace("寄り", "")}{v}走' for k, v in pace_values.value_counts().items()) if len(pace_values) else '不明'
    agari = numeric('上がり順位')
    agari = agari.loc[agari.ge(1)].dropna()
    n_agari = len(agari)
    agari_label = '不明'
    if n_agari:
        rate = agari.le(3).mean()
        agari_label = '末脚上位' if rate >= .6 else ('末脚まちまち' if rate >= .3 else '末脚上位少')
    if n_good == 0:
        title = '好走データなし'
    elif not n_pos and not n_agari:
        title = '走り方は不明'
    elif n_pos and n_agari:
        suffix = '×末脚' if agari_label == '末脚上位' else ''
        title = {'前方':'前方型','中団':'中団型','後方':'後方型'}[pos_label] + suffix
    elif n_pos:
        title = pos_label + '型'
    else:
        title = '末脚型' if agari_label == '末脚上位' else '脚質不明'
    # ラップ判定の根拠は各馬のラップではなく出走したレース全体のもの。
    short_lap = str(lap_label).split('＋')[0]
    if short_lap in ('特徴差が小さい', '好走データなし', '凡走データなし'):
        short_lap = '判定保留'
    if len(short_lap) > 12:
        short_lap = short_lap[:12]
    if n_good == 0:
        conf = '判定不可'
    elif n_good < 3 or (n_pos and n_pos < 2) or (n_agari and n_agari < 2):
        conf = '参考'
    elif n_good < 5 or n_pos == 0 or n_agari == 0:
        conf = '暫定'
    else:
        conf = '傾向あり'
    reason = (f'3着以内 {n_good}/{len(d)}走。好走時の4角位置データ {n_pos}走'
              f'（中央値の頭数比から{pos_label}判定）、上がり順位 {n_agari}走'
              f'（{agari_label}）。好走時のnetkeibaペース内訳：{pace_summary}。'
              f'ラップ特徴は「{short_lap}」〔馬自身ではなくレース全体〕。')
    return {'走りの総合診断': title, '位置取り': pos_label, '末脚': agari_label,
            '展開': short_lap, 'S/M/H': pace_summary, '信頼度': conf, '対象走数': len(d),
            '好走走数': n_good, '位置取得': n_pos, '上がり取得': n_agari, '根拠': reason}



def estimate_field_pace_v19(data, names):
    """出走予定馬の序盤通過順から逃げ競合を暫定推定する。

    4角先頭は単騎逃げの証拠ではない。欠損時は4角だけでHを強行しない。
    予測ルールは仮説であり、事後レースでの検証前のため確率は出さない。
    """
    names = list(dict.fromkeys(names))
    records = []
    for name in names:
        h = data.loc[data['馬名'].eq(name)].copy()
        if '日付' in h.columns:
            h = h.sort_values('日付', ascending=False, na_position='last', kind='stable')
        h = h.head(5)
        p = pd.to_numeric(h['4角順位'], errors='coerce')
        f = pd.to_numeric(h['頭数'], errors='coerce')
        valid = p.ge(1) & f.ge(2) & p.le(f)
        n = int(valid.sum())
        lead = int((valid & p.eq(1)).sum())
        front = int((valid & p.le((f*.35).clip(lower=2))).sum())
        # netkeiba「通過」は 1-2-3-4 / 2-2 / 3 のような形式。
        # 最初の通過地点がスタート直後とは限らないため参考指標に限定。
        corners = h['通過順'] if '通過順' in h.columns else pd.Series('', index=h.index)
        early_positions = []
        early_lead = 0
        early_front = 0
        for raw, size in zip(corners, f):
            pieces = re.findall(r'\d+', str(raw))
            if not pieces or pd.isna(size) or size < 2:
                continue
            first = int(pieces[0])
            if not (1 <= first <= size):
                continue
            early_positions.append(first)
            early_lead += int(first == 1)
            early_front += int(first <= max(2, size*.35))
        en = len(early_positions)
        # 逃げ候補は初通過地点の先頭実績を優先（最低2走）。
        escape_candidate = en >= 2 and early_lead/en >= .4
        forward_candidate = en >= 2 and early_front/en >= .6
        records.append({'馬名':name, '位置データ':n, '初通過取得':en,
                        '初通過先頭':early_lead, '初通過前方':early_front,
                        '4角先頭':lead, '4角前方':front,
                        '逃げ候補':escape_candidate, '先行候補':forward_candidate})
    info = pd.DataFrame(records)
    total = len(info)
    known = int(info['初通過取得'].ge(2).sum()) if total else 0
    leaders = int(info['逃げ候補'].sum()) if total else 0
    forward = int(info['先行候補'].sum()) if total else 0
    coverage = known / total if total else 0
    if not total or coverage < .6:
        pred = None
        explanation = (f'序盤通過順を2走以上確認できた馬が{known}/{total}頭。'
                       '4角位置だけでは逃げ争いを判断できないため判定保留。')
    elif leaders >= 2 and forward >= max(4, (total+3)//4):
        pred = 'ハイ（H）'
        explanation = '序盤の先頭常連が複数、かつ先行候補も多い。逃げ競合によるHシナリオが有力。'
    elif leaders == 1 and forward <= max(3, total//4):
        pred = 'スロー（S）'
        explanation = '序盤先頭常連が1頭で先行候補も少なめ。単騎逃げのSシナリオが有力。'
    elif leaders == 0 and forward <= max(2, total//6):
        pred = 'スロー（S）'
        explanation = '序盤先頭常連・先行候補が少なく、Sシナリオが有力。'
    else:
        pred = 'ミドル（M）'
        explanation = '逃げ競合・先行圧力の材料が拮抗しておりMを中心に想定。'
    return pred, explanation, info, known, leaders, forward

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


def parse_lap_html_v1591(html, race_id):
    """4列ごとの繰り返し見出しを含むSP版ラップ表を、区間距離で結合・照合。"""
    soup = BeautifulSoup(html, 'html.parser')
    heading = next((h for h in soup.find_all(['h2', 'h3', 'h4'])
                    if 'ラップタイム' in h.get_text(' ', strip=True)), None)
    if heading is None:
        raise ValueError('ラップタイム見出しが見つかりません')

    number_pattern = r'(?<![0-9])(?:[0-9]{1,2}:)?[0-9]{1,3}\.[0-9](?![0-9])'
    def seconds(t):
        t = str(t)
        if ':' in t:
            minute, second = t.split(':', 1)
            return int(minute)*60 + float(second)
        return float(t)

    def numbers(el):
        return [seconds(x) for x in re.findall(number_pattern, el.get_text(' ', strip=True))]

    # レースの距離はページ内の「芝2000m」「ダート1800m」等から取得する。
    page_text = soup.get_text(' ', strip=True)
    dist_match = re.search(r'(?:芝|ダート|ダ\.?|障害|障)\s*[:：]?\s*(\d{3,4})\s*m', page_text, re.I)
    race_distance = int(dist_match.group(1)) if dist_match else None

    # 部分的に壊れた表があっても、区間距離をキーにして結合する。
    segments = {}
    samples = []
    tables = []
    for el in heading.next_elements:
        if getattr(el, 'name', None) in ('h2', 'h3') and el is not heading:
            break
        if getattr(el, 'name', None) == 'table':
            tables.append(el)
        if len(tables) >= 20:
            break

    for table in tables:
        rows = table.find_all('tr')
        if not rows:
            continue
        blocks = []
        for idx, tr in enumerate(rows):
            cells = tr.find_all(['td', 'th'], recursive=False)
            vals = []
            for cell in cells:
                m = re.fullmatch(r'\s*(\d{3,4})\s*m\s*', cell.get_text(' ', strip=True), re.I)
                if m:
                    vals.append(int(m.group(1)))
            if len(vals) >= 1 and all(v % 200 == 0 for v in vals):
                blocks.append((idx, vals))
        if not blocks:
            continue
        samples.append(table.get_text(' ', strip=True)[:900])
        for block_idx, (header_index, distances) in enumerate(blocks):
            end = blocks[block_idx+1][0] if block_idx+1 < len(blocks) else len(rows)
            width = len(distances)
            lines = []
            for tr in rows[header_index+1:end]:
                cells = tr.find_all(['th','td'], recursive=False)
                if len(cells) >= 1:
                    vals = [numbers(x) for x in cells]
                    if any(vals):
                        lines.append(vals)
            if not lines:
                continue
            # 最終ブロックが「1800m」の1列だけになる場合を処理。
            # 累積と区間ラップが同一セル、別セル、別行のいずれでも拾う。
            if width == 1:
                flat = [v for line in lines for cell in line for v in cell]
                if len(flat) >= 2:
                    cv, lv = flat[0], flat[1]
                    if 8 <= lv <= 25 and cv >= lv:
                        segments[distances[0]] = (cv, lv)
                        continue
            # ① 通過タイムの横1行＋ラップの横1行
            if len(lines) >= 2 and all(len(v)==1 for v in lines[0]) and all(len(v)==1 for v in lines[1]) and len(lines[0]) >= width and len(lines[1]) >= width and all(lines[1][i][0] < 30 for i in range(width)):
                for d, cv, lv in zip(distances, lines[0], lines[1]):
                    segments[d] = (cv[0], lv[0])
                continue
            # ② 1セルの中に累積・200mの2数値が入る横長行
            combined = next((line for line in lines if len(line)>=width and all(len(v)>=2 for v in line[:width])), None)
            if combined:
                for d, v in zip(distances, combined):
                    segments[d] = (v[0], v[-1])
                continue
            # ③ 4区間を縦4行で保持 (各行に累積・ラップ)
            if len(lines) >= width and all(len(line)>=2 and len(line[0])==1 and len(line[1])==1 for line in lines[:width]):
                for d, line in zip(distances, lines[:width]):
                    segments[d] = (line[0][0], line[1][0])

    # 最終区間の補完：CSVにも残るラップ表全文を第2経路で解析する。
    # 最後の見出しが1列だけになっている場合のHTML構造差を吸収する。
    lap_recovery = '不要'
    if race_distance and race_distance % 200 == 0 and race_distance not in segments:
        last = race_distance
        prev = last - 200
        if prev in segments:
            # 先に各テーブルの文字列を採取する。構造を解析できない最終1列表も対象。
            all_table_text = [t.get_text(' ', strip=True) for t in tables]
            # 「1800m 1:45.5 11.6」のような3点組を解析。
            # タイム差による検算なしの補完は絶対にしない。
            distance_expr = r'(?<![0-9])' + str(last) + r'\s*m'
            time_expr = r'((?:[0-9]{1,2}:)?[0-9]{1,3}\.[0-9])'
            pattern = re.compile(distance_expr + r'\s+' + time_expr + r'\s+' + time_expr + r'(?![0-9])', re.I)
            for raw in list(samples) + all_table_text:
                normalized = re.sub(r'\s+', ' ', raw.replace('：', ':')).strip()
                for match in pattern.finditer(normalized):
                    cumulative = seconds(match.group(1))
                    lap = seconds(match.group(2))
                    if 8 <= lap <= 25 and abs(cumulative - segments[prev][0] - lap) < 0.21:
                        segments[last] = (cumulative, lap)
                        lap_recovery = '末尾3点組・累積差一致'
                        break
                if last in segments:
                    break
            if last not in segments:
                lap_recovery = '末尾候補なし／累積差不一致'

    observed = sorted(segments)
    laps = [segments[d][1] for d in observed]
    cumul = [segments[d][0] for d in observed]
    checks = []
    if not observed:
        checks.append('ラップ表を解釈できません')
    else:
        if observed != list(range(200, observed[-1]+1, 200)):
            checks.append('途中の200m区間が欠けています')
        if race_distance is None:
            checks.append('レース距離を特定できません')
        elif race_distance % 200 != 0:
            checks.append('200m単位でないレース距離です')
        elif observed[-1] != race_distance or len(observed) != race_distance//200:
            checks.append(f'区間不足：{len(observed)}/{race_distance//200}区間')
        if any(not (8 <= v <= 25) for v in laps):
            checks.append('200mとして不自然なタイムあり')
        total = 0
        for d in observed:
            cv, lv = segments[d]
            total += lv
            if abs(total-cv) > 0.65:
                checks.append(f'{d}m通過タイムとラップ合計が不一致')
                break
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    return {'状態':'検証OK' if not checks else '要確認',
            'レース距離':race_distance if race_distance else '',
            '200mラップ':' - '.join(f'{v:.1f}' for v in laps),
            '区間数':len(laps), '期待区間数':race_distance//200 if race_distance else '',
            '合計秒':round(sum(laps),1) if laps else '',
            '検証':'OK' if not checks else ' / '.join(checks),
            '末尾補完':lap_recovery,
            '表の一部':' / '.join(samples)[:1200], 'ページURL':url}


@st.cache_data(ttl=86400, show_spinner=False)
def get_lap_table_test_v1594(race_id):
    """netkeibaの200mラップを検証用に取得。ランキングは更新しない。"""
    if not re.fullmatch(r'\d{12}', str(race_id)):
        raise ValueError('レースID不正')
    url = f'https://db.sp.netkeiba.com/race/{race_id}/'
    response = requests.get(url, headers=HEADERS, timeout=18)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return parse_lap_html_v1591(response.text, race_id)




def classify_lap_shape_v16(lap_text, distance, validation='OK'):
    """200mラップ試験分類。持続性と絶対的な速さを分けて評価する（暫定）。"""
    if str(validation) != 'OK':
        return {'展開タイプ': '判定不可', '判定根拠': 'ラップ検証がOKではありません'}
    try:
        distance = int(distance)
        laps = [float(x.strip()) for x in str(lap_text).split(' - ')]
        if distance < 1200 or distance % 200 or len(laps) != distance // 200:
            raise ValueError('区間数不一致')
        if any(not (8 <= x <= 25) for x in laps):
            raise ValueError('ラップ値異常')
    except (ValueError, TypeError):
        return {'展開タイプ': '判定不可', '判定根拠': '距離とラップ数が不一致・不正'}

    n = len(laps)
    early = laps[1:max(3, n // 2)]  # スタートの200mは除く
    early_avg = sum(early) / len(early)
    late = sum(laps[-3:]) / 3
    before = sum(laps[-6:-3]) / 3
    accel = before - late
    fade = late - early_avg
    tail5 = laps[-5:]
    threshold = early_avg - 0.20
    quick = [x <= threshold for x in tail5]
    longest = run = 0
    for hit in quick:
        run = run + 1 if hit else 0
        longest = max(longest, run)
    sustained_count = sum(quick)
    tail_spread = max(tail5) - min(tail5)

    # 「速さ」と「持続」を切り離す。距離の違いに対する最適化は未実施。
    tail_mean = sum(tail5) / len(tail5)
    # 残り1000m直前の200m×3（1600mは最初の加速区間を除いた2区間）。
    baseline = laps[-8:-5] if n >= 9 else laps[1:-5]
    baseline_mean = sum(baseline) / len(baseline)
    relative_gain = baseline_mean - tail_mean  # 正なら後半1000mが速い
    early_tail_mean = sum(tail5[:2]) / 2  # 残1000〜600m
    late_tail_mean = sum(tail5[-3:]) / 3
    evenness = tail_spread <= 0.45
    # 残1000〜600mの時点ですでに速く、後半1000m全体で持続しているか。
    early_activation = early_tail_mean <= baseline_mean - 0.15
    relative_sustained = (evenness and relative_gain >= 0.20 and
                          early_activation and fade < 0.70)
    # 旧方式の「高速ラップ」判定も根拠として残す。
    absolute_sustained = (sustained_count >= 4 and longest >= 3 and
                          sum(quick[:2]) >= 1 and tail_spread <= 1.2 and fade < 0.7)
    long_spurt = relative_sustained or absolute_sustained
    sharp = accel >= 0.70 and not long_spurt
    exhausting = fade >= 0.70 and accel < 0.35
    if long_spurt:
        kind = 'ロングスパート戦寄り'
    elif sharp:
        kind = '瞬発戦寄り'
    elif exhausting:
        kind = '消耗戦寄り'
    else:
        kind = '混合・判定保留'
    if long_spurt:
        approach = '持続性＋相対加速' if relative_sustained else '高速区間連続（旧基準）'
    else:
        approach = '非該当'
    reason = (f'残1000m平均{tail_mean:.2f}s／直前基準{baseline_mean:.2f}s／相対加速{relative_gain:+.2f}s。'
              f'残1000〜600m平均{early_tail_mean:.2f}s／ラップ幅{tail_spread:.2f}s。'
              f'高速区間{sustained_count}/5・連続最長{longest}。'
              f'終盤600m加速{accel:+.2f}s／序盤比減速{fade:+.2f}s。'
              f'ロンスパ根拠：{approach}。※独自の暫定分類であり各馬の脚質判定ではありません')
    return {'展開タイプ': kind, '判定根拠': reason,
            '序盤平均_秒': round(early_avg, 2),
            '終盤600m平均_秒': round(late, 2),
            '終盤加速幅_秒': round(accel, 2),
            '終盤減速幅_秒': round(fade, 2),
            '高速区間数_残1000m': sustained_count,
            '最長高速連続区間数': longest,
            '残1000mラップ幅_秒': round(tail_spread, 2),
            '残1000m平均_秒': round(tail_mean, 2),
            '残1000m直前基準_秒': round(baseline_mean, 2),
            '残1000m相対加速_秒': round(relative_gain, 2),
            '残1000〜600m平均_秒': round(early_tail_mean, 2),
            '残600m平均_秒': round(late_tail_mean, 2),
            '持続性_ラップ幅045以内': '該当' if evenness else '非該当',
            '早めの加速': '該当' if early_activation else '非該当',
            'ロンスパ判定方法': approach,
            '持続判定': '該当' if long_spurt else '非該当'}


def get_lap_race_title(rid, sample):
    """保存済みの馬成績のレース名を使う。不足時のみ結果ページを参照。"""
    race_name = ''
    race_day = ''
    if 'レースID' in sample.columns:
        rows = sample[sample['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True).eq(str(rid))]
        if not rows.empty:
            for col, var in [('レース名', 'race_name'), ('日付', 'race_day')]:
                if col in rows:
                    vals = rows[col].dropna().astype(str).str.strip()
                    vals = vals[~vals.isin(['', 'nan', 'None'])]
                    if not vals.empty:
                        if col == 'レース名': race_name = vals.iloc[0]
                        else: race_day = vals.iloc[0]
    if not race_name:
        try:
            res = requests.get('https://race.netkeiba.com/race/result.html?race_id=' + str(rid),
                               headers=HEADERS, timeout=9)
            res.raise_for_status()
            res.encoding = res.apparent_encoding
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.title.get_text(' ', strip=True) if soup.title else ''
            name = re.split(r'\s+結果|\s*[|｜]\s*', title, maxsplit=1)[0].strip()
            if name and 'netkeiba' not in name.lower() and 'エラー' not in name:
                race_name = name
        except (requests.RequestException, ValueError):
            pass
    return race_name or 'レース名未取得', race_day


def lap_v16_cache_path(rid):
    return CACHE_DIR / f'lap_v16_{rid}.json'


def load_lap_v16(rid):
    path = lap_v16_cache_path(rid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def save_lap_v16(rid, payload):
    path = lap_v16_cache_path(rid)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def lap_full_progress_path(rid):
    """レース単位の一時保存。デプロイやサーバー停止で消える場合があります。"""
    if not re.fullmatch(r'\d{12}', str(rid)):
        return None
    return CACHE_DIR / f'lap_full_v165_{rid}.csv.gz'


def load_lap_full_progress(rid):
    path = lap_full_progress_path(rid)
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, compression='gzip', dtype={'レースID':str}).fillna('')
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def save_lap_full_progress(rid, frame):
    path = lap_full_progress_path(rid)
    if path is None or frame is None or frame.empty:
        return
    tmp = path.with_suffix('.tmp')
    frame.to_csv(tmp, index=False, compression='gzip')
    tmp.replace(path)


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
    data['通過順'] = data['通過'].fillna('').astype(str)
    data['4角順位'] = pd.to_numeric(data['通過'].astype(str).str.extract(r'(\d+)\s*$')[0], errors='coerce')
    data['上がり順位'] = float('nan')
    data['日付'] = pd.to_datetime(data['日付'], errors='coerce')
    data['頭数'] = pd.to_numeric(data.get('頭 数'), errors='coerce')
    # netkeibaの「ペース」は前半3F-後半3Fの表記。距離差があるため目安として扱う。
    pace_pair = data['ペース'].astype(str).str.extract(r'(\d{2}\.\d)\s*[-－ー〜～]\s*(\d{2}\.\d)') if 'ペース' in data.columns else pd.DataFrame(index=data.index, columns=[0, 1])
    data['前半3F'] = pd.to_numeric(pace_pair[0], errors='coerce')
    data['後半3F'] = pd.to_numeric(pace_pair[1], errors='coerce')
    data['前後半3F差'] = data['前半3F'] - data['後半3F']
    return data[['馬名', '馬ID', 'レースID', '日付', '競馬場', 'コース', '距離_m', '馬場状態', '着順_数値', '4角順位', '上がり3F_秒', '上がり順位', '頭数', '前半3F', '後半3F', '前後半3F差', 'レース名', '格付け根拠', 'レース格', '通過順']].copy()


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
    if '通過順' not in data.columns:
        data['通過順'] = ''  # 旧保存データでも起動できるようにする
    data['通過順'] = data['通過順'].fillna('').astype(str)
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


st.subheader('レースを選ぶ')
known_ids = saved_race_ids()
if 'race_input_v141' not in st.session_state:
    remembered = st.query_params.get('race_id', '')
    st.session_state.race_input_v141 = remembered if parse_race_id(remembered) else ''
if known_ids:
    cached_labels = {rid: get_saved_race_label(rid) for rid in known_ids}
    cached_choice = tap_choice('保存済みレース', ['選択しない'] + known_ids, 'saved_race_choice_v153', format_func=lambda rid: cached_labels.get(rid, rid) if rid != '選択しない' else rid)
    if st.button('保存済みレースを開く', disabled=cached_choice == '選択しない', use_container_width=True):
        st.session_state.race_input_v141 = cached_choice
        st.rerun()
race_input = st.text_input('netkeiba出馬表URL または12桁のレースID', key='race_input_v141', placeholder='例：202605041111')
race_id = parse_race_id(race_input) if race_input.strip() else ''
if race_id:
    st.query_params['race_id'] = race_id
if race_input.strip():
    if race_id:
        st.caption(f'レースID：{race_id}')
    else:
        st.warning('正しいURLまたは12桁のレースIDを入力してください。')

st.caption('初回は一括取得、保存済みなら自動復元します。')
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
            # 保存済み成績には馬番が含まれないため、出馬表の馬番だけを再照合する。
            # 出馬表取得が失敗しても保存済み成績と診断はそのまま利用する。
            try:
                latest_horses_182, latest_numbers_182 = get_horses(race_id)
                if latest_horses_182:
                    st.session_state.auto_horses = latest_horses_182
                    st.session_state.auto_numbers = latest_numbers_182
                    st.session_state.auto_df = update_cached_names(st.session_state.auto_df, latest_horses_182)
            except Exception:
                pass
    except Exception as e:
        st.warning(f'保存済みデータの復元に失敗しました：{e}')
if race_id and st.session_state.auto_df is not None:
    prepared_data = st.session_state.auto_df
    st.success(f'データ準備完了｜{len(prepared_data)}走。総合診断はこの下に表示しています。')
if st.session_state.get('fetch_issues_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
    st.warning('一部のデータを取得できませんでした。詳細は「精度チェック」で確認できます。')
if st.session_state.get('fetch_fatal_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
    st.error('一括取得を完了できませんでした。詳細は「精度チェック」を確認してください。')


# URL入力から1タップで、出走馬・過去成績・上がり順位まで取得する。
# 保存済みの成績は再利用し、上がり順位がまだない場合は追加照合する。
if st.button('出走馬・成績・上がり・S/M/Hを一括取得', type='primary',
             disabled=not race_id, use_container_width=True):
    st.session_state.pop('fetch_fatal_v174', None)
    transient_progress_v174 = st.empty()
    with transient_progress_v174.container():
        st.caption('取得中だけ進捗と処理結果を表示します。完了後は自動で閉じます。')
        st.session_state['fetch_issues_v174'] = []
        st.session_state['fetch_issues_race_v174'] = race_id
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
                st.session_state['fetch_issues_v174'].append(f'出馬表更新情報：{e}')
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
                    st.session_state['fetch_issues_v174'].append('過去成績の取得失敗：' + ' / '.join(horse_errors))
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
                    st.session_state['fetch_issues_v174'].append(f'上がり順位取得不可：{len(agari_errors)}レース')
                    st.warning(f'上がり順位を取得できなかったレース：{len(agari_errors)}件。例：{agari_errors[0]}')
            except Exception as e:
                st.session_state['fetch_issues_v174'].append(f'上がり順位の照合失敗：{e}')
                st.warning(f'過去成績は保存済みですが、上がり順位の照合は失敗しました：{e}。同じボタンで再試行できます。')

            # Ver.17.3: netkeibaのペースも同じボタンで差分取得。途中10件ごとに保存する。
            pace_work = st.session_state.auto_df
            if pace_work is not None and 'レースID' in pace_work.columns:
                pids = pace_work['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
                valid_pids = pids[pids.str.fullmatch(r'\d{12}')].drop_duplicates().tolist()
                old_pace = pace_work['netkeibaペース'].fillna('').astype(str).str.strip() if 'netkeibaペース' in pace_work.columns else pd.Series('', index=pace_work.index)
                acquired = set(pids[old_pace.isin(['スロー寄り','ミドル寄り','ハイ寄り'])])
                pending_pids = [r for r in valid_pids if r not in acquired]
                if pending_pids:
                    st.caption(f'S/M/Hの未取得分 {len(pending_pids)}レースを追加取得中（10件ごと保存）')
                    pace_progress = st.progress(0)
                    new_paces, errors = {}, []
                    for j, pr in enumerate(pending_pids, 1):
                        try:
                            new_paces[pr] = get_netkeiba_pace(pr)
                        except Exception as ex:
                            errors.append({'レースID':pr,'状態':'取得不可','理由':str(ex)[:180]})
                        if j % 10 == 0 or j == len(pending_pids):
                            if new_paces:
                                pace_work = apply_netkeiba_pace(pace_work, new_paces)
                                st.session_state.auto_df = pace_work
                                save_race_cache(race_id, horses_now, pace_work)
                                new_paces.clear()
                            st.session_state['pace_v157_failed'] = errors.copy()
                        pace_progress.progress(j/len(pending_pids))
                        if j < len(pending_pids):
                            time.sleep(0.35)
                    pace_progress.empty()
                    st.success(f'S/M/Hの差分取得完了：今回 {len(pending_pids)-len(errors)}/{len(pending_pids)} レース')
                    if errors:
                        st.session_state['fetch_issues_v174'].append(f'S/M/H取得不可：{len(errors)}レース')
                        st.warning(f'S/M/Hを取得できなかったレース：{len(errors)}件。精度チェックで確認できます。')
                else:
                    st.caption('S/M/H判定は保存済みデータを再利用しました。')
        except Exception as e:
            st.session_state['fetch_fatal_v174'] = str(e)
            st.error(f'まとめて取得できませんでした：{e}')
    transient_progress_v174.empty()
    st.rerun()

def render_pace_check_and_retry():
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


# Ver.19.2: データ修復はメイン画面を散らかさないように折りたたみに配置。
# 成功した場合のみ差し替え、古い保存データを消さずに残す。
with st.expander('序盤通過順の確認・過去成績の強制再取得（Ver.19.2）', expanded=False):
    current_v192 = st.session_state.auto_df
    if current_v192 is not None and not current_v192.empty:
        total_v192 = len(current_v192)
        corners_v192 = (current_v192['通過順'].fillna('').astype(str)
                        if '通過順' in current_v192.columns else pd.Series('', index=current_v192.index))
        valid_v192 = corners_v192.apply(lambda x: bool(re.search(r'\d+', x)))
        st.write(f'保存済み過去成績：**{total_v192}走**／通過順の数値あり：**{int(valid_v192.sum())}走**')
        bad_v192 = current_v192.loc[~valid_v192].copy()
        if not bad_v192.empty:
            st.warning(f'通過順が確認できない成績が{len(bad_v192)}走あります。旧キャッシュの可能性があります。')
        else:
            st.info('通過順の数値は保存されています。予測が判定保留なら、初通過地点の取得走数・頭数も確認してください。')
        with st.expander('保存済み通過順のサンプルを確認', expanded=False):
            show_cols_v192 = [c for c in ['馬名','レース名','日付','通過順','4角順位','頭数'] if c in current_v192.columns]
            st.dataframe(current_v192[show_cols_v192].head(15), use_container_width=True, hide_index=True)
    else:
        st.caption('まだ過去成績がありません。先に出馬表URLから一括取得してください。')
    st.caption('再取得は全馬の過去成績だけを再読込し、上がり順位・ペースなど取得済みの値はレースIDを使ってできるだけ引き継ぎます。元データは取得が成功するまで保持します。')
    confirm_v192 = st.checkbox('現在のレースの全馬過去成績を再取得する（通信が発生します）', key='confirm_force_v192')
    if st.button('過去成績を強制再取得・通過順を更新', type='secondary',
                 disabled=(not confirm_v192 or not race_id), use_container_width=True):
        current_horses_v192 = st.session_state.auto_horses
        if not current_horses_v192:
            st.error('出走馬一覧がありません。先に一括取得を実行してください。')
        else:
            try:
                get_results.clear()  # @st.cache_data の旧データを再利用しない
                fetched_v192, errors_v192 = [], []
                prg_v192 = st.progress(0)
                for idx_v192, (hid_v192, name_v192) in enumerate(current_horses_v192.items(), 1):
                    try:
                        raw_v192 = get_results(hid_v192, name_v192)
                        fetched_v192.append(raw_v192)
                    except Exception as exc_v192:
                        errors_v192.append(f'{name_v192}: {str(exc_v192)[:100]}')
                    prg_v192.progress(idx_v192 / len(current_horses_v192))
                prg_v192.empty()
                if errors_v192:
                    st.error(f'{len(errors_v192)}頭で取得失敗したので、保存済みデータを維持しました。')
                    with st.expander('失敗した馬を見る'):
                        st.write(' / '.join(errors_v192))
                else:
                    rebuilt_v192 = prepare(convert_auto(pd.concat(fetched_v192, ignore_index=True)))
                    old_v192 = st.session_state.auto_df
                    if old_v192 is not None and not old_v192.empty:
                        # 元データと同一馬・同一レースであると確認できた行だけ補助情報を復元する。
                        keep_v192 = [c for c in ['上がり順位','netkeibaペース'] if c in old_v192.columns]
                        if keep_v192 and all(c in old_v192.columns for c in ['馬ID','レースID']):
                            previous_v192 = old_v192[['馬ID','レースID'] + keep_v192].copy()
                            previous_v192['馬ID'] = previous_v192['馬ID'].astype(str)
                            previous_v192['レースID'] = previous_v192['レースID'].astype(str)
                            previous_v192 = previous_v192.drop_duplicates(['馬ID','レースID'])
                            rebuilt_v192['馬ID'] = rebuilt_v192['馬ID'].astype(str)
                            rebuilt_v192['レースID'] = rebuilt_v192['レースID'].astype(str)
                            rebuilt_v192 = rebuilt_v192.drop(columns=keep_v192, errors='ignore').merge(
                                previous_v192, on=['馬ID','レースID'], how='left', validate='many_to_one')
                            rebuilt_v192 = prepare(rebuilt_v192)
                    new_valid_v192 = int(rebuilt_v192['通過順'].fillna('').astype(str).str.contains(r'\d+',regex=True).sum())
                    if new_valid_v192 == 0:
                        st.error('再取得データでも通過順が0走でした。旧データを保持します。取得元のHTML列名を調べる必要があります。')
                    else:
                        st.session_state.auto_df = rebuilt_v192
                        save_race_cache(race_id, current_horses_v192, rebuilt_v192)
                        st.success(f'通過順を{new_valid_v192}/{len(rebuilt_v192)}走で確認し、データを更新しました。')
                        st.rerun()
            except Exception as exc_v192:
                st.error(f'再取得を完了できませんでした。以前の保存データは維持しています：{exc_v192}')

horses = st.session_state.auto_horses
st.caption('データの保存はサーバー一時保存です。再起動で消える可能性があるため、必要ならデータ管理からExcelへ保存してください。')
with st.expander('データ管理・手入力・Excel/CSV（必要なときだけ）', expanded=False):
    if horses:
        st.write(f'出走馬：{len(horses)}頭')
        st.write('、'.join(horse_display(n, horses, st.session_state.auto_numbers) for n in horses.values()))
    if st.session_state.auto_df is not None:
        available = int(st.session_state.auto_df['上がり順位'].notna().sum())
        st.caption(f'保存済みの過去成績：{len(st.session_state.auto_df)}走／上がり順位取得済み：{available}走')
    manual = st.text_area('手入力する出走馬名（任意・1行に1頭）', height=95)
    st.caption('自動取得に失敗したときや、外部のExcel/CSVを使うときだけ設定します。')
    uploads = st.file_uploader('分析データを追加（任意）', type=['xlsx', 'csv'], accept_multiple_files=True)

target_horses = list(dict.fromkeys([n.strip() for n in manual.splitlines() if n.strip()] or list(horses.values())))
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
    st.info('レースURLを入力して「一括取得」を押すか、「データ管理」からExcel/CSVを追加してください。')
    st.stop()
df = pd.concat(datasets, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)
with st.expander('取得データをExcelでバックアップ', expanded=False):
    st.caption(f'{len(df)}走・{df["馬名"].nunique()}頭を読み込み済み。')
    st.download_button('Excelを保存', data=make_excel(df), file_name='競馬分析_統合データ.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)

analysis_df = df.copy()
if target_horses:
    names_available = set(analysis_df['馬名'].unique())
    missing = [n for n in target_horses if n not in names_available]
    analysis_df = analysis_df[analysis_df['馬名'].isin(target_horses)].copy()
    if missing:
        st.warning('データが見つからない馬：' + '、'.join(missing))
if analysis_df.empty:
    st.error('分析対象の馬の成績がありません。')
    st.stop()
st.caption(f'分析準備完了：{analysis_df["馬名"].nunique()}頭・{len(analysis_df)}走')

# Ver.17.1: 通常利用は診断を最優先。保存済みラップから自動で計算する。
# Streamlitのタブにより詳細分析のボタンや長い表を通常画面から分離する。
tab_main171, tab_detail171, tab_quality172 = st.tabs(['🏇 総合診断', '📊 詳細分析', '🛠️ 精度チェック'])
with tab_main171:
    st.subheader('出走馬の総合診断')
    st.caption('初期条件：各馬の直近10走のうちG1・G2・G3。操作なしで自動表示。条件を変える場合は「詳細分析」へ。')
    quick171 = analysis_df.copy()
    if '日付' in quick171.columns:
        quick171 = quick171.sort_values('日付', ascending=False, na_position='last', kind='stable')
    quick171 = quick171.groupby('馬名', sort=False).head(10).copy()
    quick171 = quick171.loc[quick171['レース格'].isin(['G1','G2','G3'])].copy()
    quick_race171 = str(st.session_state.get('auto_race_id', ''))
    quick_shapes171 = load_lap_full_progress(quick_race171)
    if quick_shapes171.empty:
        maybe171 = st.session_state.get('lap_shapes_v163')
        if (isinstance(maybe171, pd.DataFrame) and not maybe171.empty
                and st.session_state.get('lap_shapes_v163_race') == quick_race171):
            quick_shapes171 = maybe171.copy()
    metric_names171 = ['終盤加速幅_秒', '残1000m相対加速_秒', '残1000mラップ幅_秒']
    quick_lapped171 = pd.DataFrame()
    if not quick171.empty and 'レースID' in quick171.columns and not quick_shapes171.empty and 'レースID' in quick_shapes171.columns:
        qbase171 = quick171.copy()
        qbase171['_quick_rid171'] = qbase171['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        qshape171 = quick_shapes171.copy()
        qshape171['_quick_rid171'] = qshape171['レースID'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        if '検証' in qshape171.columns:
            qshape171 = qshape171[qshape171['検証'].eq('OK')].copy()
        for m171 in metric_names171:
            if m171 not in qshape171.columns:
                qshape171[m171] = float('nan')
            qshape171[m171] = pd.to_numeric(qshape171[m171], errors='coerce')
        qshape171 = qshape171.drop_duplicates('_quick_rid171')
        quick_lapped171 = qbase171.merge(qshape171[['_quick_rid171'] + metric_names171], on='_quick_rid171', how='inner')
        quick_lapped171['着順_数値'] = pd.to_numeric(quick_lapped171['着順_数値'], errors='coerce')
        quick_lapped171 = quick_lapped171.drop_duplicates(['馬名','_quick_rid171'])
    quick_names171 = list(dict.fromkeys(list(horses.values()) if horses else []))
    quick_names171 += [nm for nm in quick171['馬名'].dropna().unique() if nm not in quick_names171]
    records171 = []
    for name171 in quick_names171:
        allhorse171 = quick171.loc[quick171['馬名'].eq(name171)]
        lapgroup171 = (quick_lapped171.loc[quick_lapped171['馬名'].eq(name171)]
                       if not quick_lapped171.empty else pd.DataFrame())
        if lapgroup171.empty:
            label171 = '不明'
            # ラップが未取得でも、馬自身の位置と末脚の診断は表示する。
            hdiagnose171 = allhorse171
        else:
            hdiagnose171 = lapgroup171
            good171 = lapgroup171['着順_数値'].between(1,3)
            bad171 = lapgroup171['着順_数値'].ge(4)
            comp171 = {'好走走数': int(good171.sum()), '凡走走数': int(bad171.sum())}
            for col171, lab171 in zip(metric_names171, ['終盤加速', '残1000m加速', '残1000mラップ幅']):
                va171 = pd.to_numeric(lapgroup171.loc[good171, col171], errors='coerce').dropna()
                vb171 = pd.to_numeric(lapgroup171.loc[bad171, col171], errors='coerce').dropna()
                comp171[lab171+'_差'] = va171.mean()-vb171.mean() if len(va171) and len(vb171) else float('nan')
            label171, _, _ = interpret_running_pattern(comp171)
        diag171 = diagnose_horse_v17(hdiagnose171, label171)
        diag171['馬名'] = name171
        diag171['ラップ確認走数'] = len(lapgroup171)
        records171.append(diag171)
    quick_result171 = pd.DataFrame(records171)
    if not quick_result171.empty:
        quick_display171 = display_horse_column(quick_result171, horses, st.session_state.auto_numbers)
        st.dataframe(quick_display171[['馬名','走りの総合診断','展開','信頼度','好走走数']],
                     hide_index=True, use_container_width=True,
                     height=min(700, 48+35*len(quick_display171)),
                     column_config={'馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                    '走りの総合診断': st.column_config.TextColumn('走りの特徴', width='medium'),
                                    '展開': st.column_config.TextColumn('好走展開', width='medium')})
        # Ver.18: 実際のnetkeiba S/M/H実績から、選択した展開での好走率を比較。
        # レース全体のペースであり、将来の勝率を予測した値ではない。
        st.markdown('#### 想定ペース × 相性・実績')
        # Ver.19: 出走馬の位置取りから暫定の想定ペースを表示。
        # レースの真のS/M/H判定ではない。発走前の展開は不確実である。
        predicted_v19, reason_v19, pressure_v19, known_v19, lead_v19, front_v19 = estimate_field_pace_v19(analysis_df, quick_names171)
        if predicted_v19:
            st.write(f'**暫定の想定ペース：{predicted_v19}**')
            st.caption(f'{reason_v19} 初通過位置を確認：{known_v19}/{len(quick_names171)}頭／逃げ候補：{lead_v19}頭／先行候補：{front_v19}頭。')
        else:
            st.write('**暫定の想定ペース：判定保留**')
            st.caption(reason_v19)
        st.caption('初通過地点は発走直後とは限りません。過去実績からの暫定推定で、逃げ宣言・騎手の作戦・馬場・レース前半の実ラップは未反映。多数派の予想には合わせず、根拠が強ければHも表示します。')
        with st.expander('ペース予測の根拠（各馬の位置取り）', expanded=False):
            pressure_show_v19 = display_horse_column(pressure_v19[['馬名','初通過取得','初通過先頭','初通過前方','4角先頭','逃げ候補','先行候補']], horses, st.session_state.auto_numbers)
            st.dataframe(pressure_show_v19, hide_index=True, use_container_width=True)
            st.caption('逃げ候補＝直近5走のうち初通過地点で先頭40%以上、先行候補＝初通過上位約35%が60%以上（各2走以上）。初通過地点は競馬場・距離により異なります。')
        # ユーザーによる切替を尊重。初めて表示するときだけ暫定予測を初期選択。
        if st.session_state.get('scenario_pace_race_v191') != quick_race171:
            st.session_state['scenario_pace_v18'] = predicted_v19 or 'ミドル（M）'
            st.session_state['scenario_pace_race_v191'] = quick_race171
        selected_pace18 = st.radio('今回想定するペース',
                                   ['スロー（S）', 'ミドル（M）', 'ハイ（H）'],
                                   horizontal=True, key='scenario_pace_v18')
        pace_key18 = {'スロー（S）': 'スロー寄り', 'ミドル（M）': 'ミドル寄り',
                      'ハイ（H）': 'ハイ寄り'}[selected_pace18]
        results18 = []
        details18 = {}
        pace_options18 = ['スロー寄り', 'ミドル寄り', 'ハイ寄り']
        for horse18 in quick_names171:
            raw18 = quick171.loc[quick171['馬名'].eq(horse18)].copy()
            if raw18.empty:
                continue
            raw18['着順_数値'] = pd.to_numeric(raw18['着順_数値'], errors='coerce')
            raw18 = raw18.loc[raw18['着順_数値'].ge(1)].copy()
            unknown18 = int((~raw18['推定ペース'].isin(pace_options18)).sum())
            known18 = raw18.loc[raw18['推定ペース'].isin(pace_options18)].copy()
            selected18 = known18.loc[known18['推定ペース'].eq(pace_key18)].copy()
            others18 = known18.loc[~known18['推定ペース'].eq(pace_key18)].copy()
            n18, other_n18 = len(selected18), len(others18)
            good18 = int(selected18['着順_数値'].le(3).sum())
            other_good18 = int(others18['着順_数値'].le(3).sum())
            pct18 = 100 * good18 / n18 if n18 else None
            other_pct18 = 100 * other_good18 / other_n18 if other_n18 else None
            diff18 = pct18 - other_pct18 if pct18 is not None and other_pct18 is not None else None
            if n18 == 0:
                if len(known18) == 0:
                    status18 = '判定データ不足'
                    reason18 = '着順付きのS/M/H判定済みデータがありません。'
                elif unknown18:
                    status18 = '該当0走※'
                    reason18 = f'取得済みのS/M/Hには該当0走。ただしペース未判定が{unknown18}走あるため未経験とは断定できません。'
                else:
                    status18 = '該当0走'
                    reason18 = '対象条件のペース判定済みレースには該当なし。苦手という意味ではありません。'
            elif n18 < 2 or other_n18 < 2:
                status18 = '比較不足'
                reason18 = '想定ペースと他ペースの両方が2走以上必要なため、優劣は判定しません。'
            elif diff18 >= 25:
                status18 = '他より高め' if min(n18,other_n18) >= 4 else '他より高め※'
                reason18 = '他ペースより3着内率が25ポイント以上高い。統計的な有意差や未来の勝率を意味しません。'
            elif diff18 <= -25:
                status18 = '他より低め' if min(n18,other_n18) >= 4 else '他より低め※'
                reason18 = '他ペースより3着内率が25ポイント以上低いという相対比較です。このペースの絶対的な成績が悪いとは限りません。'
            else:
                status18 = '同程度' if min(n18,other_n18) >= 4 else '同程度※'
                reason18 = '他ペースとの3着内率差が25ポイント未満です。'
            good_rows18 = selected18.loc[selected18['着順_数値'].le(3)]
            corners18 = pd.to_numeric(good_rows18['4角順位'], errors='coerce').dropna()
            agari18 = pd.to_numeric(good_rows18['上がり順位'], errors='coerce').dropna()
            pos_note18 = f'4角平均{corners18.mean():.1f}番手' if len(corners18) else '4角の有効データなし'
            if len(agari18):
                pos_note18 += f'／上がり3位内{int(agari18.le(3).sum())}/{len(agari18)}走'
            results18.append({'馬名':horse18, '相性':status18,
                              '実績': f'{good18}/{n18}走' if n18 else '—',
                              '3着内率':f'{pct18:.1f}%' if pct18 is not None else '—',
                              '想定ペース対象走数':n18, '他ペース対象走数':other_n18,
                              '他ペース実績':f'{other_good18}/{other_n18}走' if other_n18 else '—',
                              'ペース判定不能走数':unknown18})
            details18[horse18] = (f'想定ペース：{pace_key18}／3着以内{good18}/{n18}走'
                                  + (f'（{pct18:.1f}%）' if pct18 is not None else '')
                                  + f'。他ペース：{other_good18}/{other_n18}走'
                                  + (f'（{other_pct18:.1f}%）' if other_pct18 is not None else '')
                                  + f'。未判定：{unknown18}走。{reason18} 好走時：{pos_note18}。')
        pace_result18 = pd.DataFrame(results18)
        if not pace_result18.empty:
            pace_display18 = display_horse_column(pace_result18, horses, st.session_state.auto_numbers)
            st.dataframe(pace_display18[['馬名','相性','実績']], hide_index=True,
                         use_container_width=True, height=min(700,48+35*len(pace_display18)),
                         column_config={'馬名':st.column_config.TextColumn('馬番＋馬名',width='medium'),
                                        '相性':st.column_config.TextColumn('他ペース比',width='small'),
                                        '実績':st.column_config.TextColumn('3着内／出走',width='small')})
            st.caption('相性はその馬自身の他ペースと比較した傾向。実績は選択ペースでの3着以内回数／出走回数。※は少数走数の参考判定。'
                       ' 該当0走と、ペース判定データ不足は区別しています。将来の勝率予測ではありません。')
            with st.expander('各馬の根拠・対象走数・他ペースを確認',expanded=False):
                chosen_pace18 = tap_choice('確認する馬', pace_display18['馬名'].tolist(), 'scenario_horse_v18')
                chosen_index18 = pace_display18['馬名'].tolist().index(chosen_pace18)
                chosen_raw18 = pace_result18.iloc[chosen_index18]['馬名']
                st.write(details18[chosen_raw18])
                st.caption('集計対象：各馬の直近10走に含まれる重賞。netkeibaのS/M/H判定済みデータのみ。')
            st.download_button('想定ペース別比較をCSVで保存',
                               pace_display18.to_csv(index=False).encode('utf-8-sig'),
                               file_name='keiba_scenario_v182.csv',mime='text/csv',
                               use_container_width=True)
        if quick_shapes171.empty:
            st.info('保存済みのラップ分析結果がないため、現時点では位置取り・末脚を中心に表示しています。詳細分析からラップを取得すると展開診断も加わります。')
        st.caption('「参考」「暫定」は少数データの目印。レース全体のラップを馬自身の個別ラップとして扱いません。')
        with st.expander('気になる馬の診断根拠を見る', expanded=False):
            chosen171 = tap_choice('馬を選ぶ', quick_display171['馬名'].tolist(), 'quick_diagnosis_horse_v171')
            row171 = quick_display171.loc[quick_display171['馬名'].eq(chosen171)].iloc[0]
            st.markdown(f"**{row171['馬名']}：{row171['走りの総合診断']}**　{row171['信頼度']}")
            st.write(row171['根拠'])
            st.caption(f"好走 {row171['好走走数']}/{row171['対象走数']}走、ラップ確認 {row171['ラップ確認走数']}走、4角取得 {row171['位置取得']}走、上がり取得 {row171['上がり取得']}走")
        st.download_button('総合診断をCSVで保存', quick_display171.to_csv(index=False).encode('utf-8-sig'),
                           file_name='keiba_quick_diagnosis_v171.csv', mime='text/csv', use_container_width=True)
    else:
        st.info('診断に使える出走馬データがありません。')

with tab_quality172:
    st.subheader('データの精度チェック')
    if st.session_state.get('fetch_issues_race_v174') == race_id and st.session_state.get('fetch_issues_v174'):
        with st.expander('直近の一括取得で発生した注意事項', expanded=False):
            for issue in st.session_state['fetch_issues_v174']:
                st.write('・' + str(issue))
    if st.session_state.get('fetch_fatal_v174') and st.session_state.get('fetch_issues_race_v174') == race_id:
        st.error(str(st.session_state['fetch_fatal_v174']))
    with st.expander('netkeiba S/M/H取得状況・未取得分の再取得', expanded=False):
        render_pace_check_and_retry()
    st.caption('普段の予想画面から独立した検証スペースです。取得元や判定根拠を必要なときだけ確認できます。')
    st.subheader('レース格の判定チェック')
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


with tab_detail171:
    st.subheader('分析条件')
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

        with st.expander('200mラップの試験取得（5レース＋1600m確認・Ver.15.9.4）', expanded=False):
            st.caption('ラップ表の上段（通過タイム）と下段（200mラップ）を区別して読み込み、合計・累積タイムを検証します。ランキングは変更しません。')
            sample_ids = []
            if 'レースID' in pace_compare_base.columns:
                sample_ids = (pace_compare_base['レースID'].fillna('').astype(str)
                              .str.replace(r'\.0$', '', regex=True))
                sample_ids = sample_ids[sample_ids.str.fullmatch(r'\d{12}')].drop_duplicates().head(5).tolist()
            # 通常5レースに加えて、該当データがあれば1600mのレースを1件検証。
            extra_1600 = ''
            if 'レースID' in pace_compare_base.columns and '距離_m' in pace_compare_base.columns:
                distance_1600 = pd.to_numeric(pace_compare_base['距離_m'], errors='coerce').eq(1600)
                ids_1600 = (pace_compare_base.loc[distance_1600, 'レースID'].fillna('').astype(str)
                            .str.replace(r'\.0$', '', regex=True))
                candidates_1600 = ids_1600[ids_1600.str.fullmatch(r'\d{12}')]
                extra_1600 = next((r for r in candidates_1600 if r not in sample_ids), '')
                if extra_1600:
                    sample_ids.append(extra_1600)
            st.caption('対象：通常の先頭5レース' + (' ＋1600mの追加1レース' if extra_1600 else '（1600mの追加対象なし）'))
            if not sample_ids:
                st.info('試験取得できるレースIDがありません。')
            elif st.button('200mラップを試験取得', key='lap_table_v1594'):
                rows = []
                progress_lap = st.progress(0)
                for i, rid in enumerate(sample_ids, 1):
                    try:
                        result_lap = get_lap_table_test_v1594(rid)
                        rows.append({'レースID':rid, **result_lap})
                    except Exception as e:
                        rows.append({'レースID':rid, '状態':'取得不可', '200mラップ':'',
                                     '区間数':0, 'レース距離':'', '期待区間数':'', '合計秒':'', '検証':str(e)[:200], '末尾補完':'例外', '表の一部':'',
                                     'ページURL':f'https://db.sp.netkeiba.com/race/{rid}/'})
                    progress_lap.progress(i / len(sample_ids))
                st.session_state['lap_table_v1592_result'] = pd.DataFrame(rows)
            if 'lap_table_v1592_result' in st.session_state:
                lap_result = st.session_state['lap_table_v1592_result']
                st.dataframe(lap_result, hide_index=True, use_container_width=True)
                st.download_button('200mラップ試験結果をCSVで保存',
                                   lap_result.to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_lap_table_test_v1594.csv', mime='text/csv',
                                   use_container_width=True)

        with st.expander('200mラップ展開タイプ分析（Ver.16.5・全レース対応）', expanded=False):
            st.caption('20・30レースの試験、または保存済みの全過去成績を対象にできます。全件は重複レースを1回だけ取得し、5件ごとに途中保存。結果表は現在の分析条件で絞った馬を比較します。')
            sample = pace_compare_base.copy()
            # Ver.16.6.1: 表示用の過去成績にも照合用レースIDを設定する。
            # 以前は取得対象のコピーにだけ _rid16 を追加していたため、
            # 展開タイプ分布は表示されても全馬比較表が表示されなかった。
            if 'レースID' in sample.columns:
                sample['_rid16'] = (sample['レースID'].fillna('').astype(str)
                                    .str.strip().str.replace(r'\.0$', '', regex=True))
            full_sample16 = analysis_df.copy()
            test_size = tap_choice('取得するレース数', [20, 30, '全レース'], 'lap_shape_v163_n')
            all_mode16 = test_size == '全レース'
            acquisition_sample16 = full_sample16 if all_mode16 else sample
            if 'レースID' in acquisition_sample16.columns:
                acquisition_sample16['_rid16'] = acquisition_sample16['レースID'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
                candidates = acquisition_sample16[acquisition_sample16['_rid16'].str.fullmatch(r'\d{12}')].drop_duplicates('_rid16').copy()
            else:
                candidates = pd.DataFrame()
            race_ids_16 = candidates['_rid16'].tolist() if all_mode16 and not candidates.empty else (candidates['_rid16'].head(test_size).tolist() if not candidates.empty else [])
            # 試験モードのみ、1600mを追加確認する（全件モードは全レースそのまま）。
            if not all_mode16 and '距離_m' in candidates.columns and race_ids_16:
                d1600 = candidates.loc[pd.to_numeric(candidates['距離_m'], errors='coerce').eq(1600), '_rid16'].tolist()
                if d1600 and not any(r in d1600 for r in race_ids_16):
                    race_ids_16[-1] = d1600[0]
            race_key16 = str(st.session_state.get('auto_race_id', ''))
            stored16 = load_lap_full_progress(race_key16) if all_mode16 else pd.DataFrame()
            if all_mode16 and not stored16.empty:
                stored16['レースID'] = stored16['レースID'].astype(str).str.replace(r'\.0$', '', regex=True)
                stored16 = stored16.drop_duplicates('レースID', keep='last')
                stored16 = stored16[stored16['レースID'].isin(race_ids_16)].copy()
            successful16 = set(stored16.loc[stored16.get('検証', pd.Series('', index=stored16.index)).eq('OK'), 'レースID']) if not stored16.empty else set()
            if all_mode16:
                pending_ids16 = [rid for rid in race_ids_16 if rid not in successful16]
                st.write(f'**対象：{len(race_ids_16)}レース ／ 検証OK：{len(successful16)} ／ 未取得・要再試行：{len(pending_ids16)}**')
                st.caption('「未取得・要再試行」には特殊距離・海外レースなどの取得不可も含みます。再試行を繰り返しても取得できないレースは、下のCSVで原因を確認してください。')
            else:
                pending_ids16 = race_ids_16
                st.caption(f'候補{len(candidates)}レース ／ 今回の試験対象{len(race_ids_16)}レース')
            start16 = st.button('全レースの未取得分をまとめて取得・再開' if all_mode16 else '展開タイプを20〜30レース試験判定する',
                                key='classify_lap_v165_button', disabled=not pending_ids16, use_container_width=True)
            if start16:
                # 過去の成功分は残す。失敗分は今回の試行で上書きする。
                rows16 = stored16.to_dict('records') if all_mode16 else []
                merged16 = {str(r['レースID']):r for r in rows16} if all_mode16 else {}
                progress16 = st.progress(0)
                for j, rid16 in enumerate(pending_ids16, 1):
                    try:
                        payload16 = load_lap_v16(rid16)
                        cache_hit = bool(payload16 is not None and payload16.get('検証') == 'OK')
                        if not cache_hit:
                            payload16 = get_lap_table_test_v1594(rid16)
                            if payload16.get('検証') == 'OK':
                                save_lap_v16(rid16, payload16)
                        extra16 = classify_lap_shape_v16(payload16.get('200mラップ', ''),
                                                          payload16.get('レース距離'), payload16.get('検証'))
                        title16, day16 = get_lap_race_title(rid16, acquisition_sample16)
                        row16 = {'レースID': rid16, 'レース名': title16, '開催日': day16,
                                 'レース距離': payload16.get('レース距離'),
                                 '200mラップ': payload16.get('200mラップ'),
                                 '検証': payload16.get('検証'),
                                 '取得方法': '保存済み' if cache_hit else '新規取得', **extra16}
                    except Exception as ex16:
                        row16 = {'レースID':rid16, 'レース名':'取得エラー', '開催日':'',
                                 '検証':'取得エラー', '取得方法':'失敗',
                                 '展開タイプ':'判定不可', '判定根拠':str(ex16)[:180]}
                    merged16[str(rid16)] = row16
                    progress16.progress(j / len(pending_ids16))
                    if j % 5 == 0 or j == len(pending_ids16):
                        current16 = pd.DataFrame(list(merged16.values()))
                        st.session_state['lap_shapes_v163'] = current16
                        st.session_state['lap_shapes_v163_race'] = race_key16
                        st.session_state['lap_shapes_v163_n'] = test_size
                        if all_mode16:
                            save_lap_full_progress(race_key16, current16)
                st.success(f'{len(pending_ids16)}レースを処理しました。取得不可は未確認として保持します。')
            if all_mode16:
                # 別操作による再実行後も、ディスクに保存済みの全件結果を表示。
                shapes_current16 = load_lap_full_progress(race_key16)
                if not shapes_current16.empty:
                    shapes_current16['レースID'] = shapes_current16['レースID'].astype(str).str.replace(r'\.0$', '', regex=True)
                    shapes_current16 = shapes_current16[shapes_current16['レースID'].isin(race_ids_16)].copy()
                    st.session_state['lap_shapes_v163'] = shapes_current16
                    st.session_state['lap_shapes_v163_race'] = race_key16
                    st.session_state['lap_shapes_v163_n'] = test_size
            shapes16 = st.session_state.get('lap_shapes_v163')
            if isinstance(shapes16, pd.DataFrame) and not shapes16.empty and st.session_state.get('lap_shapes_v163_race') == race_key16:
                st.write('**レース単位の検証結果**')
                st.dataframe(shapes16, hide_index=True, use_container_width=True)
                st.download_button('展開タイプ検証結果をCSVで保存', shapes16.to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_lap_shape_v165.csv', mime='text/csv', use_container_width=True)
                count16 = (shapes16['展開タイプ'].fillna('判定不可').value_counts()
                           .rename_axis('展開タイプ').reset_index(name='レース数'))
                count16['割合(%)'] = (100 * count16['レース数'] / len(shapes16)).round(1)
                st.write('**展開タイプの分布（レース単位）**')
                st.dataframe(count16, hide_index=True, use_container_width=True)
                st.download_button('展開タイプ分布をCSVで保存', count16.to_csv(index=False).encode('utf-8-sig'),
                                   file_name='keiba_lap_shape_distribution_v163.csv', mime='text/csv', use_container_width=True)
                if 'レース距離' in shapes16.columns:
                    distance16 = shapes16[['レース距離', '展開タイプ']].copy()
                    distance16['レース距離'] = pd.to_numeric(distance16['レース距離'], errors='coerce')
                    distance16['距離帯'] = pd.cut(distance16['レース距離'],
                                             bins=[0, 1600, 1800, 2200, 2400, 10000],
                                             labels=['1600m以下', '1601〜1800m', '1801〜2200m', '2201〜2400m', '2401m以上'])
                    bydistance16 = (distance16.groupby(['距離帯', '展開タイプ'], observed=True)
                                     .size().rename('レース数').reset_index())
                    st.write('**距離別の判定分布**')
                    st.dataframe(bydistance16, hide_index=True, use_container_width=True)
                    st.download_button('距離別の判定分布をCSVで保存',
                                       bydistance16.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='keiba_lap_shape_distance_v163.csv', mime='text/csv', use_container_width=True)
                valid16 = shapes16.loc[shapes16['検証'].eq('OK') &
                                       shapes16['展開タイプ'].ne('判定不可'),
                                       ['レースID', '展開タイプ']].drop_duplicates('レースID')
                # 表が出ないときに理由が分かるよう、照合数も明示する。
                if '_rid16' not in sample.columns:
                    st.warning('展開別・全馬比較：過去成績にレースID列がありません。過去成績を再取得してください。')
                elif valid16.empty:
                    st.warning('展開別・全馬比較：検証OKのラップがありません。ラップ取得結果を確認してください。')
                if not valid16.empty and '_rid16' in sample.columns:
                    linked16 = sample.merge(valid16, left_on='_rid16', right_on='レースID',
                                            how='inner', suffixes=('', '_分類'))
                    if linked16.empty:
                        st.warning(f'展開別・全馬比較：レースIDの照合が0件です（現在の分析条件：{len(sample)}走／ラップ検証OK：{len(valid16)}レース）。フィルター条件またはレースIDを確認してください。')
                    if not linked16.empty:
                        st.caption(f'展開別・全馬比較の照合結果：{len(linked16)}走（重複除去前）／{linked16["馬名"].nunique()}頭')
                        # 同一馬・同一レースの重複行は1走として数える。
                        linked16 = linked16.drop_duplicates(['馬名', '_rid16']).copy()
                        for field in ['着順_数値', '上がり順位', '4角順位']:
                            if field not in linked16:
                                linked16[field] = float('nan')
                            linked16[field] = pd.to_numeric(linked16[field], errors='coerce')
                        linked16['勝利'] = linked16['着順_数値'].eq(1).astype(int)
                        linked16['馬券内'] = linked16['着順_数値'].between(1, 3).astype(int)
                        overview16 = (linked16.groupby(['馬名', '展開タイプ'], dropna=False)
                                      .agg(対象走数=('馬券内', 'size'), 勝利数=('勝利', 'sum'),
                                           馬券内数=('馬券内', 'sum'), 平均着順=('着順_数値', 'mean'),
                                           **{'4角取得走数': ('4角順位', 'count'), '平均4角': ('4角順位', 'mean')},
                                           上がり順位取得走数=('上がり順位', 'count'),
                                           平均上がり順位=('上がり順位', 'mean')).reset_index())
                        overview16['勝率(%)'] = (100 * overview16['勝利数'] / overview16['対象走数']).round(1)
                        overview16['複勝率(%)'] = (100 * overview16['馬券内数'] / overview16['対象走数']).round(1)
                        for field in ['平均着順', '平均4角', '平均上がり順位']:
                            overview16[field] = overview16[field].round(2)
                        overview16['データ量'] = overview16['対象走数'].map(
                            lambda n: '参考（1〜2走）' if n < 3 else ('少数（3〜4走）' if n < 5 else '5走以上'))
                        # Ver.16.6：中心は「馬名 + 3展開」のコンパクトな横並び表。
                        type_order166 = ['瞬発戦寄り', 'ロングスパート戦寄り', '消耗戦寄り', '混合・判定保留']
                        type_labels166 = {'瞬発戦寄り': '瞬発戦', 'ロングスパート戦寄り': 'ロンスパ',
                                          '消耗戦寄り': '消耗戦', '混合・判定保留': '保留'}
                        # 分母は現在の分析条件に入る「馬×レース」の重複を除いた走数。
                        candidates166 = sample.loc[
                            sample['_rid16'].astype(str).str.fullmatch(r'\d{12}', na=False)
                        ].drop_duplicates(['馬名', '_rid16']).copy()
                        denominator166 = candidates166.groupby('馬名').size().to_dict()
                        by_horse_type166 = {(str(r['馬名']), str(r['展開タイプ'])): r
                                            for _, r in overview16.iterrows()}
                        ordered_horses166 = list(dict.fromkeys(horses.values())) if horses else []
                        present_horses166 = set(sample['馬名'].dropna().astype(str))
                        ordered_horses166 = [h for h in ordered_horses166 if h in present_horses166]
                        ordered_horses166.extend(sorted(present_horses166.difference(ordered_horses166)))
                        wide_rows166 = []
                        for horse166 in ordered_horses166:
                            row166 = {'馬名': horse166}
                            covered166 = 0
                            for typ166 in type_order166:
                                label166 = type_labels166[typ166]
                                hit166 = by_horse_type166.get((horse166, typ166))
                                if hit166 is None:
                                    row166[label166] = '—'
                                    row166[label166 + '_走数'] = 0
                                else:
                                    n166 = int(hit166['対象走数'])
                                    rate166 = float(hit166['複勝率(%)'])
                                    row166[label166] = f'{rate166:g}%（{n166}走）'
                                    row166[label166 + '_走数'] = n166
                                    covered166 += n166
                            total166 = int(denominator166.get(horse166, 0))
                            row166['分類済み'] = covered166
                            row166['対象候補'] = total166
                            row166['未確認'] = max(0, total166 - covered166)
                            row166['確認率'] = f'{100*covered166/total166:.0f}%' if total166 else '—'
                            wide_rows166.append(row166)
                        wide166 = pd.DataFrame(wide_rows166)
                        if not wide166.empty:
                            wide166 = display_horse_column(wide166, horses, st.session_state.auto_numbers)
                            st.subheader('展開タイプ別・全馬比較（Ver.16.6）')
                            st.caption('複勝率（該当走数）で表示。— はデータなし、0%は該当レースで3着以内なし。1〜2走は参考値です。')
                            main_cols166 = ['馬名', '瞬発戦', 'ロンスパ', '消耗戦']
                            st.dataframe(wide166[main_cols166], hide_index=True, use_container_width=True,
                                         height=min(690, 45 + 36 * len(wide166)),
                                         column_config={'馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                        '瞬発戦': st.column_config.TextColumn('瞬発戦 複勝率（走数）', width='medium'),
                                                        'ロンスパ': st.column_config.TextColumn('ロンスパ 複勝率（走数）', width='medium'),
                                                        '消耗戦': st.column_config.TextColumn('消耗戦 複勝率（走数）', width='medium')})
                            all_cols166 = ['馬名', '瞬発戦', 'ロンスパ', '消耗戦', '保留', '分類済み',
                                           '対象候補', '未確認', '確認率']
                            st.download_button('展開別・全馬比較をCSVで保存',
                                               wide166[all_cols166].to_csv(index=False).encode('utf-8-sig'),
                                               file_name='keiba_lap_horse_wide_v166.csv',
                                               mime='text/csv', use_container_width=True)
                            # Ver.16.7: 分類保留のレースでも数値を活用する比較表。
                            # いずれも各馬が記録した個別ラップではなく、出走したレース全体のラップ。
                            metric_cols167 = ['終盤加速幅_秒', '残1000m相対加速_秒', '残1000mラップ幅_秒']
                            shape_metrics167 = shapes16.copy()
                            shape_metrics167['レースID'] = (shape_metrics167['レースID'].fillna('').astype(str)
                                                               .str.strip().str.replace(r'\.0$', '', regex=True))
                            shape_metrics167 = shape_metrics167.loc[
                                shape_metrics167['検証'].eq('OK')].drop_duplicates('レースID').copy()
                            for metric167 in metric_cols167:
                                if metric167 not in shape_metrics167.columns:
                                    shape_metrics167[metric167] = float('nan')
                                shape_metrics167[metric167] = pd.to_numeric(shape_metrics167[metric167], errors='coerce')
                            # 分類用ラップを取得できたものだけ対象にする（全件解析結果から再計算不要）。
                            numeric_source167 = sample.merge(
                                shape_metrics167[['レースID'] + metric_cols167],
                                left_on='_rid16', right_on='レースID', how='inner')
                            numeric_source167 = numeric_source167.drop_duplicates(['馬名', '_rid16']).copy()
                            numeric_source167['着順_数値'] = pd.to_numeric(
                                numeric_source167['着順_数値'], errors='coerce')
                            numeric_source167['馬券内167'] = numeric_source167['着順_数値'].between(1, 3)
                            numeric_rows167 = []
                            for horse167 in ordered_horses166:
                                horse_df167 = numeric_source167.loc[numeric_source167['馬名'].eq(horse167)]
                                good_df167 = horse_df167.loc[horse_df167['馬券内167']]
                                row167 = {'馬名': horse167,
                                          'ラップ確認走数': len(horse_df167),
                                          '好走走数': len(good_df167)}
                                for source_col167, short_col167 in [
                                    ('終盤加速幅_秒', '好走時_終盤加速秒'),
                                    ('残1000m相対加速_秒', '好走時_残1000m加速秒'),
                                    ('残1000mラップ幅_秒', '好走時_残1000mラップ幅秒')]:
                                    series167 = good_df167[source_col167].dropna()
                                    row167[short_col167] = round(float(series167.mean()), 2) if not series167.empty else float('nan')
                                    row167[short_col167 + '_対象走数'] = int(len(series167))
                                numeric_rows167.append(row167)
                            numeric_wide167 = pd.DataFrame(numeric_rows167)
                            if not numeric_wide167.empty:
                                numeric_wide167 = display_horse_column(
                                    numeric_wide167, horses, st.session_state.auto_numbers)
                                st.subheader('加速・持続ラップの比較（Ver.16.7）')
                                st.caption('**3着以内に好走したレースの平均値**。馬自身の個別ラップ・能力値ではなく、経験したレース全体のラップ構成です。分類保留のレースも含めます。')
                                st.caption('終盤加速：直前600mよりラスト600mが何秒/200m速いか。残1000m加速：直前基準より残1000m平均が何秒速いか。ラップ幅：残1000mの最速と最遅の差（小さいほど一定）。')
                                display_cols167 = ['馬名', '好走時_終盤加速秒',
                                                   '好走時_残1000m加速秒', '好走時_残1000mラップ幅秒',
                                                   '好走走数', 'ラップ確認走数']
                                display167 = numeric_wide167[display_cols167].copy()
                                st.dataframe(display167, hide_index=True, use_container_width=True,
                                             height=min(690, 45 + 36 * len(display167)),
                                             column_config={
                                                 '馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                 '好走時_終盤加速秒': st.column_config.NumberColumn('終盤加速', format='%.2f'),
                                                 '好走時_残1000m加速秒': st.column_config.NumberColumn('残1000m加速', format='%.2f'),
                                                 '好走時_残1000mラップ幅秒': st.column_config.NumberColumn('残1000mラップ幅', format='%.2f'),
                                                 '好走走数': st.column_config.NumberColumn('好走数', format='%d走'),
                                                 'ラップ確認走数': st.column_config.NumberColumn('確認数', format='%d走')
                                             })
                                st.download_button('加速・持続ラップ比較をCSVで保存',
                                                   numeric_wide167.to_csv(index=False).encode('utf-8-sig'),
                                                   file_name='keiba_lap_numeric_v167.csv',
                                                   mime='text/csv', use_container_width=True)
                                with st.expander('数値の根拠になったレースを確認（Ver.16.7.1）', expanded=False):
                                    reason167 = numeric_source167.loc[numeric_source167['馬券内167']].copy()
                                    if not reason167.empty:
                                        # sampleにもレースIDが存在する場合、merge後はレースID_x / レースID_y
                                        # になり得る。常に正規化済みの_rid16を照合キーに使う。
                                        info167 = shapes16[['レースID'] + [
                                            c for c in ['レース名', '開催日', '展開タイプ'] if c in shapes16.columns
                                        ]].drop_duplicates('レースID').copy()
                                        info167['_rid16'] = (info167['レースID'].fillna('').astype(str)
                                            .str.strip().str.replace(r'\.0$', '', regex=True))
                                        info167 = info167.drop(columns=['レースID']).drop_duplicates('_rid16')
                                        # 同名の既存列は混在させず、ラップ集計側を表示する。
                                        reason167 = reason167.drop(columns=[
                                            c for c in ['レース名', '開催日', '展開タイプ'] if c in reason167.columns
                                        ])
                                        reason167 = reason167.merge(info167, on='_rid16', how='left')
                                        reason167 = display_horse_column(reason167, horses, st.session_state.auto_numbers)
                                        for col167 in ['レース名', '開催日', '展開タイプ']:
                                            if col167 not in reason167.columns:
                                                reason167[col167] = ''
                                        detail_cols167 = ['馬名', 'レース名', '開催日', '着順_数値', '展開タイプ'] + metric_cols167
                                        st.dataframe(reason167[detail_cols167], hide_index=True,
                                                     use_container_width=True)
                                    else:
                                        st.info('この条件で3着以内のラップ確認レースはありません。')
                            # Ver.16.8: 3着以内と4着以下のレース全体ラップを比較。
                            # 取消・中止・失格など着順が数値でないレースは比較から除外する。
                            st.subheader('好走時 vs 凡走時：ラップ比較（Ver.16.8）')
                            st.caption('3着以内＝好走、4着以下＝凡走。表示値は**各馬が出走したレース全体**のラップ平均であり、馬自身の個別ラップではありません。差＝好走時 − 凡走時。')
                            valid168 = numeric_source167.loc[
                                numeric_source167['着順_数値'].ge(1)].copy()
                            valid168['結果区分168'] = valid168['着順_数値'].apply(
                                lambda x: '好走' if x <= 3 else '凡走')
                            pairs168 = [
                                ('終盤加速幅_秒', '終盤加速'),
                                ('残1000m相対加速_秒', '残1000m加速'),
                                ('残1000mラップ幅_秒', '残1000mラップ幅'),
                            ]
                            rows168 = []
                            for horse168 in ordered_horses166:
                                subset168 = valid168.loc[valid168['馬名'].eq(horse168)]
                                row168 = {'馬名': horse168,
                                          '好走走数': int(subset168['結果区分168'].eq('好走').sum()),
                                          '凡走走数': int(subset168['結果区分168'].eq('凡走').sum())}
                                for original168, label168 in pairs168:
                                    good168 = pd.to_numeric(subset168.loc[
                                        subset168['結果区分168'].eq('好走'), original168],
                                        errors='coerce').dropna()
                                    bad168 = pd.to_numeric(subset168.loc[
                                        subset168['結果区分168'].eq('凡走'), original168],
                                        errors='coerce').dropna()
                                    row168[label168 + '_好走走数'] = int(len(good168))
                                    row168[label168 + '_凡走走数'] = int(len(bad168))
                                    row168[label168 + '_好走'] = round(float(good168.mean()), 2) if len(good168) else float('nan')
                                    row168[label168 + '_凡走'] = round(float(bad168.mean()), 2) if len(bad168) else float('nan')
                                    row168[label168 + '_差'] = (round(float(good168.mean() - bad168.mean()), 2)
                                                                if len(good168) and len(bad168) else float('nan'))
                                rows168.append(row168)
                            comparison168 = pd.DataFrame(rows168)
                            if not comparison168.empty:
                                comparison168 = display_horse_column(
                                    comparison168, horses, st.session_state.auto_numbers)
                                def show_sec168(x):
                                    return f'{float(x):+.2f}' if pd.notna(x) else '—'
                                main168 = comparison168[['馬名', '終盤加速_好走', '終盤加速_凡走',
                                                          '終盤加速_差', '好走走数', '凡走走数']].copy()
                                for c168 in ['終盤加速_好走', '終盤加速_凡走', '終盤加速_差']:
                                    main168[c168] = main168[c168].map(show_sec168)
                                main168 = main168.rename(columns={
                                    '終盤加速_好走': '好走時', '終盤加速_凡走': '凡走時',
                                    '終盤加速_差': '差（秒）', '好走走数': '好走', '凡走走数': '凡走'})
                                st.dataframe(main168, hide_index=True, use_container_width=True,
                                             height=min(690, 45 + 36 * len(main168)))
                                st.caption('上の表は**終盤加速**（秒/200m）の比較。— はデータなし。差がプラスでも「その展開が得意」と断定できず、1〜2走の数字は参考値です。')
                                with st.expander('残1000m加速・持続性・指標別の対象走数を見る', expanded=False):
                                    extra168 = ['馬名', '好走走数', '凡走走数']
                                    for _, name168 in pairs168:
                                        extra168.extend([name168 + '_好走', name168 + '_凡走',
                                                         name168 + '_差', name168 + '_好走走数',
                                                         name168 + '_凡走走数'])
                                    st.dataframe(comparison168[extra168], hide_index=True,
                                                 use_container_width=True)
                                    st.caption('残1000mラップ幅は小さいほど一定。ただし差の正負だけで良し悪しは判断できません。')
                                st.subheader('どんな展開で好走しやすい？（Ver.17.0・自動解説）')
                                st.caption('好走・凡走の**レース全体のラップ差**から特徴を自動要約。馬自身のラップや能力値ではありません。分類基準は試験用で、統計的な有意差を示すものではありません。')
                                interpretations169 = []
                                for _, row169 in comparison168.iterrows():
                                    label169, confidence169, reason169 = interpret_running_pattern(row169)
                                    interpretations169.append({'馬名': row169['馬名'],
                                        '好走する展開の特徴': label169,
                                        '判定の確かさ': confidence169,
                                        '好走走数': int(row169['好走走数']),
                                        '凡走走数': int(row169['凡走走数']),
                                        '判定理由': reason169})
                                interpretation_df169 = pd.DataFrame(interpretations169)
                                st.dataframe(interpretation_df169[['馬名', '好走する展開の特徴', '判定の確かさ', '好走走数', '凡走走数']],
                                             hide_index=True, use_container_width=True,
                                             height=min(690, 45 + 36 * len(interpretation_df169)))
                                with st.expander('なぜこの判定になった？（馬ごとの説明）', expanded=False):
                                    selected169 = tap_choice('確認する馬', interpretation_df169['馬名'].tolist(), 'interpret_horse169')
                                    rec169 = interpretation_df169[interpretation_df169['馬名'].eq(selected169)].iloc[0]
                                    st.markdown(f"**{rec169['好走する展開の特徴']}**　／　{rec169['判定の確かさ']}")
                                    st.write(rec169['判定理由'])
                                    st.caption('好走時と凡走時の比較で、各指標の差が0.25秒以上（ラップ幅は0.30秒以上）の場合に特徴として表示。閾値は暫定です。')
                                st.download_button('好走展開の自動解説をCSVで保存',
                                                   interpretation_df169.to_csv(index=False).encode('utf-8-sig'),
                                                   file_name='keiba_running_pattern_v169.csv',
                                                   mime='text/csv', use_container_width=True)
                                # Ver.17: 馬自身の位置取り・上がり順位と、レース全体のラップを統合表示。
                                st.subheader('脚質 × 末脚 × 展開：総合診断（Ver.17）')
                                st.caption('位置・上がりは**馬自身の成績**、展開は**レース全体のラップ**。診断は暫定的な説明で、能力点や未来の勝率ではありません。')
                                explanation_map17 = dict(zip(
                                    interpretation_df169['馬名'].astype(str),
                                    interpretation_df169['好走する展開の特徴'].astype(str)))
                                diagnoses17 = []
                                for h17 in ordered_horses166:
                                    hdata17 = numeric_source167.loc[numeric_source167['馬名'].eq(h17)].copy()
                                    diag17 = diagnose_horse_v17(hdata17, explanation_map17.get(h17, '不明'))
                                    diag17['馬名'] = h17
                                    diagnoses17.append(diag17)
                                diagnosis_df17 = pd.DataFrame(diagnoses17)
                                if not diagnosis_df17.empty:
                                    diagnosis_df17 = display_horse_column(diagnosis_df17, horses, st.session_state.auto_numbers)
                                    st.dataframe(diagnosis_df17[['馬名', '走りの総合診断', '展開', '信頼度', '好走走数']],
                                                 hide_index=True, use_container_width=True,
                                                 height=min(690, 45 + 36 * len(diagnosis_df17)),
                                                 column_config={'馬名': st.column_config.TextColumn('馬番＋馬名', width='medium'),
                                                                '走りの総合診断': st.column_config.TextColumn('走りの特徴', width='medium'),
                                                                '展開': st.column_config.TextColumn('好走展開', width='medium')})
                                    with st.expander('総合診断の根拠（馬ごとに確認）', expanded=False):
                                        chosen17 = tap_choice('診断を見る馬', diagnosis_df17['馬名'].tolist(), 'diagnosis_horse_v17')
                                        rec17 = diagnosis_df17.loc[diagnosis_df17['馬名'].eq(chosen17)].iloc[0]
                                        st.markdown(f"**{rec17['走りの総合診断']}**　／　{rec17['信頼度']}")
                                        st.write(rec17['根拠'])
                                        st.caption(f"対象{rec17['対象走数']}走・好走{rec17['好走走数']}走・4角取得{rec17['位置取得']}走・上がり順位取得{rec17['上がり取得']}走")
                                    st.download_button('総合診断をCSVで保存',
                                                       diagnosis_df17.to_csv(index=False).encode('utf-8-sig'),
                                                       file_name='keiba_horse_total_diagnosis_v17.csv',
                                                       mime='text/csv', use_container_width=True)
                                st.download_button('好走・凡走ラップ比較をCSVで保存',
                                                   comparison168.to_csv(index=False).encode('utf-8-sig'),
                                                   file_name='keiba_good_bad_lap_v168.csv',
                                                   mime='text/csv', use_container_width=True)
                            else:
                                st.info('比較対象の着順・ラップがありません。')
                            with st.expander('確認率・保留レース・データ量を見る', expanded=False):
                                st.dataframe(wide166[['馬名','保留','分類済み','対象候補','未確認','確認率']],
                                             hide_index=True, use_container_width=True)
                                st.caption('「分類済み」はラップ検証済み（保留を含む）。「未確認」は対象候補のうち展開分類を確認できなかった走数。確認率は能力の信頼度ではありません。')
                            with st.expander('集計に使ったレース名・着順を見る', expanded=False):
                                race_detail166 = linked16.copy()
                                race_detail166['レースID_照合'] = race_detail166['_rid16'].astype(str)
                                shape_info166_cols = ['レースID'] + [c for c in ['開催日','レース名','レース距離'] if c in shapes16.columns]
                                shape_info166 = shapes16[shape_info166_cols].drop_duplicates('レースID').copy()
                                shape_info166['レースID'] = shape_info166['レースID'].astype(str)
                                race_detail166 = race_detail166.merge(shape_info166, left_on='レースID_照合',
                                                                      right_on='レースID', how='left', suffixes=('', '_ラップ'))
                                # レース名は過去成績の原文を優先し、欠損時だけラップ取得結果を使用。
                                if 'レース名_ラップ' in race_detail166.columns:
                                    name166 = race_detail166['レース名'].fillna('').astype(str).str.strip() if 'レース名' in race_detail166 else pd.Series('',index=race_detail166.index)
                                    race_detail166['レース名'] = name166.where(name166.ne(''), race_detail166['レース名_ラップ'])
                                if 'レース名' not in race_detail166.columns:
                                    race_detail166['レース名'] = '不明'
                                if '日付' not in race_detail166.columns:
                                    race_detail166['日付'] = ''
                                race_detail166['馬名_内部'] = race_detail166['馬名'].astype(str)
                                race_detail166 = display_horse_column(race_detail166, horses, st.session_state.auto_numbers)
                                chosen166 = st.radio('表示する展開タイプ', ['全タイプ', '瞬発戦寄り', 'ロングスパート戦寄り', '消耗戦寄り', '混合・判定保留'],
                                                        horizontal=True, key='shape_detail_type_v166')
                                if chosen166 != '全タイプ':
                                    race_detail166 = race_detail166[race_detail166['展開タイプ'].eq(chosen166)]
                                detailed_cols166 = ['馬名', '日付', 'レース名', '展開タイプ', '着順_数値',
                                                    '4角順位', '上がり順位', 'レースID_照合']
                                detail_out166 = race_detail166[detailed_cols166].rename(columns={'レースID_照合':'レースID'})
                                st.dataframe(detail_out166, hide_index=True, use_container_width=True,
                                             height=min(600, 48 + 34*len(detail_out166)))
                                st.download_button('集計対象レースの詳細をCSVで保存',
                                                   detail_out166.to_csv(index=False).encode('utf-8-sig'),
                                                   file_name='keiba_lap_horse_races_v166.csv', mime='text/csv',
                                                   use_container_width=True)
                            with st.expander('指標別の詳しい成績（勝率・上がり・4角）', expanded=False):
                                detail_metrics166 = display_horse_column(overview16, horses, st.session_state.auto_numbers)
                                st.dataframe(detail_metrics166, hide_index=True, use_container_width=True)
                                st.download_button('展開タイプ別の指標をCSVで保存',
                                                   detail_metrics166.to_csv(index=False).encode('utf-8-sig'),
                                                   file_name='keiba_lap_horse_metrics_v166.csv', mime='text/csv',
                                                   use_container_width=True)
                st.warning('展開タイプは独自の暫定分類です。200mで割り切れない距離や地方・海外レースは取得不可の場合があります。少数走数では適性を断定できません。')

        with tab_quality172:
            with st.expander('② ペース判定の精度チェック（分析条件に連動）', expanded=False):
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
