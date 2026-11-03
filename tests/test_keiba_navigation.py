import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from keiba_navigation import DEPARTMENTS,DETAILS


class NavigationTests(unittest.TestCase):
    def test_three_departments_and_optional_management_preserve_existing_views(self):
        app=AppTest.from_string('from keiba_navigation import render_navigation\nimport streamlit as st\nst.write(render_navigation())').run()
        self.assertEqual(app.radio(key='nav_department_v22').options,DEPARTMENTS)
        app.radio(key='nav_department_v22').set_value('🔍 詳細分析').run()
        for detail,legacy in DETAILS.items():
            app.radio(key='nav_detail_v22').set_value(detail).run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(m.value==legacy for m in app.markdown))
        app.radio(key='nav_department_v22').set_value('⚙️ 詳細設定').run()
        self.assertTrue(any(m.value=='⚙️ 分析条件' for m in app.markdown))

    def test_original_main_switches_all_departments_without_data_or_source_calls(self):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
        self.assertEqual(len(app.exception),0)
        for department in DEPARTMENTS:
            app.radio(key='nav_department_v22').set_value(department).run()
            self.assertEqual(len(app.exception),0,department)
            if department=='🔍 詳細分析':
                for detail in DETAILS:
                    app.radio(key='nav_detail_v22').set_value(detail).run()
                    self.assertEqual(len(app.exception),0,detail)
