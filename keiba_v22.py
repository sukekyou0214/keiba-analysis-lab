"""Ver.22 offline, date-isolated descriptive features. Scores are not probabilities."""
from __future__ import annotations
import os
from pathlib import Path
import sqlite3
import time
import pandas as pd

ELIZABETH = 'https://race.netkeiba.com/special/index.html?id=0117'
ENTRY_URL = 'https://race.netkeiba.com/race/shutuba.html?race_id=202608050411'

def scope_key(url, start=2016, end=2025):
    from keiba_affinity_collect import validate_special_url
    from urllib.parse import parse_qs, urlparse
    validate_special_url(url)
    if not 2000 <= int(start) <= int(end) <= 2099:
        raise ValueError('Invalid historical years')
    return f"special-{int(parse_qs(urlparse(url).query)['id'][0])}-{int(start)}-{int(end)}"

class Store:
    """Atomic per-scope checkpoints; mount KEIBA_V22_STORE on durable storage."""
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('KEIBA_V22_STORE', '.cache/keiba-v22.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (scope TEXT PRIMARY KEY, payload BLOB NOT NULL, updated REAL NOT NULL)')
    def save(self, scope, tables):
        from keiba_affinity_store import pack_checkpoint
        payload = pack_checkpoint(tables)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)', (scope, payload, time.time()))
    def load(self, scope):
        from keiba_affinity_store import load_checkpoint
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute('SELECT payload,updated FROM snapshots WHERE scope=?', (scope,)).fetchone()
        return (load_checkpoint(row[0]), row[1]) if row else ({}, None)

def validate_scope(targets, url, start, end):
    """Reject foreign races even if a restored ZIP has no origin manifest."""
    scope_key(url,start,end)
    if targets.empty or not {'レースID','レース名','日付'}.issubset(targets):
        raise ValueError('対象データが空、または識別列がありません')
    ids = targets['レースID'].astype(str)
    dates = pd.to_datetime(targets['日付'],errors='coerce')
    if dates.isna().any() or not dates.dt.year.between(start,end).all():
        raise ValueError('対象年度が一致しません')
    if not ids.str.fullmatch(r'\d{12}').all() or not ids.str[:4].eq(dates.dt.year.astype(str)).all():
        raise ValueError('レースIDと年度が一致しません')
    sid = scope_key(url,start,end).split('-')[1]
    expected = {'117':'エリザベス女王杯','109':'天皇賞'}.get(sid)
    if expected and not targets['レース名'].astype(str).str.contains(expected,regex=False).all():
        raise ValueError('別レースのデータです。対象を確認してください')
    if sid == '117':
        expected_code = dates.dt.year.map(lambda y:'09' if 2020<=y<=2022 else '08')
        if not ids.str[4:6].eq(expected_code).all():
            raise ValueError('エリザベス女王杯の開催場が年度と一致しません')

def history_frame(raw):
    """Keep margin and source pace labels which legacy conversion drops."""
    if raw.empty:
        return raw.copy()
    if '着順_数値' in raw:
        d=raw.copy()
    else:
        from keiba_core import convert_auto
        d=convert_auto(raw)
        d['着差']=pd.to_numeric(raw.get('着差',pd.Series(index=raw.index,dtype=float)),errors='coerce')
        for col in ['netkeibaペース','単勝オッズ']:
            if col in raw: d[col]=raw[col]
    d['日付']=pd.to_datetime(d['日付'],errors='coerce')
    for col in ['着順_数値','人気','距離_m','4角順位','頭数','上がり3F_秒','着差','単勝オッズ']:
        d[col]=pd.to_numeric(d.get(col,pd.Series(index=d.index,dtype=float)),errors='coerce')
    for col in ['競馬場','レース名','コース','netkeibaペース','レース格']:
        if col not in d: d[col]=pd.NA
    # No independent numeric pace inference is passed off as a source S/M/H label.
    d['ペース区分']=d['netkeibaペース'].map({'スロー寄り':'S','ミドル寄り':'M','ハイ寄り':'H','S':'S','M':'M','H':'H'})
    ratio=d['4角順位']/d['頭数'].where(d['頭数'].gt(0))
    d['脚質']='不明'
    d.loc[ratio.notna() & ratio.le(.33),'脚質']='先行'
    d.loc[ratio.gt(.33) & ratio.le(.67),'脚質']='差し'
    d.loc[ratio.gt(.67),'脚質']='追込'
    d.loc[d['4角順位'].eq(1),'脚質']='逃げ'
    return d

def rate(frame):
    f=pd.to_numeric(frame['着順_数値'],errors='coerce')
    valid=f.ge(1)&f.mod(1).eq(0)
    if '頭数' in frame:
        field=pd.to_numeric(frame['頭数'],errors='coerce')
        valid &= field.isna()|f.le(field)
    f=f.loc[valid]
    return float(f.le(3).mean()*100) if len(f) else float('nan')

def pre_features(h, cutoff, config=None):
    h=h.loc[h['日付'].lt(pd.Timestamp(cutoff))].sort_values('日付',ascending=False)
    if h['日付'].isna().any(): raise ValueError('日付不明')
    turf=h.loc[h['コース'].eq('芝')]
    out={'過去走数':len(h),'芝走数':len(turf)}
    prev=h.iloc[0] if len(h) else pd.Series(dtype=object)
    for dest,src in [('前走','レース名'),('前走日','日付'),('前走着順','着順_数値'),('前走着差','着差'),('前走上がり3F','上がり3F_秒'),('脚質','脚質')]:
        out[dest]=prev.get(src,pd.NA)
    for dist in [1800,2000,2200,2400]:
        sub=turf.loc[turf['距離_m'].eq(dist)]
        out[f'{dist}m走数']=len(sub);out[f'{dist}m複勝率']=rate(sub)
    kyoto=turf.loc[turf['競馬場'].eq('京都')]
    out['京都走数']=len(kyoto);out['京都複勝率']=rate(kyoto)
    k22=kyoto.loc[kyoto['距離_m'].eq(2200)]
    out['京都2200走数']=len(k22);out['京都2200複勝率']=rate(k22)
    pace_base=h.loc[h['コース'].eq(config.surface if config else '芝')]
    for pace in ['S','M','H']:
        sub=pace_base.loc[pace_base['ペース区分'].eq(pace)]
        out[f'{pace}走数']=len(sub);out[f'{pace}複勝率']=rate(sub)
    venue=config.venue if config else '京都'
    distance=config.distance if config else 2200
    surface=config.surface if config else '芝'
    selected=h.loc[h['コース'].eq(surface)]
    for label,sub in [('対象場',selected.loc[selected['競馬場'].eq(venue)]),('対象距離',selected.loc[selected['距離_m'].eq(distance)]),('対象場距離',selected.loc[selected['競馬場'].eq(venue)&selected['距離_m'].eq(distance)])]:
        out[label+'走数']=len(sub);out[label+'複勝率']=rate(sub)
    return out

def historical_features(targets, raw, config=None):
    if config:config.validate_targets(targets)
    else:validate_scope(targets,ELIZABETH,2016,2025)
    h=history_frame(raw)
    if h.empty: raise ValueError('馬別戦績が未取得です')
    if h['日付'].isna().any(): raise ValueError('日付不明の戦績があり、前走を保証できません')
    rows=[]
    for _,r in targets.iterrows():
        prior=h.loc[h['馬ID'].astype(str).eq(str(r['馬ID']))]
        f=pre_features(prior,r['日付'],config)
        rid=str(r['レースID'])
        rows.append({**r.to_dict(),**f,'開催区分':__import__('keiba_race_config').VENUES.get(rid[4:6],'不明')})
    return pd.DataFrame(rows)

def pattern_tables(features):
    """Descriptive only; retain unknown values and separate target venues."""
    d=features.copy()
    d['人気帯']=pd.cut(pd.to_numeric(d['人気'],errors='coerce'),[0,3,5,9,99],labels=['1〜3人気','4〜5人気','6〜9人気','10人気以下']).astype('string').fillna('不明')
    d['前走着順帯']=pd.cut(pd.to_numeric(d['前走着順'],errors='coerce'),[0,1,3,5,99],labels=['1着','2〜3着','4〜5着','6着以下']).astype('string').fillna('不明')
    d['前走着差帯']=pd.cut(pd.to_numeric(d['前走着差'],errors='coerce'),[-float('inf'),0,.3,1,float('inf')],labels=['0秒以下','.1〜.3秒','.4〜1秒','1秒超']).astype('string').fillna('不明')
    d['前走上がり帯']=pd.cut(pd.to_numeric(d['前走上がり3F'],errors='coerce'),[0,34,35,99],labels=['34秒以下','34秒超35秒以下','35秒超']).astype('string').fillna('不明')
    columns=['人気帯','前走','前走着順帯','前走着差帯','前走上がり帯','脚質']
    for label in ['1800m','2000m','2200m','2400m','京都','S','M','H']:
        count=d[f'{label}走数']; success=d[f'{label}複勝率']
        d[label+'実績']='不明'
        d.loc[count.gt(0),label+'実績']='経験あり・3着内なし'
        d.loc[count.gt(0)&success.gt(0),label+'実績']='3着内経験あり'
        # No rows does not prove absence without source completeness verification.
        columns.append(label+'実績')
    rows=[]
    for col in columns:
        for (venue,label),g in d.groupby(['開催区分',col],dropna=False):
            odds=pd.to_numeric(g.get('単勝オッズ',pd.Series(index=g.index,dtype=float)),errors='coerce')
            f=pd.to_numeric(g['着順_数値'],errors='coerce')
            complete=odds.notna().all() and f.notna().all()
            rows.append({'開催':venue,'項目':col,'条件':str(label) if pd.notna(label) else '不明','出走数':len(g),'結果判明数':int(f.notna().sum()),'3着内数':int(f.le(3).sum()),'3着内率':rate(g),'単勝回収率':float(odds.where(f.eq(1),0).sum()/len(g)*100) if complete else float('nan'),'複勝回収率':float('nan'),'信頼度':'参考' if len(g)>=10 else '小標本'})
    return pd.DataFrame(rows)

def rank_entries(entries, raw, cutoff='2026-11-15', pace='M', config=None):
    """Transparent heuristic with evidence coverage; no fabricated model EV."""
    if config:cutoff=config.race_date
    h=history_frame(raw); rows=[]
    if not h.empty and {'馬ID','レースID'}.issubset(h):
        identified=h['レースID'].fillna('').astype(str).str.fullmatch(r'\d{12}')
        h=pd.concat([h.loc[identified].drop_duplicates(['馬ID','レースID']),h.loc[~identified]],ignore_index=True)
    if not h.empty and h['日付'].isna().any(): raise ValueError('日付不明の戦績が含まれています')
    for _,e in entries.iterrows():
        mine=h.loc[h['馬ID'].astype(str).eq(str(e['馬ID']))] if not h.empty else h
        if mine.empty:
            rows.append({**e.to_dict(),'総合参考点':float('nan'),'判定':'戦績未取得','妙味':'保留'});continue
        f=pre_features(mine,cutoff,config)
        recent=mine.loc[mine['日付'].lt(pd.Timestamp(cutoff)) & mine['コース'].eq(config.surface if config else '芝')].sort_values('日付',ascending=False).head(5)
        # Finishing percentile is relative placing, not a speed or ability index.
        valid=recent['着順_数値'].ge(1) & recent['着順_数値'].le(recent['頭数']) & recent['着順_数値'].mod(1).eq(0) & recent['頭数'].gt(1)
        ability=((1-(recent.loc[valid,'着順_数値']-1)/(recent.loc[valid,'頭数']-1))*100).mean()
        components=[(ability,.4),(f['対象場距離複勝率'],.2),(f['対象距離複勝率'],.15),(f['対象場複勝率'],.1),(f[f'{pace}複勝率'],.15)]
        known=[(v,w) for v,w in components if pd.notna(v)]
        coverage=sum(w for _,w in known)
        score=sum(v*w for v,w in known)/coverage if coverage>=.6 and valid.sum()>=3 else float('nan')
        rows.append({**e.to_dict(),**f,'近5走着順参考点':ability,'近5走有効数':int(valid.sum()),'総合参考点':round(score,1) if pd.notna(score) else score,'評価項目充足率':round(coverage*100),'判定':'順位保留・根拠不足' if pd.isna(score) else ('暫定・少数' if valid.sum()<5 or coverage<.8 else '暫定'),'持続力':'ラップ未取得・判定保留','瞬発力':'上がり順位未取得・判定保留','消耗戦':'ラップ未取得・判定保留','好走パターン一致度':'研究データ検証待ち','妙味':'想定人気未取得・保留'})
    d=pd.DataFrame(rows)
    if d.empty:return d
    d=d.sort_values('総合参考点',ascending=False,na_position='last').reset_index(drop=True)
    d['参考順位']=d['総合参考点'].rank(method='min',ascending=False).astype('Int64')
    if '想定人気' in d:
        pop=pd.to_numeric(d['想定人気'],errors='coerce')
        d.loc[pop.ge(1),'妙味']='順位差による抽出対象外（期待値未検証）'
        mask=pop.between(4,9)&d['参考順位'].le(5)&d['評価項目充足率'].ge(80)&d['近5走有効数'].ge(5) if '評価項目充足率' in d else pd.Series(False,index=d.index)
        d.loc[mask,'妙味']='中穴候補（参考順位差・期待値未検証）'
    return d


def venue_bundle(tables, venue, config=None):
    """Scope all four verified inputs together; never reuse combined affinity."""
    from keiba_race_config import VENUES
    if venue not in VENUES.values():
        raise ValueError('開催場は京都または阪神です')
    target=tables['targets']
    if config:config.validate_targets(target)
    else:validate_scope(target,ELIZABETH,2016,2025)
    code=next(k for k,v in VENUES.items() if v==venue)
    target=target.loc[target['レースID'].astype(str).str[4:6].eq(code)].copy()
    if target.empty:raise ValueError('選択した開催場のデータがありません')
    races=set(target['レースID'].astype(str));horses=set(target['馬ID'].astype(str))
    return {
        'targets':target,
        'target_log':tables['target_log'].loc[tables['target_log']['race_id'].astype(str).isin(races)].copy(),
        'horse_raw':tables['horse_raw'].loc[tables['horse_raw']['馬ID'].astype(str).isin(horses)].copy(),
        'horse_log':tables['horse_log'].loc[tables['horse_log']['horse_id'].astype(str).isin(horses)].copy(),
    }


def match_historical_patterns(features, patterns, minimum=10, venue='京都'):
    """Descriptive matches only: Kyoto cohorts; no target outcome in predictors.

    Matching is not a fitted model or independent evidence across overlapping
    conditions. Unknowns and sample-poor patterns never count as matches.
    """
    rows=[]
    if patterns.empty:return pd.DataFrame()
    for label,column in [('前走','前走'),('脚質','脚質')]:
        value=features.get(column)
        if pd.isna(value) or value in ('不明',''):continue
        matches=patterns.loc[patterns['開催'].eq(venue) & patterns['項目'].eq(label)
                             & patterns['条件'].eq(str(value)) & patterns['出走数'].ge(minimum)]
        for _,r in matches.iterrows():
            if pd.isna(r['3着内率']):continue
            rows.append({'条件':f'{label}：{value}','標本数':int(r['出走数']),
                         '3着内数':int(r['3着内数']),'過去3着内率':float(r['3着内率']),
                         '位置付け':'探索的・条件の重複あり・予測確率ではない'})
    return pd.DataFrame(rows)
