"""nar_local の出力（地方参考順位・信頼度区分・run_log）から、スマホで見る HTML を作る。

信頼度の過去の的中は models/nar_tiers.json（検証で確かめた競馬場だけ「確認済み」）から書く。
点数はレース内の相対点（0〜100）で、確率ではない。
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TIERS = ROOT / 'models' / 'nar_tiers.json'
WEEK = '月火水木金土日'
REF_LABEL = {'nar-v1S': '4場版'}
REF_NOTE = {
    'nar-v1S': ('参考：南関東4場版（検証中）', 'nar-v1S（浦和・大井・川崎・船橋で学習）の順位を並べています。'
                '11/26 以降の浦和で前向きに確かめている途中で、主予測ではありません。'),
    'C3U': ('参考：C3U（検証で不採用）', '浦和だけで学習し、地方の4つと格付けの3つを足した組です。9/1〜11/24 の浦和118レースで'
            '近走人気だけを +9.3pt 上回りましたが、95%区間の下限が 0.0 で採用の条件に届きませんでした。主予測ではありません。'),
}

CSS = """:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--hi:#eef4ff;--bar:#3b6fd8;--a:#1f7a3d;--b:#a86b00;--c:#777}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#eee;--mut:#aaa;--line:#333;--hi:#1e2a40;--bar:#6c9cff;--a:#5fd08a;--b:#f0b44c;--c:#999}}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,sans-serif;margin:0;padding:16px;line-height:1.5}h1{font-size:20px;margin:0 0 4px}h2{font-size:17px}.mut{color:var(--mut);font-size:13px}
.box{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:12px 0;font-size:14px}details{border-bottom:1px solid var(--line);padding:8px 0}summary{cursor:pointer}
table{border-collapse:collapse;font-size:14px;width:100%;margin-top:6px}th,td{border-bottom:1px solid var(--line);padding:5px;text-align:center}td.n{text-align:left;font-weight:600}tr.top{background:var(--hi)}
.tA,.tB,.tC{font-weight:700}.tA{color:var(--a)}.tB{color:var(--b)}.tC{color:var(--c)}
td.pt{position:relative;min-width:70px}td.pt span{position:absolute;left:0;top:25%;height:50%;width:var(--w);background:var(--bar);opacity:.25;border-radius:3px}"""


def pct(x):
    return f'{x * 100:.0f}%'


def tier_note(venue, tiers):
    t = tiers.get(venue)
    if not t:
        return 'この競馬場では未確認（過去のレース単位の検証をまだしていません）。'
    rates = '・'.join(f"{k} {pct(t[k]['的中率'])}（{t[k]['レース数']}R）" for k in ('A', 'B', 'C') if k in t)
    src = f"（{t['方式']}の順位に区分を当てはめた結果）" if t.get('方式') else ''
    head = f"過去の{venue}（{t['期間']}）での5頭ボックス的中{src}：{rates}、全レース {pct(t['全レース']['的中率'])}"
    if t.get('確認済み'):
        return head + '。過去の検証での実績で、当たる確率ではありません。'
    return head + f"。ただし A と全レースの差ははっきりせず（95%区間の下限 {t['下限'] * 100:+.1f}pt）、区分は未確認です。"


def build(out, venue, date, tiers=None):
    out = Path(out)
    tiers = json.loads(TIERS.read_text(encoding='utf-8')) if tiers is None and TIERS.exists() else (tiers or {})
    ranks = pd.read_csv(out / f'地方参考順位_{date}.csv', dtype={'レースID': str, '馬番': str})
    tier = pd.read_csv(out / f'信頼度区分_{date}.csv', dtype={'レースID': str})
    log = json.loads((out / 'run_log.json').read_text(encoding='utf-8'))
    day = pd.Timestamp(date)
    method = ranks['方式'].iloc[0] if '方式' in ranks and len(ranks) else log.get('使う方式', 'nar-local-v1')
    refs = [c[:-2] for c in ranks.columns if c.endswith('順位') and c != '順位']
    tier = tier.sort_values(['信頼度', 'ボックス向き順'])
    auc = log.get('AUC', {})
    d = log.get(f"AUC差（{log.get('使う方式', 'nar-local-v1')[-2:]}−近走人気）", log.get('AUC差（v1−近走人気）', {}))
    p = [f'<!doctype html><html lang=ja><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
         f'<title>{venue} 地方参考順位</title><style>{CSS}</style>',
         f'<h1>{day.month}/{day.day}（{WEEK[day.weekday()]}）{html.escape(venue)} 全{len(tier)}レース 地方参考順位</h1>',
         f'<div class=mut>方式：{html.escape(str(method))}（ラボ計算）</div>',
         '<div class=box><b>検証（地方の走で3着以内を当てる、AUC）</b><br>'
         + '／'.join(f'{html.escape(k)} {v:.3f}' for k, v in auc.items()) + '（0.5＝でたらめ）<br>'
         + (f"使う方式 − 近走人気だけ：{d.get('差', float('nan')):+.3f}（95%区間 {d.get('下限', float('nan')):+.3f}〜{d.get('上限', float('nan')):+.3f}）→ {html.escape(log.get('判定', ''))}" if d else '')
         + '</div>',
         f'<div class=box><b>信頼度について</b><br>{html.escape(tier_note(venue, tiers))}</div>',
         '<h2>信頼度別（上位5頭の馬番）</h2><table><tr><th>信頼度</th><th>R</th><th>発走</th><th>頭数</th><th>上位5頭</th>' + ''.join(f'<th>{html.escape(REF_LABEL.get(n, n))}上位5頭</th>' for n in refs) + '</tr>']
    for _, r in tier.iterrows():
        g = ranks.loc[ranks['レースID'].eq(r['レースID'])].sort_values('順位')
        top = '・'.join(g['馬番'].head(5).astype(str))
        rno = str(r['レース']).split()[0]
        ref_tops = ''.join('<td class=mut>' + '・'.join(g.sort_values(f'{n}順位')['馬番'].head(5).astype(str)) + '</td>' for n in refs)
        p.append(f"<tr><td class=t{r['信頼度']}>{r['信頼度']}</td><td>{html.escape(rno)}</td><td>{html.escape(str(r['発走']))}</td><td>{r['頭数']}</td><td>{top}</td>{ref_tops}</tr>")
    p.append('</table><p class=mut>信頼度：ボックス向きの順（頭数の少ない順、同じなら上位5頭と6位以下の点差の大きい順）で A＝上位3、B＝4〜6、C＝7以下。'
             '地方の戦績が2走未満の馬が2頭以上いれば1段下げる。</p>')
    for n in refs:
        title, note = REF_NOTE.get(n, (f'参考：{n}', '参考として並べた順位で、主予測ではありません。'))
        p.append(f'<div class=box><b>{html.escape(title)}</b><br>{html.escape(note)}</div>')
    p.append('<h2>各レース</h2>')
    for rid, g in ranks.groupby('レースID', sort=False):
        g = g.sort_values('順位')
        t = tier.loc[tier['レースID'].eq(rid), '信頼度']
        t = t.iloc[0] if len(t) else ''
        p.append(f"<details><summary><b class=t{t}>{t}</b> {html.escape(str(g['レース'].iloc[0]))}</summary><table><tr><th>順位</th><th>馬番</th><th>馬名</th><th>騎手</th><th>地方点</th>"
                 + ''.join(f'<th>{html.escape(REF_LABEL.get(n, n))}</th>' for n in refs) + '</tr>')
        for _, h in g.iterrows():
            w = max(0.0, min(100.0, float(h['地方点'])))
            cls = ' class=top' if h['順位'] <= 5 else ''
            s = ''.join(f"<td>{int(h[f'{n}順位'])}</td>" if pd.notna(h.get(f'{n}順位')) else '<td>—</td>' for n in refs)
            p.append(f"<tr{cls}><td>{h['順位']}</td><td>{html.escape(str(h['馬番']))}</td><td class=n>{html.escape(str(h['馬名']))}</td>"
                     f"<td>{html.escape(str(h['騎手']))}</td><td class=pt><span style='--w:{w:.1f}%'></span>{w:.0f}</td>{s}</tr>")
        p.append('</table></details>')
    p.append('<div class=box><b>注意</b><br>・地方点はレース内の相対点（0〜100）で、勝つ確率ではありません。<br>'
             '・当日の馬場・オッズ・馬体重・取消は入っていません。</div></html>')
    path = out / f'{venue}_地方参考順位_{date.replace("-", "")}.html'
    path.write_text(''.join(p), encoding='utf-8')
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='nar_local の出力フォルダ')
    ap.add_argument('--venue', required=True)
    ap.add_argument('--date', required=True)
    a = ap.parse_args(argv)
    print(build(a.out, a.venue, a.date))


if __name__ == '__main__':
    main()
