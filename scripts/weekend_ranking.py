"""データ充足度ランキング（HTML・CSV）を週末出力から作る（通信なし）。v5 の上位3頭に v5点を添える。
使い方: python scripts/weekend_ranking.py --out-dir <週末出力> --work <作業フォルダ> --save-prefix <保存先の先頭（拡張子なし）> [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import glob
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import weekend_report_html as w  # noqa: E402

STYLE = ('body{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans JP",sans-serif;background:#f4f7fa;color:#172b3a;margin:0;line-height:1.6}'
         'main{max-width:760px;margin:auto;padding:16px}h1{font-size:22px}ol{list-style:none;padding:0}'
         'li{background:#fff;border:1px solid #dce5eb;border-radius:12px;padding:12px 14px;margin:10px 0}'
         'p{margin:4px 0}.t{font-size:16px}.g{color:#075f69;font-weight:700}.m,.n{color:#526675;font-size:13px}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--save-prefix', required=True)
    ap.add_argument('--date', help='この日のレースだけ（既定：全部）')
    a = ap.parse_args(argv)
    races = pd.read_csv(glob.glob(a.out_dir + '/*全芝レース一覧.csv')[0], dtype={'レースID': str})
    horses = pd.read_csv(glob.glob(a.out_dir + '/*全頭展開別順位.csv')[0], dtype={'レースID': str})
    if a.date:
        races = races.loc[races['日付'].eq(a.date)]
    pace = w.pace_estimates(a.work)
    races = races.assign(_p=pd.to_numeric(races['データ充足度点'], errors='coerce')).sort_values(
        ['_p', '近5走平均有効数', 'レースID'], ascending=[False, False, True], na_position='last')
    rows, items = [], []
    for i, (_, x) in enumerate(races.iterrows(), 1):
        top = w.shadow_top(a.work, x['レースID'], 'shadow-v5-', 3)
        numbers = horses.loc[horses['レースID'].eq(x['レースID'])].drop_duplicates('馬名').set_index('馬名')['馬番']
        picks = [] if top is None else [(w.num(numbers.get(r['馬名'])), r['馬名'], r['v5点']) for _, r in top.iterrows()]
        grade = str(x['データ充足度'])
        grade = '保留' if grade.startswith('全展開') else grade
        point = f"{x['_p']:.1f}" if pd.notna(x['_p']) else '—'
        debut = '新馬' in str(x['レース名'])
        label = f"{x['競馬場']}{w.num(x['R'])}R {x['レース名']}"
        text = ' ／ '.join(f'{n} {name}（{p}）' for n, name, p in picks)
        rows.append({'順位': i, '日付': x['日付'], 'レース': label, '発走': x['発走'], '距離': x['コース'], 'データ充足度': grade,
                     '点数': point, '想定展開': pace.get(x['レースID'], '—'), 'v5上位3頭（v5点）': text + ('（騎手順）' if debut else '')})
        items.append(f"<li><p class='t'><b>{i}. {html.escape(label)}</b> <span class='g'>{grade}（{point}）</span></p>"
                     f"<p class='m'>{x['日付']} {x['発走']}発走・{x['コース']}・想定展開：{html.escape(rows[-1]['想定展開'])}</p>"
                     f"<p>v5{'（騎手順）' if debut else ''}：{html.escape(text)}</p></li>")
    pd.DataFrame(rows).to_csv(a.save_prefix + '.csv', index=False, encoding='utf-8-sig')
    title = f"{a.date or '週末'} 全芝レース データ充足度ランキング"
    doc = (f'<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{html.escape(title)}</title><style>{STYLE}</style><main><h1>{html.escape(title)}</h1>'
           '<p class="n">データ充足度＝戦績データの揃い具合（100点満点）。当たりやすさではありません。v5＝結果前に保存した予想。'
           'v5点はレース内で0〜100に直した能力点（新馬戦は騎手の3着内率）。順位は能力点と全馬共通の順位の平均なので、点数と前後することがあります。</p>'
           f'<ol>{"".join(items)}</ol></main></html>')
    Path(a.save_prefix + '.html').write_text(doc, encoding='utf-8')
    print(a.save_prefix)


if __name__ == '__main__':
    main()
