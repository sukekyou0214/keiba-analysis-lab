"""Sequential, resumable acquisition. Abort on first failed horse; no retry loop."""
from __future__ import annotations
import time
import re
import pandas as pd
from bs4 import BeautifulSoup
from keiba_affinity_collect import HORSE_ID
from keiba_v22 import ENTRY_URL


def parse_entries(html, source=ENTRY_URL, expected_name='エリザベス女王杯'):
    soup=BeautifulSoup(html,'html.parser')
    title=soup.select_one('h1.RaceName')
    if title is None or expected_name not in title.get_text():
        raise ValueError('対象レースの見出しを確認できません')
    rows=[]
    for tr in soup.select('tr.HorseList'):
        a=tr.select_one('.HorseName a[href*="/horse/"]')
        if a is None: a=tr.select_one('a[href*="/horse/"]')
        m=HORSE_ID.search(a.get('href','')) if a else None
        if not m: raise ValueError('予定馬IDを取得できません')
        jockey=tr.select_one('.Jockey')
        pop=tr.select_one('.Popular')
        text=pop.get_text(strip=True) if pop else ''
        rows.append({'馬ID':m[1],'馬名':a.get_text(strip=True),'予定騎手':jockey.get_text(' ',strip=True) if jockey else pd.NA,'想定人気':float(text) if text.isdecimal() else float('nan'),'情報区分':'出走予定・騎手想定','取得元':source,'確認日時':pd.Timestamp.now(tz='Asia/Tokyo').isoformat()})
    d=pd.DataFrame(rows)
    if not 5<=len(d)<=30 or d['馬ID'].duplicated().any(): raise ValueError('予定馬の件数または重複に問題があります')
    return d


def collect_remaining(targets, loader, tables, store, scope, progress=lambda *args:None, delay=2.0, sleeper=time.sleep):
    from keiba_affinity_pipeline import collect_batch
    if not 2<=delay<=30:raise ValueError('取得間隔は2秒以上にしてください')
    tables=dict(tables)
    old=tables.get('horse_log',pd.DataFrame())
    done=set(old.loc[old['status'].eq('成功'),'horse_id'].astype(str)) if not old.empty else set()
    horses=targets[['馬ID','馬名']].drop_duplicates('馬ID')
    started=time.monotonic()
    for _,horse in horses.iterrows():
        hid=str(horse['馬ID'])
        if hid in done:continue
        sleeper(delay)
        raw,log=collect_batch(pd.DataFrame([horse]),loader,limit=1)
        tables['horse_log']=pd.concat([tables.get('horse_log',pd.DataFrame()),log],ignore_index=True).drop_duplicates('horse_id',keep='last')
        if not raw.empty:
            previous=tables.get('horse_raw',pd.DataFrame())
            if not previous.empty:previous=previous.loc[~previous['馬ID'].astype(str).eq(hid)]
            tables['horse_raw']=pd.concat([previous,raw],ignore_index=True)
            done.add(hid)
        tables.pop('affinity',None)
        store.save(scope,tables)
        progress(len(done),len(horses),time.monotonic()-started)
        if not log['status'].eq('成功').all():
            raise RuntimeError('取得先の応答を確認できないため停止しました。取得済み分は保存済みです：'+str(log.iloc[0]['reason']))
    return tables
