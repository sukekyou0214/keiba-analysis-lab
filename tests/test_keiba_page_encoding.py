"""馬成績ページの文字コードは決め打ちせず、ページの宣言どおりに読む。"""
import unittest
from unittest.mock import Mock

from keiba_fetch_policy import page_encoding

TABLE = ('<table class="db_h_race_results"><tr><th>日付</th><th>開催</th><th>距離</th><th>馬 場</th><th>着 順</th>'
         '<th>上り</th><th>通過</th><th>レース名</th></tr>'
         '<tr><td>2026/10/01</td><td>5京都1</td><td>芝2000</td><td>良</td><td>1</td><td>34.0</td><td>3-3</td>'
         '<td><a href="/race/202608050101/">試験S</a></td></tr></table>')


def page(charset, header=''):
    html = f'<html><head><meta charset="{charset}"></head><body>{TABLE}</body></html>'
    return Mock(content=html.encode(charset), text=None, headers={'Content-Type': header}, apparent_encoding=None,
                status_code=200, raise_for_status=lambda: None)


class PageEncodingTests(unittest.TestCase):
    def test_meta_charset_wins(self):
        self.assertEqual(page_encoding(page('utf-8', 'text/html; charset=EUC-JP')), 'utf-8')
        self.assertEqual(page_encoding(page('euc-jp')), 'euc-jp')

    def test_header_then_default(self):
        res = Mock(content=b'<html></html>', headers={'Content-Type': 'text/html; charset=Shift_JIS'}, apparent_encoding=None)
        self.assertEqual(page_encoding(res), 'shift_jis')
        res = Mock(content=b'<html></html>', headers={}, apparent_encoding=None)
        self.assertEqual(page_encoding(res), 'utf-8')

    def test_core_results_read_both_encodings(self):
        from unittest.mock import patch
        import keiba_fetch_policy
        from keiba_core import get_results
        for charset in ('utf-8', 'euc-jp'):
            res = page(charset)
            class Wrapped:
                content = res.content
                headers = res.headers
                apparent_encoding = None
                status_code = 200
                encoding = None
                def raise_for_status(self):
                    return None
                @property
                def text(self):
                    return self.content.decode(self.encoding)
            get_results.clear()
            policy = keiba_fetch_policy.FetchPolicy(sleeper=lambda _: None, transport=lambda *a, **k: Wrapped())
            with patch('keiba_fetch_policy.POLICY', policy):
                frame = get_results('2020000001', '試験馬')
            self.assertIn('日付', frame.columns)
            self.assertEqual(frame.iloc[0]['距離'], '芝2000')
        get_results.clear()


if __name__ == '__main__':
    unittest.main()
