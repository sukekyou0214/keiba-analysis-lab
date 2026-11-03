"""Launch the Ver.21 page in Streamlit's built-in test harness."""
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class PageSmokeTest(unittest.TestCase):
    def test_page_initial_render(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/2_過去レース相性_Ver21.py"), default_timeout=25).run()
        self.assertEqual([str(e.message) for e in app.exception], [])
        self.assertTrue(len(app.title) >= 1)
        self.assertIn("過去レース相性分析", app.title[0].value)


    def test_csv_analysis_without_upload_shows_validation(self):
        page = Path(__file__).resolve().parents[1] / "pages/2_過去レース相性_Ver21.py"
        app = AppTest.from_file(str(page), default_timeout=25).run()
        action = next(x for x in app.button if x.label == "過去レース相性を計算")
        action.click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("CSVファイルを選択" in x.message
                            for x in app.error))



if __name__ == "__main__":
    unittest.main()
