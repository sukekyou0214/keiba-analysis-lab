"""Launch the Ver.21 page in Streamlit's built-in test harness."""
import unittest
from pathlib import Path
import pandas as pd
from streamlit.testing.v1 import AppTest


class PageSmokeTest(unittest.TestCase):
    def test_page_initial_render(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/2_過去レース相性_Ver21.py"), default_timeout=25).run()
        self.assertEqual([str(e.message) for e in app.exception], [])
        self.assertTrue(len(app.title) >= 1)
        self.assertIn("過去レース相性分析", app.title[0].value)


    def test_injected_verified_data_calculates_in_page(self):
        page = Path(__file__).resolve().parents[1] / "pages/2_過去レース相性_Ver21.py"
        app = AppTest.from_file(str(page), default_timeout=25).run()
        rid = "202505041111"
        horses = ("2020000001", "2020000002")
        target = pd.DataFrame([
            {"レースID": rid, "馬ID": hid, "馬名": "甲" if i == 0 else "乙",
             "日付": "2025-11-02", "レース名": "天皇賞（秋）",
             "着順_数値": i + 1, "人気": i + 2}
            for i, hid in enumerate(horses)
        ])
        history = pd.DataFrame([
            {"レースID": f"20230504111{i}", "馬ID": hid, "馬名": "甲" if i == 0 else "乙",
             "日付": "2023/05/01", "レース名": "前哨戦" if i == 0 else "別戦",
             "距離": "芝2000", "馬 場": "良", "開催": "東京",
             "着 順": "3", "上り": "34.0", "通過": "2-2-3",
             "人 気": "3", "頭 数": "12", "レース格": "G2",
             "格付け根拠": "G2"}
            for i, hid in enumerate(horses)
        ])
        app.session_state["v21_target_results"] = target
        app.session_state["v21_acquisition_log"] = pd.DataFrame([{
            "year": 2025, "race_id": rid, "status": "成功", "entrants": 2,
        }])
        app.session_state["v21_horse_raw"] = history
        app.session_state["v21_horse_log"] = pd.DataFrame([
            {"horse_id": hid, "status": "成功", "rows": 1} for hid in horses
        ])
        app.run()
        action = next(b for b in app.button if b.label == "全頭取得完了を検査して相性計算")
        action.click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue("v21_affinity_result" in app.session_state)
        self.assertFalse(app.session_state["v21_affinity_result"].empty)
        # A ZIP produced by the offline E2E contains converted rows, not the
        # raw netkeiba layout. A second click must not run convert_auto twice.
        from keiba_core import convert_auto
        app.session_state["v21_horse_raw"] = convert_auto(history)
        app.session_state["v21_affinity_result"] = None
        app.run()
        next(b for b in app.button if b.label == "全頭取得完了を検査して相性計算").click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue("v21_affinity_result" in app.session_state)
        self.assertFalse(app.session_state["v21_affinity_result"].empty)

    def test_csv_analysis_without_upload_shows_validation(self):
        page = Path(__file__).resolve().parents[1] / "pages/2_過去レース相性_Ver21.py"
        app = AppTest.from_file(str(page), default_timeout=25).run()
        action = next(x for x in app.button if x.label == "過去レース相性を計算")
        action.click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("CSVファイルを選択" in x.value
                            for x in app.error))



if __name__ == "__main__":
    unittest.main()
