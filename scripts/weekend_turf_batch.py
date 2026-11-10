"""CLI: python scripts/weekend_turf_batch.py --dates 20261114 20261115 --work DIR --out DIR"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keiba_weekend_batch import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
