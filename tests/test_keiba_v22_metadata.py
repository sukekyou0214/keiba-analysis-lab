import unittest
from keiba_race_metadata import *
URL='https://race.netkeiba.com/race/shutuba.html?race_id=202608050411'
class MetadataTests(unittest.TestCase):
    def test_explicit_metadata(self):
        h='<h1 class="RaceName">試験重賞</h1><div class="RaceData01">芝2200m</div><div class="RaceData02">5回京都4日</div><time datetime="2026-11-15"></time><a href="/special/index.html?id=0117">特集</a>'
        d=parse_metadata(h,URL)
        self.assertEqual(d['distance'],2200);self.assertEqual(d['race_date'],'2026-11-15');self.assertEqual(d['venue'],'京都')
    def test_missing_and_ambiguous_remain_missing(self):
        d=parse_metadata('<h1 class="RaceName">試験</h1><time datetime="2026-11-15"></time><time datetime="2026-11-16"></time>',URL)
        self.assertNotIn('race_date',d);self.assertNotIn('distance',d)
    def test_conflicting_venue_rejected(self):
        with self.assertRaises(ValueError):parse_metadata('<div class="RaceData02">東京</div>',URL)
    def test_urls_are_restricted(self):
        for u in ['http://race.netkeiba.com/race/shutuba.html?race_id=202608050411','https://example.com/','https://race.netkeiba.com.evil.test/race/shutuba.html?race_id=202608050411']:
            with self.assertRaises(ValueError):validate_entry_url(u)
