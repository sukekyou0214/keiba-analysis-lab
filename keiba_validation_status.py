"""モデルの検証状況（docs/accuracy/status.json）を画面に出す。数値は検証スクリプトの結果だけを書く。"""
import json
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parent / 'docs' / 'accuracy' / 'status.json'


def load_status(path=STATUS_PATH):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def status_text(mode, status=None):
    status = status if status is not None else load_status()
    model = status.get('モデル', {}).get(mode)
    if not model:
        return '検証状況：記録なし'
    parts = [f"{v['区分']} {v['期間']} {v['レース数']}R：上位3で勝ち馬 {v['上位3勝ち馬']}" for v in model.get('検証', [])]
    parts.append('未使用期間での検証：' + str(model.get('未使用期間', '未実施')))
    return '検証状況（' + model.get('名称', mode) + '）：' + ' ／ '.join(parts)
