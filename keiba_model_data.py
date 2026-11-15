"""予測モデル用の戦績正規化（全モデル共通の土台）。

方針
- 原文を残す（*_原文 列）。数値化できない値は欠損のまま。推測で埋めない。
- 芝・ダート・障害を区別する。予測対象は芝だが、ダートの戦績は削除しない（休養・芝復帰の判定に使う）。
- 競走状態を区別する：完走／取消／除外／中止／失格／降着。
  取消・除外は「出走していない」ので、直近走数や窓に数えない。
  中止・失格は「出走した」が着順の比較には使わない（特徴量ごとの扱いは FEATURE_RULES に記載）。
- クラスはJRAの明示表記（G1〜G3、L、オープン、3勝/2勝/1勝（旧1600万/1000万/500万）、未勝利、新馬）から分類する。
  地方・海外・表記なしは「不明」のまま保持する。
- 対象日より後の行は cutoff で必ず落とす（未来情報の遮断）。
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

DATA_VERSION = 'data-v1'
JRA_VENUES = ('札幌', '函館', '福島', '新潟', '東京', '中山', '中京', '京都', '阪神', '小倉')
CLASS_ORDER = ('新馬', '未勝利', '1勝', '2勝', '3勝', 'OP', 'L', 'G3', 'G2', 'G1')
STATUS_RAN = ('完走', '中止', '失格', '降着')
FEATURE_RULES = {
    '直近走数・窓': '取消・除外は数えない。中止・失格は1走として数える（出走はしている）。',
    '着順の比較（頭数補正・3着内）': '完走・降着のみ。中止・失格は比較から外す（着順がない／確定順位が別）。',
    '着差': '完走のみ。数値でない着差は欠損。',
    '休養日数': '取消・除外を除いた最終出走日から数える。',
}


GOING = {'良': '良', '稍': '稍重', '稍重': '稍重', '重': '重', '不': '不良', '不良': '不良'}


def going_key(text):
    """馬場表記をそろえる（戦績の「稍」「不」と結果ページの「稍重」「不良」）。分からなければ None。"""
    return GOING.get(zen2han(text).strip()) if text is not None else None


def zen2han(text):
    return unicodedata.normalize('NFKC', str(text)) if text is not None else ''


def classify_class(name='', condition='', grade=''):
    """明示表記からクラスを返す。分からなければ None。"""
    grade = zen2han(grade).upper().replace('Ｇ', 'G')
    text = zen2han(f'{name} {condition}')
    for g, pattern in (('G1', r'\(G(?:I|1)\)|\bG1\b|ＧⅠ'), ('G2', r'\(G(?:II|2)\)|\bG2\b'), ('G3', r'\(G(?:III|3)\)|\bG3\b')):
        if grade == g or re.search(pattern, text):
            return g
    if re.search(r'\(L\)|リステッド', text):
        return 'L'
    for label, pattern in (('3勝', r'3勝|1600万'), ('2勝', r'2勝|1000万'), ('1勝', r'1勝|500万'),
                           ('未勝利', r'未勝利'), ('新馬', r'新馬'), ('OP', r'\(OP\)|オープン')):
        if re.search(pattern, text):
            return label
    return None


def finish_status(raw):
    """着順原文 → (状態, 数値着順)。"""
    text = zen2han(raw).strip()
    if text in ('', 'nan', 'None'):
        return '不明', None
    if re.fullmatch(r'\d+', text):
        return '完走', int(text)
    if '降' in text:
        num = re.search(r'\d+', text)
        return '降着', int(num.group()) if num else None
    for key, label in (('取', '取消'), ('除', '除外'), ('中', '中止'), ('失', '失格')):
        if text.startswith(key):
            return label, None
    return '不明', None


def _num(series):
    return pd.to_numeric(series.astype(str).map(zen2han).str.replace(',', '').str.strip(), errors='coerce')


def normalize_history(raw):
    """get_results の取得表（1頭分または複数頭）を共通形式へ。原文列を残す。"""
    if raw is None or raw.empty:
        return pd.DataFrame()
    d = pd.DataFrame(index=raw.index)
    col = lambda *names: next((raw[n] for n in names if n in raw), pd.Series(pd.NA, index=raw.index))
    d['馬ID'] = col('馬ID').astype(str)
    d['レースID'] = col('レースID').astype(str).str.replace(r'\.0$', '', regex=True)
    d['日付'] = pd.to_datetime(col('日付').astype(str).str.replace('/', '-'), errors='coerce')
    venue_raw = col('開催').astype(str).map(zen2han)
    d['開催_原文'] = venue_raw
    d['競馬場'] = venue_raw.str.replace(r'\d', '', regex=True).str.strip()
    d['JRA'] = d['競馬場'].isin(JRA_VENUES)
    dist_raw = col('距離').astype(str).map(zen2han)
    d['距離_原文'] = dist_raw
    surface = dist_raw.str.extract(r'(芝|ダ|障)')[0]
    d['コース'] = surface.map({'芝': '芝', 'ダ': 'ダ', '障': '障'}).fillna('不明')
    d['距離'] = pd.to_numeric(dist_raw.str.extract(r'(\d{3,4})')[0], errors='coerce')
    d['馬場'] = col('馬 場', '馬場').astype(str).map(going_key)
    d['頭数'] = _num(col('頭 数', '頭数'))
    d['馬番'] = _num(col('馬 番', '馬番'))
    finish_raw = col('着 順', '着順')
    d['着順_原文'] = finish_raw.astype(str)
    parsed = finish_raw.map(finish_status)
    d['状態'] = parsed.map(lambda x: x[0])
    d['着順'] = pd.to_numeric(parsed.map(lambda x: x[1]), errors='coerce')
    d['着差'] = _num(col('着差')).where(d['状態'].eq('完走'))
    d['上り'] = _num(col('上り', '上がり'))
    d['通過_原文'] = col('通過').astype(str).replace({'nan': ''})
    d['人気'] = _num(col('人 気', '人気'))
    name = col('レース名').astype(str)
    grade = col('レース格').astype(str)
    d['レース名'] = name
    d['クラス'] = [classify_class(n, '', g) if j else None for n, g, j in zip(name, grade, d['JRA'])]
    d['クラス'] = d['クラス'].fillna('不明')
    d['データ版'] = DATA_VERSION
    d = d.loc[d['日付'].notna()]
    ids = d['レースID'].where(d['レースID'].str.fullmatch(r'\d{12}'))
    keyed = d.assign(_k=ids.fillna(d['日付'].astype(str) + d['レース名']))
    return keyed.drop_duplicates(['馬ID', '_k']).drop(columns='_k').sort_values(['馬ID', '日付'], ascending=[True, False])


def before(history, cutoff):
    """対象日（その日を含まない）より前の行だけ。"""
    return history.loc[history['日付'].lt(pd.Timestamp(cutoff))]


def ran(history):
    """実際に出走した行（取消・除外を除く）。"""
    return history.loc[history['状態'].isin(STATUS_RAN)]


def comparable(history):
    """着順を比較できる行（完走・降着、数値着順と頭数あり）。"""
    h = history.loc[history['状態'].isin(('完走', '降着'))]
    return h.loc[h['着順'].ge(1) & h['頭数'].gt(1) & h['着順'].le(h['頭数'])]


def first_corner(passing):
    """通過順の最初の値（最初に記録されたコーナー）。発走直後の位置とは限らない。"""
    hit = re.match(r'\s*(\d+)', zen2han(passing))
    return int(hit.group(1)) if hit else None
