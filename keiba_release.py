"""Refresh function modules once per app release without touching saved data."""
import importlib
import sys
from pathlib import Path
from threading import RLock

_LOCK=RLock()
_KEY=None

# Keep the RaceConfig class and its existing preset instances stable.
MODULES=('keiba_fetch_policy','keiba_core','keiba_v22','keiba_v22_collect','keiba_race_metadata',
         'keiba_diagnosis_evidence','keiba_main_conditions','keiba_main_laps',
         'keiba_main_backup','keiba_main_integration','keiba_main_patterns',
         'keiba_main_ranking','keiba_reference_replay','keiba_workspace_ui','keiba_navigation','keiba_fetch_status_ui')


def prepare_release(version):
    global _KEY
    navigation=importlib.import_module('keiba_navigation')
    root=Path(__file__).resolve().parent
    fingerprints=[]
    for name in MODULES:
        path=root/(name+'.py')
        info=path.stat()
        fingerprints.append((name,info.st_mtime_ns,info.st_size))
    key=(version,tuple(fingerprints))
    with _LOCK:
        if _KEY==key and getattr(navigation,'_loaded_app_version',None)==version:
            return navigation
        importlib.invalidate_caches()
        for name in MODULES:
            module=sys.modules.get(name)
            if module is not None:
                importlib.reload(module)
        navigation=sys.modules['keiba_navigation']
        navigation._loaded_app_version=version
        _KEY=key
        return navigation
