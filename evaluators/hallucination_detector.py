import re
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("hallucination_detector")

class HallucinationDetector:
    """
    AI가 생성한 텍스트(분석 사유, 설명 등)가 실제 원본 DB 데이터와 일치하는지 검증하는 클래스입니다.
    """

    @staticmethod
    def verify_numbers_rule_based(generated_text: str, ground_truth_numbers: Dict[str, int]) -> Dict[str, Any]:
        """
        [Rule-based 검증]
        정규식을 통해 텍스트 내의 모든 숫자를 추출하고, 필수 원본 숫자들이 모두 텍스트에 포함되어 있는지 검열합니다.
        
        :param generated_text: AI가 생성한 문장 (예: "현재 12개 → 1시간 후 2개 예상 · 지금 생산 시 찬스 로스 18% 감소")
        :param ground_truth_numbers: 실제 DB 값 (예: {"current_stock": 12, "forecast": 2, "chance_loss": 18})
        :return: 검증 통과 여부 및 상세 매칭 결과
        """
        # 텍스트에서 연속된 숫자들만 추출
        extracted_numbers_str = re.findall(r'\d+', generated_text)
        extracted_numbers = [int(n) for n in extracted_numbers_str]

        details = {}
        is_fully_consistent = True

        for key, expected_val in ground_truth_numbers.items():
            if expected_val in extracted_numbers:
                details[key] = {"expected": expected_val, "status": "PASS"}
            else:
                details[key] = {"expected": expected_val, "status": "FAIL (Not found or mismatched)"}
                is_fully_consistent = False

        return {
            "is_consistent": is_fully_consistent,
            "extracted_numbers": extracted_numbers,
            "details": details
        }

    @staticmethod
    async def evaluate_with_llm_judge(generated_text: str, raw_data_context: Dict[str, Any], ai_client) -> Dict[str, Any]:
        """
        [LLM-as-a-Judge 검증]
        Gemini 모델을 평가자(Judge)로 사용하여 생성된 문장의 논리적/수치적 정합성을 평가합니다.
        
        :param generated_text: 평가할 AI 생성 문장
        :param raw_data_context: 기준이 되는 원시 데이터 (Ground Truth)
        :param ai_client: Gemini API 호출용 클라이언트 인스턴스
        """
        prompt = f"""
        당신은 엄격하고 객관적인 데이터 검증관(Data Validation Judge)입니다.
        아래 제공된 [원본 데이터]를 바탕으로 [생성된 문장]에 허위 사실(Hallucination), 과장, 또는 잘못된 수치가 포함되어 있는지 평가하세요.

        [원본 데이터 (Ground Truth)]
        {json.dumps(raw_data_context, ensure_ascii=False, indent=2)}

        [생성된 문장]
        "{generated_text}"

        평가 기준:
        1. 생성된 문장의 모든 숫자가 원본 데이터와 100% 일치하는가?
        2. 원본 데이터에 없는 정보를 지어내어 과장하지 않았는가?
        3. 논리적인 인과관계가 원본 데이터를 훼손하지 않는가?

        다음 JSON 형식으로만 응답하세요:
        {{
            "is_consistent": true/false,
            "confidence_score": 0.0 ~ 1.0 (1.0이 완벽하게 일치함),
            "reason": "검증에 대한 상세 사유 (어떤 숫자가 틀렸는지, 왜 점수를 깎았는지)"
        }}
        """

        try:
            import asyncio
            # call_gemini_text is sync, so we run it in a thread
            response_text = await asyncio.to_thread(ai_client.call_gemini_text, prompt)
            
            # JSON 포맷 파싱 (Markdown 코드블럭 제거 등)
            clean_json = response_text.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean_json)
            return result
            
        except Exception as e:
            logger.error(f"LLM Judge 평가 중 오류 발생: {e}")
            return {
                "is_consistent": False,
                "confidence_score": 0.0,
                "reason": f"평가 실패: {str(e)}"
            }
