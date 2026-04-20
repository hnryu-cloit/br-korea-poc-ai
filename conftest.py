from __future__ import annotations

import sys
from pathlib import Path

# pytest 실행 시 br-korea-poc-ai 루트를 PYTHONPATH에 추가
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
