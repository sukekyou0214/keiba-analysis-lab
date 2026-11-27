"""11/28〜30 の JRA 芝を1ファイルに。区分は models/box.json（box-v1）をそのまま使う。順位は保存済みの記録のまま。"""
import sys, json, html
from pathlib import Path
import pandas as pd
repo, out = Path(sys.argv[1]), sys.argv[2]
dirs = sys.argv[3:]
sys.path.insert(0, str(repo))
import keiba_box as kb
spec = json.loads((repo / 'models/box.json').read_text(encoding='utf-8'))
races = pd.concat([pd.read_csv(next(Path(d).glob('*_全芝レース一覧.csv'))) for d in dirs])
horses = pd.concat([pd.read_csv(next(Path(d).glob('*_全頭展開別順位.csv'))) for d in dirs])
v5dir = repo / 'records/predictions/shadow-v5-37e9151ede'
e = lambda s: html.escape(str(s))
rows = []
for _, r in races.iterrows():
    rid = r['レースID']; f = v5dir / f'{rid}.csv'
    t = pd.read_csv(f).sort_values('順位') if f.exists() else None
    ok = t is not None and len(t.dropna(subset=['順位'])) >= 5 and not str(t['判定'].iloc[0]).startswith('騎手点')
    rows.append({'日付': r['日付'], 'レースID': rid, **(kb.race_values(t) if ok else {'頭数': r['頭数'], 'B2': None, 'B3': None}), '_ok': ok})
d = pd.DataFrame(rows)
o = kb.order(d[d['_ok']].drop(columns='_ok'), spec['選択'])
tier = {x['レースID']: ('A' if x['ボックス向き順'] <= 3 else 'B' if x['ボックス向き順'] <= 6 else 'C', int(x['ボックス向き順'])) for _, x in o.iterrows()}
def top5_common(rid):
    h = horses[horses['レースID'].eq(rid)].dropna(subset=['全馬共通M順位']).sort_values(['全馬共通M順位', '馬番'])
    return [(int(x['馬番']) if pd.notna(x['馬番']) else None, x['馬名']) for _, x in h.head(5).iterrows()]
def top5_v5(rid):
    f = v5dir / f'{rid}.csv'
    if not f.exists(): return []
    t = pd.read_csv(f).dropna(subset=['順位']).sort_values('順位')
    nums = horses[horses['レースID'].eq(rid)].drop_duplicates('馬名').set_index('馬名')['馬番']
    return [(int(nums[x['馬名']]) if pd.notna(nums.get(x['馬名'])) else None, x['馬名']) for _, x in t.head(5).iterrows()]
def fmt(lst):
    return '<ol>' + ''.join(f"<li><b>{n if n else '—'}</b> {e(m)}</li>" for n, m in lst) + '</ol>' if lst else '<p class=mut>順位なし（全頭保留）</p>'
WD = '月火水木金土日'
body = []
summary = []
for day, g in races.groupby('日付'):
    dt = pd.Timestamp(day); label = f"{dt.month}/{dt.day}（{WD[dt.weekday()]}）"
    g = g.assign(_t=g['レースID'].map(lambda x: tier.get(x, ('外', 99))[0]), _o=g['レースID'].map(lambda x: tier.get(x, ('外', 99))[1]))
    chips = ''.join(f"<a class='chip t{x['_t']}' href='#r{x['レースID']}'>{x['_t']} {e(x['競馬場'])}{int(x['R'])}R</a>" for _, x in g.sort_values('_o').iterrows())
    few = (g['_t'] != '外').sum() <= 3
    note = "<div class=mut>この日は対象が3レース以下なので全部 A になります。過去の確認（各日11〜12レースから上位3つを選んだ場合）と同じ意味ではありません。</div>" if few else ''
    summary.append(f"<div class=day><b>{label}</b> 当たりやすい順：<div class=chips>{chips}</div>{note}</div>")
    body.append(f"<h2 id=d{dt.day}>{label} {len(g)}レース</h2>")
    for _, r in g.sort_values('発走').iterrows():
        rid = r['レースID']; t, o_ = tier.get(rid, ('外', None))
        tl = {'A': 'A 当たりやすい（各日上位3）', 'B': 'B 中（各日4〜6番目）', 'C': 'C 低め（各日7番目以下）', '外': '対象外（新馬戦など）'}[t]
        warn = ' <span class=mut>馬番未確定あり</span>' if '馬番未確定' in str(r.get('注意点', '')) else ''
        body.append(f"<section id=r{rid}><div class=hd><span class='tag t{t}'>{tl}</span></div>"
                    f"<h3>{e(r['競馬場'])}{int(r['R'])}R {e(r['レース名'])} <span class=mut>{e(r['発走'])}・{e(r['コース'])}・{int(r['頭数'])}頭</span>{warn}</h3>"
                    f"<div class=cols><div><div class=lab>v5（区分の基準）上位5頭</div>{fmt(top5_v5(rid))}</div>"
                    f"<div><div class=lab>主予測（全馬共通M）上位5頭</div>{fmt(top5_common(rid))}</div></div></section>")
cnt = pd.Series([v[0] for v in tier.values()]).value_counts()
page = f"""<!doctype html><html lang=ja><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><meta name="claude:viewport-fit-sides" content="cover"><title>週末 JRA 芝 予想</title>
<style>:root{{--bg:#f6f8fa;--card:#fff;--fg:#17202a;--mut:#5b6875;--line:#dde3e8;--A:#13794a;--Ab:#e3f5ea;--B:#9a6200;--Bb:#fdf1dc;--C:#6b7480;--Cb:#eef0f2}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#121417;--card:#1b1e22;--fg:#e9edf1;--mut:#9aa6b2;--line:#2c3138;--A:#5fd08a;--Ab:#173323;--B:#f0b44c;--Bb:#3a2c12;--C:#a3acb6;--Cb:#262a30}}}}
html,body{{background:var(--bg);color:var(--fg)}}body{{font-family:-apple-system,sans-serif;margin:0;padding:16px max(16px,env(safe-area-inset-right,0px)) 16px max(16px,env(safe-area-inset-left,0px));line-height:1.5;max-width:820px;margin:auto}}
h1{{font-size:20px;margin:4px 0}}h2{{font-size:18px;margin:22px 0 6px;padding-top:6px;border-top:2px solid var(--line)}}h3{{font-size:15px;margin:4px 0 6px}}.mut{{color:var(--mut);font-size:12px;font-weight:400}}
.box,section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0;font-size:14px}}
.tag,.chip{{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:1px 7px}}.chip{{margin:3px 4px 0 0;text-decoration:none}}
.tA{{color:var(--A);background:var(--Ab)}}.tB{{color:var(--B);background:var(--Bb)}}.tC,.t外{{color:var(--C);background:var(--Cb)}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}@media (max-width:480px){{.cols{{grid-template-columns:1fr}}}}.lab{{font-size:12px;color:var(--mut)}}ol{{margin:2px 0;padding-left:20px}}li{{margin:1px 0}}.day{{margin:6px 0}}:root[data-theme=dark]{{--bg:#121417;--card:#1b1e22;--fg:#e9edf1;--mut:#9aa6b2;--line:#2c3138;--A:#5fd08a;--Ab:#173323;--B:#f0b44c;--Bb:#3a2c12;--C:#a3acb6;--Cb:#262a30}}</style></head><body>
<h1>11/28（土）・29（日）・30（月） JRA 芝 予想</h1>
<div class=mut>レース前に保存した記録（11/27 保存、土曜の馬番は 11/27 夜の再取得で確定）をそのまま並べています。順位は手で直していません。</div>
<div class=box><b>当たりやすさ（信頼度）A・B・C の決め方</b><br>
各日、出走頭数の少ない順（同じなら v5 の上位5頭の点が6位以下より抜けている順）に並べ、A＝上位3レース、B＝4〜6番目、C＝7番目以下。<br>
過去の確認（2026年春・1回だけ）：A のレースで v5 上位5頭の三連複ボックスが当たったのは <b>50%</b>（全レースでは 27%、差の95%区間 +12〜+37pt）。<br>
<span class=mut>これは過去の的中の割合で、このレースが当たる確率ではありません。B と C の差は未確認です。回収率は未検証です。新馬戦は v5 が騎手だけで順位をつけるため対象外。区分の数：A {cnt.get('A',0)}・B {cnt.get('B',0)}・C {cnt.get('C',0)}。</span></div>
{''.join(summary)}
<div class=box><span class=mut>v5＝影の予測（近走の人気も使う、検証で全馬共通より上位3に勝ち馬 +1.8pt・区間は0をまたぐ）。主予測＝全馬共通（重みは仮、前向きの評価中）。どちらも人気に情報を足しているとは言えない（18 の検証）。日曜・月曜の一部は馬番未確定（土曜に確定）。</span></div>
{''.join(body)}
</body></html>"""
Path(out).write_text(page, encoding='utf-8')
print(cnt.to_dict(), len(page))
tiers = pd.DataFrame([{'レースID': k, '区分': v[0], '各日の順': v[1]} for k, v in tier.items()])
tiers.merge(races[['日付', '発走', '競馬場', 'R', 'レース名', 'レースID']], on='レースID').sort_values(['日付', '各日の順']).to_csv(Path(out).with_suffix('.区分.csv'), index=False, encoding='utf-8-sig')
