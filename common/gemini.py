import os
import time
import base64
import csv
from datetime import datetime
from pathlib import Path
import threading
from io import BytesIO

from google import genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image

# logger는 파일 상단에서 한 번만 임포트합니다.
from common.logger import init_logger, timefn

# CSV 로깅을 위한 전역 변수
BILLING_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../results/billing.csv")
csv_lock = threading.Lock()

def encode_image_to_base64(file_path: str) -> str:
    """로컬 이미지 파일을 Base64로 인코딩하는 함수"""
    try:
        with open(file_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except FileNotFoundError:
        print(f"오류: 파일 '{file_path}'을(를) 찾을 수 없습니다.")
        return None

def load_image_bytes(file_path: str) -> bytes:
    """로컬 이미지 파일을 읽어 원본 바이트를 반환하는 함수"""
    try:
        with open(file_path, "rb") as image_file:
            return image_file.read()
    except FileNotFoundError:
        print(f"오류: 파일 '{file_path}'을(를) 찾을 수 없습니다.")
        return None

def log_gemini_call(function_name: str, model_name: str, prompt: str, response: str, status: str, api_call_count: int = 1):
    """
    Gemini API 호출 내역을 CSV 파일에 로깅하는 함수

    Args:
        function_name: 호출한 함수명
        model_name: 사용한 Gemini 모델명
        prompt: 입력 프롬프트 (긴 경우 요약)
        response: 응답 결과 (긴 경우 요약)
        status: 성공/실패 상태
        api_call_count: API 호출 횟수
    """

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # CSV 파일 경로 생성
    csv_path = Path(BILLING_CSV_PATH)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = csv_path.exists()

    with csv_lock:
        try:
            with open(csv_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)

                if not file_exists:
                    writer.writerow(['function', 'model_name', 'timestamp', 'prompt', 'response', 'status', 'api_call_count'])

                # 로그 데이터 작성
                writer.writerow([
                    function_name,
                    model_name,
                    timestamp,
                    prompt,
                    response,
                    status,
                    api_call_count
                ])
        except Exception as e:
            print(f"CSV 로깅 중 오류 발생: {e}")


class Gemini:

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv('API_KEY')

        # Developer API 클라이언트 (텍스트 및 이미지 생성용)
        self.client = genai.Client(api_key=self.api_key)

        self.model = "gemini-2.5-flash"
        self.image_model = "gemini-3.1-flash-image-preview"  # 이미지 생성 모델
        self.max_retries = 3
        self.initial_delay = 1

        # init_logger 사용
        self.logger = init_logger()

    def retry_with_delay(max_retries=None):
        """재시도 데코레이터 - 함수별로 다른 retry 횟수 지원"""
        def decorator(func):
            def wrapper(self, *args, **kwargs):
                retries = max_retries if max_retries is not None else self.max_retries
                delay = self.initial_delay
                api_call_count = 0

                for attempt in range(retries):
                    try:
                        api_call_count = attempt + 1
                        result = func(self, *args, api_call_count=api_call_count, **kwargs)
                        return result
                    except Exception as e:
                        if attempt == retries - 1:
                            raise e
                        self.logger.error(f"gemini 호출 {attempt + 1}번째 실패: {e}")
                        time.sleep(delay)
                        delay *= 2
            return wrapper
        return decorator

    @retry_with_delay(max_retries=3)
    @timefn
    def get_embeddings(self, text: str, model: str = "gemini-embedding-001", api_call_count=1) -> list[float]:
        """텍스트를 벡터로 변환하는 임베딩 메서드"""
        try:
            result = self.client.models.embed_content(
                model=model,
                contents=[text]
            )
            return result.embeddings[0].values
        except Exception as e:
            self.logger.error(f"임베딩 생성 실패: {e}")
            raise

    @retry_with_delay(max_retries=3)
    @timefn
    def call_gemini_image_text(self, prompt, image, text=None, response_type="application/json", api_call_count=1):
        """이미지와 텍스트를 함께 처리하는 함수"""
        response_text = None
        status = "실패"

        try:
            import os
            import tempfile
            import shutil
            # 한글 경로 문제를 해결하기 위해 임시 파일을 영문 이름으로 생성
            _, ext = os.path.splitext(image)
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                shutil.copy2(image, tmp_file.name)
                tmp_path = tmp_file.name
            try:
                target_image = self.client.files.upload(file=tmp_path)
            finally:
                os.unlink(tmp_path)

            contents = [prompt, target_image]
            if text:
                contents.append(text)

            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config={
                    "response_mime_type": response_type,
                    "temperature": 0,
                    "top_p": 1,
                    "top_k": 1,
                }
            )
            response_text = response.candidates[0].content.parts[0].text
            status = "성공"
            return response_text
        except Exception as e:
            try:
                error_message = str(e).encode('utf-8', 'replace').decode('utf-8')
            except Exception:
                error_message = repr(e)
            response_text = f"오류: {error_message}"
            self.logger.error(f"call_gemini_image_text 실패: {error_message}")
            raise
        finally:
            # 로깅
            prompt_str = f"{str(prompt)[:200]}... [이미지 포함]"
            log_gemini_call(
                function_name="call_gemini_image_text",
                model_name=self.model,
                prompt=prompt_str,
                response=response_text if response_text else "응답 없음",
                status=status,
                api_call_count=api_call_count
            )

    @retry_with_delay(max_retries=3)
    @timefn
    def call_generate_image(self, prompt, reference_image=None, product_images=None, aspect_ratio="1:1", resolution="1K", output_mime_type="image/png", system_prompt=None, api_call_count=1):
        """
        Gemini 이미지 생성 함수 (gemini-3-pro-image-preview 모델 사용)

        Args:
            prompt: 생성 프롬프트
            reference_image: 레퍼런스 이미지 경로 (선택)
            product_images: 제품 이미지 경로 리스트 (선택)
            aspect_ratio: 이미지 비율 (1:1, 16:9, 9:16, 4:3, 3:4)
            resolution: 해상도 (1K, 2K, 4K)
            output_mime_type: 출력 형식 (image/png, image/jpeg)
            system_prompt: 시스템 지시문 (선택)
            api_call_count: API 호출 횟수 (데코레이터에서 관리)

        Returns:
            bytes: 생성된 이미지 데이터
        """
        status = "실패"
        response_summary = "이미지 생성 실패"

        try:
            import os
            import mimetypes

            # Parts 리스트 구성
            parts = []

            # 레퍼런스 이미지 추가
            if reference_image:
                try:
                    with open(reference_image, "rb") as f:
                        image_bytes = f.read()
                        mime_type, _ = mimetypes.guess_type(reference_image)
                        if not mime_type:
                            mime_type = "image/png"
                        parts.append(types.Part.from_bytes(
                            data=image_bytes,
                            mime_type=mime_type
                        ))
                except Exception as e:
                    self.logger.error(f"레퍼런스 이미지 읽기 실패: {e}")
                    raise

            # 제품 이미지들 추가
            if product_images:
                for image_path in product_images:
                    try:
                        with open(image_path, "rb") as f:
                            image_bytes = f.read()
                            mime_type, _ = mimetypes.guess_type(image_path)
                            if not mime_type:
                                mime_type = "image/png"
                            parts.append(types.Part.from_bytes(
                                data=image_bytes,
                                mime_type=mime_type
                            ))
                    except Exception as e:
                        self.logger.error(f"제품 이미지 읽기 실패: {image_path}, {e}")
                        raise

            # 프롬프트 텍스트 추가
            parts.append(types.Part.from_text(text=prompt))

            # Content 구성
            contents = [types.Content(role="user", parts=parts)]

            # GenerateContentConfig 구성
            generate_content_config = types.GenerateContentConfig(
                temperature=1,
                top_p=0.95,
                max_output_tokens=32768,
                response_modalities=["TEXT", "IMAGE"],
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
                ],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=resolution,
                ),
            )

            if system_prompt:
                generate_content_config.system_instruction = system_prompt

            # 이미지 생성 (Developer API 클라이언트 사용)
            response = self.client.models.generate_content(
                model=self.image_model,
                contents=contents,
                config=generate_content_config,
            )

            # 응답 처리
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if part.text:
                                self.logger.info(f"Text Response: {part.text}")

                            if part.inline_data:
                                image_bytes = part.inline_data.data

                                # 검증
                                image = Image.open(BytesIO(image_bytes))
                                image.verify()

                                status = "성공"
                                response_summary = f"이미지 생성 완료 ({aspect_ratio}, {resolution})"
                                return image_bytes

            raise Exception("생성된 이미지가 응답에 없습니다.")

        except Exception as e:
            try:
                error_message = str(e).encode('utf-8', 'replace').decode('utf-8')
            except Exception:
                error_message = repr(e)
            response_summary = f"오류: {error_message}"
            self.logger.error(f"이미지 생성 실패: {error_message}")
            raise
        finally:
            prompt_str = f"{str(prompt)[:200]}..."
            if reference_image:
                prompt_str += " [레퍼런스 이미지 포함]"
            if product_images:
                prompt_str += f" [제품 이미지 {len(product_images)}개]"
            log_gemini_call(
                function_name="call_generate_image",
                model_name=self.image_model,
                prompt=prompt_str,
                response=response_summary,
                status=status,
                api_call_count=api_call_count
            )

    @retry_with_delay(max_retries=3)
    @timefn
    def call_gemini_text(self, prompt, system_instruction=None, response_type="application/json", api_call_count=1):
        """텍스트만 처리하는 함수 (시스템 프롬프트 지원 및 실행 시간 콘솔 출력 추가)"""
        response_text = None
        status = "실패"
        start_t = time.time()
        
        # MIME type 보정
        mime_type = response_type
        if mime_type == "text":
            mime_type = "text/plain"
        elif mime_type == "json":
            mime_type = "application/json"

        try:
            parts = [types.Part.from_text(text=prompt)]
            contents = [types.Content(parts=parts)]
            
            # config 구성
            config_args = {
                "response_mime_type": mime_type,
                "temperature": 0,
                "top_p": 1,
                "top_k": 1,
            }
            if system_instruction:
                config_args["system_instruction"] = system_instruction
                
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_args)
            )
            response_text = response.text
            status = "성공"
            
            elapsed = time.time() - start_t
            print(f"\n  [System] ⏱️ LLM(Gemini) 분석 생성 소요 시간: {elapsed:.2f}초")
            
            return response_text
        except Exception as e:
            try:
                error_message = str(e).encode('utf-8', 'replace').decode('utf-8')
            except Exception:
                error_message = repr(e)
            response_text = f"오류: {error_message}"
            self.logger.error(f"call_gemini_text 실패: {error_message}")
            raise
        finally:
            log_gemini_call(
                function_name="call_gemini_text",
                model_name=self.model,
                prompt=str(prompt),
                response=response_text if response_text else "응답 없음",
                status=status,
                api_call_count=api_call_count
            )

    @retry_with_delay(max_retries=3)
    @timefn
    def call_extract_metadata(self, content, response_type="application/json", api_call_count=1):
        """이미지와 텍스트를 함께 처리하는 함수"""
        response_text = None
        status = "실패"

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=content,
                config={
                    "response_mime_type": response_type,
                    "temperature": 0,
                    "top_p": 1,
                    "top_k": 1,
                }
            )
            response_text = response.candidates[0].content.parts[0].text
            status = "성공"
            return response_text
        except Exception as e:
            try:
                error_message = str(e).encode('utf-8', 'replace').decode('utf-8')
            except Exception:
                error_message = repr(e)
            response_text = f"오류: {error_message}"
            self.logger.error(f"call_extract_metadata 실패: {error_message}")
            raise
        finally:
            # 로깅
            content_str = f"{str(content)[:200]}... [content 포함]"
            log_gemini_call(
                function_name="call_extract_metadata",
                model_name=self.model,
                prompt=content_str,
                response=response_text if response_text else "응답 없음",
                status=status,
                api_call_count=api_call_count
            )