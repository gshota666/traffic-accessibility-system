from pathlib import Path
import os
import sys
from platformdirs import user_data_path


def resource(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / name


def data_dir(override=None):
    path = Path(override or os.environ.get('TRAFFIC_DATA_DIR') or user_data_path('TrafficAccessibility', appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    for child in ('uploads', 'exports', 'logs'):
        (path / child).mkdir(exist_ok=True)
    return path
