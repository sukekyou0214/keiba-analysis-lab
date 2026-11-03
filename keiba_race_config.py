"""Race metadata drives the common pipeline; never infer schedule/race IDs."""
from dataclasses import dataclass
from datetime import date
import re
from urllib.parse import urlparse,parse_qs

VENUES={'01':'札幌','02':'函館','03':'福島','04':'新潟','05':'東京','06':'中山','07':'中京','08':'京都','09':'阪神','10':'小倉'}

@dataclass(frozen=True)
class RaceConfig:
    name: str
    special_url: str
    entry_url: str
    race_date: str
    venue: str
    distance: int
    surface: str='芝'
    first_year: int=2016
    last_year: int=2025

    def __post_init__(self):
        from keiba_affinity_collect import validate_special_url
        validate_special_url(self.special_url)
        if not self.name.strip() or self.venue not in VENUES.values() or self.surface not in {'芝','ダ'}:
            raise ValueError('レース名・競馬場・コースを確認してください')
        day=date.fromisoformat(self.race_date)
        if not 1000<=self.distance<=4000 or not 2000<=self.first_year<=self.last_year<day.year or self.last_year-self.first_year>=10:
            raise ValueError('距離・過去年範囲を確認してください（最大10年、開催年より前）')
        if self.entry_url:
            u=urlparse(self.entry_url);rid=parse_qs(u.query).get('race_id',[''])[0]
            if u.scheme!='https' or u.hostname!='race.netkeiba.com' or u.path!='/race/shutuba.html' or not re.fullmatch(r'\d{12}',rid):
                raise ValueError('netkeiba出馬表URLを指定してください')
            if int(rid[:4])!=day.year or VENUES.get(rid[4:6])!=self.venue:
                raise ValueError('出馬表の年度・開催場が設定と一致しません')

    @property
    def historical_scope(self):
        from keiba_v22 import scope_key
        return scope_key(self.special_url,self.first_year,self.last_year)

    @property
    def current_scope(self):
        return f'{self.historical_scope}-entry-{self.race_date}-{self.venue}-{self.surface}-{self.distance}-'+parse_qs(urlparse(self.entry_url).query).get('race_id',['none'])[0]

    @property
    def years(self):return set(range(self.first_year,self.last_year+1))

    def validate_targets(self,frame):
        from keiba_v22 import validate_scope
        import pandas as pd
        validate_scope(frame,self.special_url,self.first_year,self.last_year)
        if not frame['レース名'].astype(str).str.contains(self.name,regex=False).all():
            raise ValueError('保存データのレース名が対象と一致しません')
        if not frame['レースID'].astype(str).str[4:6].isin(VENUES).all():
            raise ValueError('JRA開催場を確認できません')

ELIZABETH_CONFIG=RaceConfig('エリザベス女王杯','https://race.netkeiba.com/special/index.html?id=0117','https://race.netkeiba.com/race/shutuba.html?race_id=202608050411','2026-11-15','京都',2200)
AUTUMN_CONFIG=RaceConfig('天皇賞','https://race.netkeiba.com/special/index.html?id=0109','https://race.netkeiba.com/race/shutuba.html?race_id=202605041111','2026-11-01','東京',2000)
PRESETS={'エリザベス女王杯':ELIZABETH_CONFIG,'天皇賞・秋（検証用）':AUTUMN_CONFIG}
