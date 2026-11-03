"""Read only explicitly declared metadata; leave ambiguous fields for input."""
import re
from datetime import date
from urllib.parse import urlparse,parse_qs,urljoin
from bs4 import BeautifulSoup
from keiba_race_config import VENUES


def validate_entry_url(url):
    u=urlparse(url)
    rid=parse_qs(u.query).get('race_id',[''])[0]
    if (u.scheme!='https' or u.hostname!='race.netkeiba.com' or u.port not in (None,443)
            or u.username or u.password or u.path!='/race/shutuba.html' or not re.fullmatch(r'\d{12}',rid)):
        raise ValueError('netkeibaの出馬表URLを指定してください')
    return rid


def parse_metadata(html,url):
    rid=validate_entry_url(url)
    soup=BeautifulSoup(html,'html.parser');result={}
    title=soup.select_one('h1.RaceName')
    if title and title.get_text(strip=True):result['name']=title.get_text(' ',strip=True)
    data=soup.select_one('.RaceData01')
    if data:
        hits=re.findall(r'(芝|ダ)\s*(\d{3,4})\s*m',data.get_text(' ',strip=True))
        if len(set(hits))==1:
            surface,distance=hits[0]
            if 1000<=int(distance)<=4000:result.update(surface=surface,distance=int(distance))
    data2=soup.select_one('.RaceData02')
    if data2:
        venues=[v for v in VENUES.values() if v in data2.get_text(' ',strip=True)]
        if len(venues)==1:
            if venues[0]!=VENUES.get(rid[4:6]):raise ValueError('ページの競馬場とレースIDが一致しません')
            result['venue']=venues[0]
    dates=set()
    for node in soup.select('time[datetime], .RaceList_Date .Active, .RaceList_Date .active'):
        raw=node.get('datetime',node.get_text(' ',strip=True))
        m=re.search(r'(20\d{2})[年/\-](\d{1,2})[月/\-](\d{1,2})',raw)
        if m:
            try:dates.add(date(*map(int,m.groups())).isoformat())
            except ValueError:pass
    if len(dates)==1:
        day=dates.pop()
        if day[:4]!=rid[:4]:raise ValueError('ページの開催年とレースIDが一致しません')
        result['race_date']=day
    specials=set()
    for a in soup.select('a[href]'):
        link=urljoin(url,a['href']);u=urlparse(link)
        if u.scheme=='https' and u.hostname=='race.netkeiba.com' and u.path=='/special/index.html':
            sid=parse_qs(u.query).get('id',[''])[0]
            if sid.isdecimal():specials.add('https://race.netkeiba.com/special/index.html?id='+sid)
    if len(specials)==1:result['special_url']=specials.pop()
    if not result:raise ValueError('レース情報を読み取れません。手入力できます')
    return result


def fetch_metadata(url):
    import requests
    validate_entry_url(url)
    r=requests.get(url,timeout=20,allow_redirects=False,headers={'User-Agent':'KeibaAnalysisLab/22'})
    if 300<=r.status_code<400:raise ValueError('転送先へ自動アクセスせず停止しました。URLを確認してください')
    r.raise_for_status();r.encoding=r.apparent_encoding or r.encoding
    return parse_metadata(r.text,url)
