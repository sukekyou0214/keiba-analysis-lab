import unittest
from unittest.mock import Mock, patch

from keiba_core import get_horses


from unittest.mock import patch as _policy_patch
from keiba_fetch_policy import FetchPolicy as _FetchPolicy
_policy_patcher = None


def setUpModule():
    """Mocked transports must not touch the shared gate state or wait between requests."""
    global _policy_patcher
    _policy_patcher = _policy_patch('keiba_fetch_policy.POLICY', _FetchPolicy(sleeper=lambda _: None))
    _policy_patcher.start()


def tearDownModule():
    _policy_patcher.stop()



class EntryParserTests(unittest.TestCase):
    def test_entry_rows_exclude_related_links_and_use_horse_number(self):
        html = """
        <tr class="HorseList">
          <td class="Waku">3</td><td class="Umaban">7</td>
          <td class="HorseInfo"><a href="/horse/2020000001/">出走馬</a></td>
        </tr>
        <a href="/horse/2020000002/">関連記事の馬</a>
        """
        response = Mock(text=html, apparent_encoding='utf-8')
        get_horses.clear()
        self.addCleanup(get_horses.clear)
        with patch('keiba_core.requests.get', return_value=response):
            horses, numbers = get_horses('202608050411')
        self.assertEqual(horses, {'2020000001': '出走馬'})
        self.assertEqual(numbers, {'2020000001': 7})
