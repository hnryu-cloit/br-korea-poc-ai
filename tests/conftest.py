"""
테스트 환경 설정 - 무거운 외부 의존성(Gemini, PIL, vertexai)을 sys.modules에
stub으로 등록하여 실제 패키지 없이도 임포트가 통과되도록 합니다.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__spec__ = None  # type: ignore[attr-defined]
    return mod


# ── PIL ───────────────────────────────────────────────────────────────────────
_pil = _stub("PIL")
_pil.Image = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("PIL", _pil)
sys.modules.setdefault("PIL.Image", MagicMock())

# ── vertexai ──────────────────────────────────────────────────────────────────
_vertexai = _stub("vertexai")
_vertexai.init = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("vertexai", _vertexai)

# ── google.genai / google.genai.types ─────────────────────────────────────────
_google = sys.modules.get("google") or _stub("google")
sys.modules.setdefault("google", _google)

_genai = _stub("google.genai")
_genai.Client = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("google.genai", _genai)
if not hasattr(_google, "genai"):
    _google.genai = _genai  # type: ignore[attr-defined]

_genai_types = _stub("google.genai.types")
sys.modules.setdefault("google.genai.types", _genai_types)

# ── common.gemini — provide a lightweight stub ────────────────────────────────
_gemini_mod = _stub("common.gemini")
_gemini_mod.Gemini = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("common.gemini", _gemini_mod)

# ── common.logger ─────────────────────────────────────────────────────────────
_logger_mod = _stub("common.logger")
_logger_mod.timefn = lambda f: f  # type: ignore[attr-defined]
_logger_mod.init_logger = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("common.logger", _logger_mod)
sys.modules.setdefault("common", _stub("common"))

# ── services.* — stub all service modules so heavy infra deps aren't needed ──
_services_pkg = _stub("services")
sys.modules.setdefault("services", _services_pkg)

for _svc in (
    "sales_analyzer",
    "production_service",
    "ordering_service",
    "rag_service",
    "orchestrator",
    "generator",
    "predictor",
    "semantic_layer",
):
    _mod = _stub(f"services.{_svc}")
    # Add a mock class with the expected name
    _cls_name = "".join(w.capitalize() for w in _svc.split("_"))
    setattr(_mod, _cls_name, MagicMock())
    # Also expose SalesAnalyzer / ProductionService etc.
    sys.modules.setdefault(f"services.{_svc}", _mod)
    setattr(_services_pkg, _svc, _mod)

# Alias expected class names explicitly
sys.modules["services.sales_analyzer"].SalesAnalyzer = MagicMock()
sys.modules["services.production_service"].ProductionService = MagicMock()
sys.modules["services.ordering_service"].OrderingService = MagicMock()
sys.modules["services.rag_service"].RAGService = MagicMock()
sys.modules["services.orchestrator"].AgentOrchestrator = MagicMock()

# ── pipeline.run ──────────────────────────────────────────────────────────────
_pipeline_pkg = _stub("pipeline")
_pipeline_run = _stub("pipeline.run")
_pipeline_run.run_pipeline = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("pipeline", _pipeline_pkg)
sys.modules.setdefault("pipeline.run", _pipeline_run)