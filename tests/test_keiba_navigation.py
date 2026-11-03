import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from keiba_navigation import DEPARTMENTS,DETAILS


class NavigationTests(unittest.TestCase):
    def test_unchanged_release_keeps_loaded_functions_for_reruns(self):
        import keiba_core as core
        from keiba_release import prepare_release
        first=prepare_release('22.2')
        converter=core.convert_auto
        second=prepare_release('22.2')
        self.assertIs(first,second)
        self.assertIs(core.convert_auto,converter)

    def test_cached_previous_menu_is_reloaded_once_for_new_release(self):
        import keiba_navigation as navigation
        import keiba_core as core
        import keiba_main_ranking as ranking
        original=navigation.render_navigation
        original_convert=core.convert_auto
        original_ranking=ranking.render_main_ranking
        def stale_function(*args,**kwargs):raise AssertionError('旧版の関数')
        marker=getattr(navigation,'_loaded_app_version',None)
        def stale_navigation():
            import streamlit as st
            st.sidebar.caption('競馬分析ラボ Ver.22.1')
            return '🏇 総合診断'
        try:
            navigation.render_navigation=stale_navigation
            core.convert_auto=stale_function
            ranking.render_main_ranking=stale_function
            navigation._loaded_app_version='22.1'
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            self.assertEqual(len(app.exception),0)
            self.assertIsNot(core.convert_auto,stale_function)
            self.assertIsNot(ranking.render_main_ranking,stale_function)
            self.assertTrue(any(c.value=='競馬分析ラボ Ver.22.2' for c in app.sidebar.caption))
            self.assertFalse(any(c.value=='競馬分析ラボ Ver.22.1' for c in app.sidebar.caption))
        finally:
            navigation.render_navigation=original
            core.convert_auto=original_convert
            ranking.render_main_ranking=original_ranking
            if marker is None:
                navigation.__dict__.pop('_loaded_app_version',None)
            else:navigation._loaded_app_version=marker

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
