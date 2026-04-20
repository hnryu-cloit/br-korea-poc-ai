from __future__ import annotations

from common.gemini import Gemini
from common.logger import init_logger

logger = init_logger("evaluator")


class QualityEvaluator:
    """
    AI 답변 품질 평가기:
    1. 답변이 제공된 근거(Context) 내에 존재하는지 확인 (Faithfulness)
    2. 답변이 질문과 관련이 있는지 확인 (Relevancy)
    3. Gemini를 평가자로 활용하는 'LLM-as-a-Judge' 기법 적용
    """

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client

    def evaluate_response(self, query: str, response: str, context: list[str]) -> float:
        """답변의 신뢰도 점수를 0.0 ~ 1.0 사이로 산출합니다."""
        if not context:
            return 1.0  # 컨텍스트가 없는 일반 응답은 일단 패스

        eval_prompt = f"""
        당신은 AI 품질 평가 전문가입니다. 아래 질문, 답변, 근거 문서를 바탕으로 답변의 '신뢰도' 점수를 0.0에서 1.0 사이로 매기세요.
        
        [질문]: {query}
        [답변]: {response}
        [근거 문서]: {chr(10).join(context)}
        
        평가 기준:
        - 답변이 근거 문서에 있는 내용인가? (Hallucination 여부)
        - 답변이 질문에 대해 적절하고 유용한가?
        
        오직 점수(숫자)만 답변하세요.
        """

        try:
            score_str = self.gemini.call_gemini_text(eval_prompt, response_type="text")
            # 숫자만 추출
            score = float("".join(c for c in score_str if c.isdigit() or c == "."))
            logger.info(f"AI 응답 품질 측정 완료: {score:.2f}")
            return score
        except Exception as e:
            logger.error(f"품질 평가 실패: {e}")
            return 0.5  # 실패 시 중간 점수 부여
