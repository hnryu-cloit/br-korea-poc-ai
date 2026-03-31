from __future__ import annotations
from typing import Any, Dict

from common.logger import init_logger
from common.gemini import Gemini
from services.orchestrator import AgentOrchestrator

logger = init_logger("pipeline")


async def run_pipeline(prompt: str, context: Dict[str, Any] | None = None) -> dict[str, object]:
    """
    Main entry point for AI Pipeline execution.
    Orchestrates the process based on natural language input.
    """
    logger.info("Pipeline execution starting with prompt: %s", prompt[:50])
    
    gemini = Gemini()
    orchestrator = AgentOrchestrator(gemini)
    
    # Process the request through the orchestrator
    result = await orchestrator.handle_request(prompt, context)
    
    # Ensure result is a dictionary for the response
    if hasattr(result, "model_dump"):
        return result.model_dump()
    elif isinstance(result, dict):
        return result
    else:
        return {"text": str(result)}
