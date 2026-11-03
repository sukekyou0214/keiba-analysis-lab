"""Launch the Ver.21 page in Streamlit's built-in test harness."""
import unittest
from streamlit.testing.v1 import AppTest


class PageSmokeTest(unittest.TestCase):
    def test_page_initial_render(self):
        app = AppTest.from_file("pages/2_過去レース相性_Ver21.py", default_timeout=25).run()
        self.assertEqual([str(e.message) for e in app.exception], [])
        self.assertTrue(len(app.title) >= 1)
        self.assertIn("過去レース相性分析", app.title[0].value)


if __name__ == "__main__":
    unittest.main()
