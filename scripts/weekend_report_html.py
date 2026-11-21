"""週末一括分析の出力から、先週（11/14・15）と同じ形の HTML レポートを作る（通信なし）。

使い方: python scripts/weekend_report_html.py --out-dir <週末出力フォルダ> --work <ラボ取得データ.zip を展開したフォルダ> --save <HTML>
- S/M/H 上位5頭・順位保留・点差は旧S/M/H方式（週末一括分析の出力そのまま）。
- 想定展開は pace-v2（検証済みの暫定の目安）。主予測（全馬共通）と v5（影の予測、結果前に保存した記録）の上位5頭も併記する。
- 「データ充足度」は戦績の揃い具合で、的中しやすさではない。
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

WEEK = '月火水木金土日'
STYLE = ('body{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans JP",sans-serif;background:#f4f7fa;color:#172b3a;'
         'line-height:1.65;margin:0}main{max-width:1100px;margin:auto;padding:18px}h1{font-size:25px}h2{font-size:20px}'
         'h3{font-size:17px}article,.box{background:white;padding:18px;border-radius:12px;margin:18px 0;border:1px solid #dce5eb}'
         'table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;padding:9px;'
         'border-bottom:1px solid #dce5eb}th{background:#eaf1f5}p{margin:10px 0}.scroll{overflow-x:auto}'
         '.muted{color:#526675;font-size:13px}a{color:#096596}nav a{display:inline-block;padding:7px 12px;background:white;'
         'border-radius:6px;margin:3px}.key{font-weight:700;color:#075f69}')
PACE_NAMES = {'S': 'スロー寄り', 'M': 'ミドル寄り', 'H': 'ハイ寄り'}


def e(x):
    return html.escape('' if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))


def num(x):
    try:
        return '' if pd.isna(x) else str(int(float(x)))
    except (TypeError, ValueError):
        return e(x)


def grade(row):
    """データ充足度の表示（旧「分析信頼度」と同じ区分）。"""
    label = str(row.get('データ充足度', ''))
    point = row.get('データ充足度点')
    if pd.notna(point) and label and not label.startswith(('判定不可', '全展開')):
        return f'{e(label)}（{float(point):.1f}/100）'
    return e(label or '—')


def pace_estimates(work):
    """各レースの想定展開（pace-v2）。根拠が足りないレースは保留。"""
    import keiba_pace_v1 as pv
    from keiba_model_data import normalize_history
    from keiba_weekend_batch import HistoryCache
    model = pv.load_v2()
    cache = HistoryCache(Path(work) / 'horses')
    out = {}
    for info_path in sorted((Path(work) / 'entries').glob('*.json')):
        info = json.loads(info_path.read_text(encoding='utf-8'))
        rid = info['レースID']
        if '新馬' in str(info.get('レース名', '')):
            out[rid] = '保留（新馬戦）'
            continue
        entries = pd.read_csv(info_path.with_suffix('.csv'), dtype={'馬ID': str})
        active = entries.loc[entries['状態'].eq('出走予定')]
        raws = [cache.load(h) for h in active['馬ID']]
        raws = [r for r in raws if not r.empty]
        hist = normalize_history(pd.concat(raws, ignore_index=True)) if raws else pd.DataFrame()
        by = {k: g for k, g in hist.groupby('馬ID')} if not hist.empty else {}
        res, positions = pv.predict_pace(active[['馬ID']], by, info['開催日'], pv.load_params())
        if res['独自判定'] is None:
            out[rid] = '保留（位置の根拠不足）'
            continue
        label = res['独自判定']
        if model is not None:
            pressure, front, known, total = pv.race_pressure(positions, pv.PaceParams())
            label = pv.v2_label(model, pressure, front, known, total, float(info['距離']))
        out[rid] = PACE_NAMES[label]
    return out


def shadow_top(work, rid, prefix, n=5):
    folders = sorted(Path(work, 'records', 'predictions').glob(prefix + '*'))
    files = []
    for folder in folders:
        for path in folder.glob(f'{rid}*.csv'):
            rev = int(path.stem.split('_r')[1]) if '_r' in path.stem else 0
            files.append((folder.name, rev, path))
    if not files:
        return None
    files.sort(key=lambda x: (x[0], x[1]))
    t = pd.read_csv(files[-1][2], dtype={'馬ID': str})
    t = t.loc[pd.to_numeric(t['順位'], errors='coerce').notna()].copy()
    t['順位'] = pd.to_numeric(t['順位']).astype(int)
    return t.sort_values('順位').head(n)


def table(rows, cols):
    head = ''.join(f'<th>{c}</th>' for c in cols)
    body = ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in r) + '</tr>\n' for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>\n{body}</tbody></table></div>'


def race_article(race, horses, pace, work, main='old'):
    rid = str(race['レースID'])
    h = horses.loc[horses['レースID'].astype(str).eq(rid)]
    parts = [f"<article id='{rid}'><h3>{e(race['競馬場'])}{num(race['R'])}R {e(race['レース名'])}</h3>",
             f"<p>{e(race['発走'])}発走　{e(race['コース'])}　{num(race['頭数'])}頭</p>",
             f"<p>想定展開：{e(pace.get(rid, '—'))}</p>",
             f"<p>データ充足度：{grade(race)}　戦績確認{num(race['戦績あり'])}/{num(race['頭数'])}頭・平均項目充足"
             f"{float(race['平均項目充足率(%)']):.1f}%。近5走平均有効数{float(race['近5走平均有効数']):.2f}走。</p>"
             if pd.notna(race.get('平均項目充足率(%)')) else f"<p>データ充足度：{grade(race)}</p>"]
    head, old = parts, []
    held, gaps = {}, []
    for p in 'SMH':
        ranked = h.loc[pd.to_numeric(h[f'{p}順位'], errors='coerce').notna()].sort_values(f'{p}順位')
        rows = [(num(r[f'{p}順位']), num(r['馬番']), e(r['馬名']), f"{float(r[f'{p}参考点']):.1f}")
                for _, r in ranked.head(5).iterrows()]
        opened = ' open' if p == 'M' and main == 'old' else ''
        label = f'{p}想定の上位5頭' if main == 'old' else f'旧S/M/H方式：{p}想定の上位5頭'
        old.append(f'<details{opened}><summary>{label}</summary>\n' +
                     (table(rows, ['順位', '馬番', '馬名', '参考点']) if rows else '<p class="muted">順位表示なし</p>') +
                     '\n</details>')
        hold = h.loc[pd.to_numeric(h[f'{p}順位'], errors='coerce').isna() & h['状態'].eq('出走予定')]
        held[p] = hold
        pts = pd.to_numeric(ranked[f'{p}参考点'], errors='coerce').tolist()
        gaps.append((p, f'{pts[0] - pts[1]:.1f}' if len(pts) >= 2 else '—'))
    holds = ' ／ '.join(f"{p}：{'・'.join(num(x) for x in held[p]['馬番']) or 'なし'}" for p in 'SMH')
    old.append(f"<p class='muted'>{e(race.get('比較対象', ''))}比較（相手・格差未補正）。順位保留（馬番） {holds}</p>")
    reasons = []
    for p, gap in gaps:
        detail = ' ／ '.join(f"{num(r['馬番'])} {e(r['馬名'])}：{e(r[f'{p}判定'])}" for _, r in held[p].iterrows()) or '保留なし'
        reasons.append(f'<p>{p}首位と次位の点差：{gap}</p>\n<p class="muted">{detail}</p>')
    old.append('<details><summary>順位保留の理由・点差（旧S/M/H方式）</summary>\n' + '\n'.join(reasons) + '\n</details>')
    common_block, v5_block = [], []
    common = h.loc[pd.to_numeric(h['全馬共通M順位'], errors='coerce').notna()].sort_values('全馬共通M順位').head(5)
    if len(common):
        rows = [(num(r['全馬共通M順位']), num(r['馬番']), e(r['馬名']), f"{float(r['全馬共通M参考点']):.1f}")
                for _, r in common.iterrows()]
        common_block.append('<details><summary>主予測（全馬共通・M想定）の上位5頭</summary>\n' +
                     table(rows, ['順位', '馬番', '馬名', '参考点']) + '\n</details>')
    v5 = shadow_top(work, rid, 'shadow-v5-')
    if v5 is not None and len(v5):
        v5 = v5.assign(馬番=v5['馬名'].map(h.drop_duplicates('馬名').set_index('馬名')['馬番']))  # 最新の出走表の馬番
        rows = [(num(r['順位']), num(r['馬番']), e(r['馬名']), e(r['判定'])) for _, r in v5.iterrows()]
        opened = ' open' if main == 'v5' else ''
        v5_block.append(f'<details{opened}><summary>v5（検証中）の上位5頭</summary>\n' +
                        table(rows, ['順位', '馬番', '馬名', '判定']) +
                        '\n<p class="muted">結果前に保存した予想。過去検証863Rで上位3に勝ち馬56.2%（主予測48.1%）。勝つ確率ではありません。</p></details>')
    elif main == 'v5':
        v5_block.append('<p class="muted">v5の記録なし</p>')
    parts = head + (v5_block + common_block + old if main == 'v5' else old + common_block + v5_block)
    day = str(race['日付']).replace('-', '')
    parts.append(f'<p><a href="#day{day}">この日の一覧へ戻る</a></p></article>')
    return '\n'.join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', nargs='+', required=True, help='週末出力フォルダ（複数可：土日＋月曜など）')
    ap.add_argument('--work', nargs='+', required=True, help='対応する作業フォルダ（--out-dir と同じ順）')
    ap.add_argument('--save', required=True)
    ap.add_argument('--main', choices=('old', 'v5'), default='old', help='各レースで最初に開く表（v5：v5 の上位5頭）')
    args = ap.parse_args(argv)
    if len(args.out_dir) != len(args.work):
        raise SystemExit('--out-dir と --work の数をそろえてください')
    races, horses, tops, pace, metas, work_of = [], [], [], {}, [], {}
    for out_dir, work in zip(args.out_dir, args.work):
        out = Path(out_dir)
        r = pd.read_csv(glob.glob(str(out / '*_全芝レース一覧.csv'))[0], dtype={'レースID': str})
        races.append(r)
        horses.append(pd.read_csv(glob.glob(str(out / '*_全頭展開別順位.csv'))[0], dtype={'レースID': str}))
        tops.append(pd.read_csv(glob.glob(str(out / '*_分析信頼度上位.csv'))[0], dtype={'レースID': str}))
        metas.append(json.loads(Path(glob.glob(str(out / '*_条件と取得状況.json'))[0]).read_text(encoding='utf-8')))
        pace.update(pace_estimates(work))
        work_of.update({rid: work for rid in r['レースID']})
    races = pd.concat(races, ignore_index=True).drop_duplicates('レースID', keep='last')
    horses = pd.concat(horses, ignore_index=True).drop_duplicates(['レースID', '馬名'], keep='last')
    meta = metas[0] | {'作成日時': '・'.join(m.get('作成日時', '') for m in metas)}
    top = (pd.concat(tops, ignore_index=True).assign(_p=lambda d: pd.to_numeric(d['データ充足度点'], errors='coerce'))
           .sort_values(['_p', '近5走平均有効数', 'レースID'], ascending=[False, False, True]).head(3))
    races = races.sort_values(['日付', '発走', '競馬場'])
    days = list(dict.fromkeys(races['日付']))
    dt = [pd.Timestamp(d) for d in days]
    title_days = '・'.join(f'{d.day}日（{WEEK[d.weekday()]}）' for d in dt)
    counts = '・'.join(f"{WEEK[pd.Timestamp(d).weekday()]}{(races['日付'] == d).sum()}" for d in days)
    body = [f'<h1>{dt[0].month}月{title_days} 全芝レース</h1>',
            f'<p>競馬分析ラボの計算結果。{counts}・計{len(races)}レース、{len(horses)}頭。保存データ作成：{e(meta.get("作成日時", ""))} JST</p>',
            '<p class="muted">参考点は勝率ではありません。S=スロー、M=ミドル、H=ハイ。データ充足度は戦績の揃い具合で、的中しやすさではありません。</p>',
            '<div class="box"><details><summary>採点・データ充足度・展開の基準</summary>'
            f'<p>S/M/H別の参考点は旧S/M/H方式（{e(meta.get("参考点", ""))}）。順位を主観で変更していません。順位保留は低評価を意味しません。</p>'
            f'<p>{e(meta.get("信頼度", "")).replace("信頼度", "データ充足度")}</p>'
            '<p>想定展開は pace-v2（未使用だった2026年夏460Rで公表S/M/Hとの一致率53.5%、常に最多の判定を答える場合34.8%）。暫定の目安です。'
            '最初に記録されたコーナーの位置を使い、発走直後の位置とは限りません。</p>'
            '<p>主予測（全馬共通）と v5（影の予測）は別方式なので、S/M/H別の順位と混ぜていません。'
            '過去検証では、上位3頭に勝ち馬が入った割合は旧S/M/H方式 25〜32%・全馬共通 45〜53%・v5 56%（2026年夏・春）。</p></details></div>',
            '<h2>データ充足度の上位</h2>']
    if args.main == 'v5':
        body.insert(3, '<p class="key">各レースの最初の表は v5（新馬戦は騎手点、それ以外は戦績＋近走人気と全馬共通の順位平均）。'
                       '主予測（全馬共通）と旧S/M/H方式は折りたたみで残しています。</p>')
    for i, (_, r) in enumerate(top.iterrows(), 1):
        d = pd.Timestamp(r['日付'])
        if args.main == 'v5':
            v5 = shadow_top(work_of[r['レースID']], r['レースID'], 'shadow-v5-', 3)
            hh = horses.loc[horses['レースID'].eq(r['レースID'])].drop_duplicates('馬名').set_index('馬名')['馬番']
            lines = 'v5：' + (' ／ '.join(f"{num(hh.get(x['馬名']))} {e(x['馬名'])}" for _, x in v5.iterrows()) if v5 is not None else '記録なし')
        else:
          lines = '<br>'.join(f"{p}：" + ' ／ '.join(
            f"{num(x['馬番'])} {e(x['馬名'])}" for _, x in horses.loc[horses['レースID'].eq(r['レースID'])]
            .assign(_r=lambda t, p=p: pd.to_numeric(t[f'{p}順位'], errors='coerce')).dropna(subset=['_r'])
            .sort_values('_r').head(3).iterrows()) for p in 'SMH')
        body.append(f"<article><h3>{i}. {d.month}-{d.day} {e(r['競馬場'])}{num(r['R'])}R {e(r['レース名'])} {e(r['発走'])}</h3>"
                    f"<p class='key'>{grade(r)}</p><p>{lines}</p><p class='muted'>想定展開：{e(pace.get(r['レースID'], '—'))}。"
                    f"{e(r.get('比較対象', ''))}比較（相手・格差未補正）。</p></article>")
    body.append('<nav>' + ''.join(f'<a href="#day{d.replace("-", "")}">{WEEK[pd.Timestamp(d).weekday()]}曜の一覧</a>' for d in days) + '</nav>')
    articles = []
    for d in days:
        day = races.loc[races['日付'].eq(d)]
        ts = pd.Timestamp(d)
        nav = '\n'.join(f"<a href='#{r['レースID']}'>{e(r['競馬場'])}{num(r['R'])}R</a>" for _, r in day.iterrows())
        rows = [(e(r['競馬場']), num(r['R']), e(r['発走']), e(r['レース名']), e(r['コース']),
                 e(pace.get(r['レースID'], '—')), grade(r)) for _, r in day.iterrows()]
        rank = day.assign(_p=pd.to_numeric(day['データ充足度点'], errors='coerce')).sort_values(
            ['_p', '近5走平均有効数', 'レースID'], ascending=[False, False, True], na_position='last')
        ranking = '\n'.join(f"<p>{i}. <a href='#{r['レースID']}'>{e(r['競馬場'])}{num(r['R'])}R {e(r['レース名'])}</a> {grade(r)}</p>"
                            for i, (_, r) in enumerate(rank.iterrows(), 1))
        body.append(f'<h2 id="day{d.replace("-", "")}">{ts.month}-{ts.day} 全{len(day)}レース一覧</h2><nav>\n{nav}\n</nav>' +
                    table(rows, ['開催場', 'R', '発走時刻', 'レース名', '芝距離', '想定展開', 'データ充足度']) +
                    f'<details><summary>この日のデータ充足度ランキング</summary>\n{ranking}\n</details>')
        articles += [race_article(r, horses, pace, work_of[r['レースID']], args.main) for _, r in day.iterrows()]
        body.append(('<h2>v5の上位5頭</h2>\n' if args.main == 'v5' else '<h2>展開別上位5頭</h2>\n') + '\n'.join(articles))
        articles = []
    body.append('<div class="box"><h2>計算方法と確認範囲</h2>'
                f'<p>{e(meta.get("参考点", ""))}</p><p>{e(meta.get("S/M/H", ""))}</p>'
                '<p>戦績は対象日より前の走だけを使用。馬番・騎手は保存した出走表の時点のもの。保存後の変更・取消・馬場・人気は反映していません。</p></div>')
    doc = (f'<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{dt[0].month}月{"・".join(str(d.day) for d in dt)}日 全芝レース・競馬分析ラボ</title><style>{STYLE}</style><main>'
           + '\n'.join(body) + '</main></html>')
    Path(args.save).write_text(doc, encoding='utf-8')
    print(args.save, len(doc))


if __name__ == '__main__':
    main()
