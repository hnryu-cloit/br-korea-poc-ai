from . import gemini
from . import logger
from . import prompt

from .gemini import Gemini
from .logger import init_logger


def _noop_timefn(fn):
    return fn


timefn = getattr(logger, "timefn", _noop_timefn)

__all__ = ["gemini", "logger", "prompt", "Gemini", "init_logger", "timefn"]
