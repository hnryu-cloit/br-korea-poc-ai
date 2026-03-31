
# -----------------------------------------------------------------------------
# Metadata Extraction Prompt Templates
# -----------------------------------------------------------------------------

METADATA_CATEGORY_PROMPT_TEMPLATE = """
        제시된 이미지를 분석하여 정확한 카테고리를 결정해주세요.
        이미지의 제품이 속하는 가장 정확한 카테고리를 선택하고 세부적인 이유와 특징을 설명해주세요.
        여러 카테고리의 특성이 혼합되어 있다면 가장 두드러진 주요 카테고리 하나만 선택해주세요.

        ### 분석 가이드
        1. **주요 제품 식별**: 이미지에서 가장 중심이 되는 제품을 파악
        2. **형태적 특성 분석**: 제품의 모양, 구조, 디자인적 특징 관찰
        3. **기능적 용도 파악**: 제품의 실제 사용 목적과 착용 방식 고려
        4. **착용 부위 확인**: 신체의 어느 부분에 착용되는지 명확히 구분
        5. **최종 카테고리 결정**: 위의 요소들을 종합하여 가장 적합한 단일 카테고리 선택

        ## 카테고리 옵션
        {product_categories}

        ### 우선순위 원칙
        - **복수 제품 존재 시**: 이미지 중앙의 가장 큰 제품을 기준으로 분류한다.
        - **경계선상 제품**: 더 구체적이고 세분화된 카테고리를 선택한다.
        - **애매한 경우**: 해당 제품의 **주요 기능**을 우선 고려한다.

        ### 주의사항
        - 반드시 **단일 카테고리**만 선택
        - 카테고리명은 제공된 옵션과 **정확히 일치**해야 함
        - 추측이나 가정이 아닌 **시각적 증거**에 근거한 판단
        - 브랜드나 가격이 아닌 **제품 자체의 특성**으로 분류

        **중요**: "category"는 반드시 {category_keys} 중 하나여야 하며
        "sub_category"는 해당 카테고리의 하위 분류 {category_values} 중에서 선택해주세요.

        응답 형식(반드시 Json으로 반환한다.):
        {{
            "category": "선택된 카테고리명",
            "sub_category": "세부 카테고리명",
            "confidence": 0.0 ~ 1.0,
            "reason": "선택 근거를 구체적 설멍(제품의 형태, 특징, 용도 등 상세 이유)",
            "key_feature": [
                "특징1: 구체적 관찰 내용",
                "특징2: 구체적 관찰 내용",
                "특징3: 구체적 관찰 내용"
            ]
        }}
        """

METADATA_ATTRIBUTE_PROMPT_TEMPLATE = """
        이미지를 보고 해당 {category} 제품의 카테고리별 속성을 분석해주세요.

        ### 분석할 {category} 전용 속성들:
        {attributes_config}

        ### 분석 가이드
        - 각 속성에 대해 위의 옵션들 중에서 가장 적합한 값을 선택해주세요.
        - 빈 리스트([])인 경우 자유롭게 값을 입력해주세요.
        - 시각적 증거를 바탕으로 정확한 판단을 해주세요.
        - 불확실한 경우 confidence를 낮게 설정해주세요.

        응답 형식 (JSON):
        {{
            "속성명1": {{
                "value": "선택된 값",
                "confidence": 0.0,
                "reason": "선택 이유 (구체적 관찰 내용)"
            }},
            "속성명2": {{
                "value": "선택된 값",
                "confidence": 0.0,
                "reason": "선택 이유 (구체적 관찰 내용)"
            }}
        }}
        """

COMMON_ATTRIBUTE_PROMPT_TEMPLATE = """
        제시된 제품 이미지에서 상품의 핵심 속성을 정확히 분석해주세요.
        ### 분석 가이드
        1. **색상(Color)**: 가장 주요하고 눈에 띄는 색상을 선택하세요.
        2. **패턴(Pattern)**: 제품 표면의 디자인 패턴을 정확히 식별하세요.
        3. **소재(Material)**: 제품의 질감과 표면을 보고 소재를 추정하세요.
        4. **스타일(Style)**: 전체적인 디자인 스타일을 분석하세요.

        ### 색상(Color) 옵션: {colors}
        ** 대표 색상 값 **
        - 블랙, 화이트, 베이지, 네이비, 카키, 그레이, 브라운, 레드, 옐로우, 블루, 핑크, 퍼플, 그린, 오렌지
        ** 분석 방법 **
        - 제품 전체 면적의 50% 이상을 차지하는 주색상 우선 식별
        - 패턴/프린트가 있는 경우 바탕색(베이스 컬러) 기준 판단
        - 조명 효과 제거 후 실제 색상 추정
        - 그라데이션/옴브레 효과가 있는 경우 가장 넓은 면적의 색상 선택
        - 색상의 톤(따뜻함/차가움), 명도(밝기), 채도(선명도) 종합 고려

        ### 패턴(Pattern) 옵션: {patterns}
        ## 패턴별 상세 설명 ##
        - 무지: 패턴 없이 단색 디자인
        - 우드그레인: 나뭇결 무늬가 강조된 패턴
        - 마블: 대리석 질감 무늬
        - 라탄·위빙: 짜임무늬가 드러나는 라탄/위빙 패턴
        - 패브릭 텍스처: 패브릭 고유의 직조 무늬
        - 그래픽: 개성 있는 문양이나 프린트가 들어간 디자인

        ### 스타일(Style) 옵션: {styles}
        ## 스타일별 특징 ##
        - 모던: 깔끔하고 세련된 도시적
        - 클래식: 전통적이고 우아한 분위기
        - 빈티지: 레트로 감성
        - 미니멀: 군더더기 없는 단순함
        - 내추럴: 원목·라탄 등 자연스러운 무드
        - 럭셔리: 대리석, 금속 등 고급스러운 느낌
        - 인더스트리얼: 철제·시멘트 소재 기반의 거친 감성
        - 북유럽: 스칸디나비아풍, 따뜻한 우드톤
        - 러블리: 밝고 아기자기한 느낌

        ### 타겟 고객 옵션: {target_customers}
        ## 타겟 고객별 특징 ##
        - 싱글: 개인 거주자 대상
        - 신혼부부: 신혼 가정 대상
        - 가족: 일반 가족 단위 대상
        - 아이 있는 가정: 자녀가 있는 가정 대상

        ### 타겟 연령층 옵션: {target_ages}
        ## 연령층별 특징 ##
        - 전체 연령: 나이 제한 없음
        - 20대: 젊은 층 대상
        - 30대: 중장년층 초기 대상
        - 40대: 중년층 대상
        - 50대 이상: 중장년층 이상 대상

        응답 형식(반드시 Json으로 반환한다.):
        {{
            "스타일": {{
                "value": "선택된_스타일",
                "confidence": 0.0 ~ 1.0,
                "reason": "스타일 판단 근거 (실루엣, 여유감, 맞음새, 전체적 비율 등)"
            }},
            "타겟 고객": {{
                "value": "선택된 타겟 고객",
                "confidence": 0.0 ~ 1.0,
                "reason": "선택 이유"
            }},
            "타겟 연령층": {{
                "value": "선택된 연령층",
                "confidence": 0.0 ~ 1.0,
                "reason": "선택 이유"
            }},
            "색상": {{
                "value": "선택된_색상",
                "confidence": 0.0 ~ 1.0,
                "reason": "색상 선택 근거(관찰된 주요 색조, 명도, 채도, 면적 비율 등)"
            }},
            "무늬": {{
                "value": "선택된_패턴",
                "confidence": 0.0 ~ 1.0,
                "reason": "패턴 식별 근거(패턴 형태, 크기, 배치, 반복성, 색상 조합 등)"
            }}
        }}
        """

PRODUCT_DESCRIPTION_PROMPT_TEMPLATE = """
        제공된 제품 속성 정보를 바탕으로 고객의 시선을 사로잡는 매력적인 상품 설명을 생성해주세요.
        ### 생성 가이드
        1. 제품의 핵심 매력을 간결하고 임팩트 있는 문장 묘사
        2. 속성 정보를 활용하여 제품의 특징과 장점을 구체적으로 묘사
        3. 제품이 제공하는 가치나 고객 경험을 강조

        ### 제품 속성 정보
        {attributes}

        응답 형식(반드시 Json으로 반환 한다.):
        {{
            "description": ""
        }}
        """

# -----------------------------------------------------------------------------
# Metadata Extraction Functions
# -----------------------------------------------------------------------------

def create_product_category_prompt(product_categories: str, category_keys: list, category_values: list) -> str:
    """
    제품 카테고리 분석 프롬프트 생성
    """
    return METADATA_CATEGORY_PROMPT_TEMPLATE.format(
        product_categories=product_categories,
        category_keys=category_keys,
        category_values=category_values
    )

def create_product_attribute_prompt(category: str, attributes_config: str) -> str:
    """
    제품 카테고리별 속성 분석 프롬프트 생성
    """
    return METADATA_ATTRIBUTE_PROMPT_TEMPLATE.format(
        category=category,
        attributes_config=attributes_config
    )

def create_common_attribute_prompt(colors: list, patterns: list, styles: list, target_customers: list, target_ages: list) -> str:
    """
    공통 속성 분석 프롬프트 생성
    """
    return COMMON_ATTRIBUTE_PROMPT_TEMPLATE.format(
        colors=colors,
        patterns=patterns,
        styles=styles,
        target_customers=target_customers,
        target_ages=target_ages
    )

def create_product_description_prompt(attributes: str) -> str:
    """
    제품 설명 생성 프롬프트 생성
    """
    return PRODUCT_DESCRIPTION_PROMPT_TEMPLATE.format(
        attributes=attributes
    )

# -----------------------------------------------------------------------------
# Existing Prompt Functions
# -----------------------------------------------------------------------------

def create_space_analysis_prompt(reference_image_path: str) -> str:
    """
    공간 분석 프롬프트 생성

    Args:
        reference_image_path: 레퍼런스 이미지 경로

    Returns:
        공간 분석 프롬프트
    """
    return """
    당신은 전문 인테리어 가구 디자이너입니다. 제공된 공간 이미지를 분석하여 상세한 정보를 추출하세요.
    
    **분석할 항목:**
    
    1. **공간 타입**: 거실, 침실, 서재, 사무실, 카페 등
    2. **공간 크기**: small (10㎡ 이하), medium (10-20㎡), large (20㎡ 이상)
    3. **조명 환경**:
       - 광원 방향: top-left, top-right, top-center, front, side
       - 조명 종류: natural (자연광), artificial (인공 조명), mixed (혼합)
       - 조명 강도: 0.0 (어두움) ~ 1.0 (매우 밝음)
       - 색온도: warm (따뜻한 색), neutral (중성), cool (차가운 색)
    4. **공간 분위기**: modern_warm, modern_cool, classic, minimalist, industrial, scandinavian, luxury
    5. **색조**: neutral_beige, warm_wood, cool_gray, white_minimal, earth_tone
    6. **바닥 재질**: wood_flooring, tile, carpet, concrete, marble
    7. **벽 색상**: white, beige, gray, colored
    8. **주요 가구 배치**: 기존 가구들의 위치와 스타일

    **출력 형식 (JSON):**
    ```json
    {
      "space_type": "거실",
      "size": "medium",
      "lighting": {
        "direction": "top-left",
        "type": "natural",
        "intensity": 0.8,
        "temperature": "warm"
      },
      "atmosphere": "modern_warm",
      "color_tone": "neutral_beige",
      "floor_material": "wood_flooring",
      "wall_color": "white",
      "existing_furniture": "소파, 커피테이블, 사이드테이블",
      "design_style": "모던 스칸디나비안"
    }
    ```
    
    **주의사항:**
    - 정확하고 객관적으로 분석하세요.
    - JSON 형식만 출력하고 다른 설명은 포함하지 마세요.
    """


def create_styling_cut_prompt(reference_desc: str, products: list, style: str = "포토리얼리스틱", aspect_ratio: str = "1:1", generation_modes: list = None) -> str:
    """
    스타일링 컷 생성 프롬프트

    Args:
        reference_desc: 레퍼런스 공간 설명
        products: 배치할 제품 리스트 (name, placement, size, image_path)
        style: 렌더링 스타일 (기본값: "포토리얼리스틱")
        aspect_ratio: 이미지 비율 (기본값: "1:1")
        generation_modes: 생성 모드 리스트 (예: ['bg_style_separation', 'style_transfer'])

    Returns:
        스타일링 컷 프롬프트
    """
    if generation_modes is None:
        generation_modes = ["style_transfer"]

    # 제품 배치를 자연스러운 설명으로 변환
    products_desc_list = []
    for idx, p in enumerate(products, 1):
        placement = p.get('placement', '중앙')
        size = p.get('size', '보통')
        products_desc_list.append(
            f"{idx}. {p['name']} - 배치: {placement}, 크기: {size} (이 제품의 원본 형태와 디자인은 절대 수정하지 마세요)"
        )

    products_desc = "\n".join(products_desc_list)

    # 선택된 모드에 따라 추가 instruction 생성
    mode_instructions = []
    if "bg_style_separation" in generation_modes:
        mode_instructions.append("""
        - **배경/스타일 분리 모드**: 레퍼런스 이미지에서 배경, 조명, 스타일만 추출하고, 제품은 완전히 새롭게 배치하세요. 레퍼런스 공간의 물리적 구조는 사용하지 말고, 오직 배경의 색감과 조명 분위기만 참고하여 제품을 위한 새로운 환경을 만드세요.
        """)
    if "product_replacement" in generation_modes:
        mode_instructions.append("""
        - **제품 교체 모드**: 레퍼런스 이미지의 기존 가구/제품 위치를 파악하고, 그 위치에 새로운 제품을 정확히 대체하세요. 레퍼런스 공간의 구조, 배경, 조명은 최대한 유지하되 제품만 교체합니다. (inpainting/outpainting 방식)
        """)
    if "style_transfer" in generation_modes:
        mode_instructions.append("""
        - **스타일 전이 모드**: 레퍼런스 이미지의 스타일(색감, 조명, 분위기)은 참고하되, 제품의 고유한 특성과 매력을 최대한 강조하세요. 제품이 주인공이 되도록 구도를 잡고, 레퍼런스는 스타일 가이드로만 활용합니다.
        """)
    if "new_composition" in generation_modes:
        mode_instructions.append("""
        - **새로운 구도 모드**: 레퍼런스는 영감으로만 참고하고, 제품에 가장 적합한 완전히 새로운 구도와 공간을 창조하세요. 제품의 특성을 고려하여 가장 매력적인 시각적 구성을 자유롭게 디자인합니다.
        """)

    mode_instructions_text = "\n".join(mode_instructions) if mode_instructions else ""

    base_prompt = f"""
        당신은 전문 가구 스타일링 포토그래퍼입니다. 첫 번째 레퍼런스 공간 이미지를 참고하여, 두 번째 이미지부터 주어지는 제품들을 가장 잘 표현하는 고품질 스타일링 사진을 생성하세요.

        **레퍼런스 공간 정보:**
        첫 번째 이미지는 생성할 이미지의 '스타일 레퍼런스'입니다. 이 이미지를 분석하여 새로운 이미지에 반영하세요.
        {reference_desc}

        **배치할 제품들 (수정 불가):**
        두 번째 이미지부터는 스타일링의 주인공이 될 가구 제품들입니다. **이 제품들의 원본 형태, 디자인, 색상은 절대 변경하면 안 됩니다.**
        {products_desc}

        **생성 모드별 지침:**
        {mode_instructions_text}

        **작업 요구사항:**

        1. **제품 중심 배치**: 주어진 제품들이 가장 매력적으로 보일 수 있도록 배치하세요.
        2. **사실적인 환경 조성**: 조명, 그림자, 원근감을 사실적으로 구현하세요.
        3. **제품 무결성 유지**: **가장 중요한 규칙입니다. 제공된 제품 이미지를 변형, 왜곡, 수정하지 말고 그대로 사용하세요.**
        4. **고품질 포토리얼리즘**: 제품의 재질감, 색상, 디테일을 최고 품질로 렌더링하세요.

        **최종 출력 설정:**
        - 스타일: {style}
        - 이미지 비율: {aspect_ratio}

        이 이미지는 프리미엄 가구 카탈로그와 온라인 쇼핑몰에 사용될 것입니다. 최고 수준의 크리에이티브와 사실감으로 제작하세요.
        """

    return base_prompt


def create_product_shot_prompt(product_name: str, fabric: str, angle: str, background_type: str = "studio_white", has_fabric_reference: bool = False, aspect_ratio: str = "1:1", resolution: str = "2K", file_format: str = "PNG", rendering_style: str = "포토리얼리스틱 (Photorealistic)", lighting_style: str = "3점 조명법") -> str:
    """
    단품 컷 생성 프롬프트

    Args:
        product_name: 제품명
        fabric: 패브릭 종류
        angle: 촬영 각도
        background_type: 배경 타입
        has_fabric_reference: 소재 참조 이미지 포함 여부
        aspect_ratio: 이미지 비율
        resolution: 결과물 해상도 (예: "2K")
        file_format: 파일 형식 (예: "PNG", "JPG")
        rendering_style: 렌더링 스타일 (기본값: "포토리얼리스틱 (Photorealistic)")
        lighting_style: 조명 스타일 (기본값: "3점 조명법")

    Returns:
        단품 컷 프롬프트
    """
    angle_details = {
        "front": {
            "name": "정면",
            "camera": "제품 정면에서 수평으로, 고정된 거리 유지",
            "focus": "전체적인 형태와 정면 디자인",
        },
        "half_side": {
            "name": "45도 측면",
            "camera": "제품 중심을 기준으로 정확히 45도 회전된 각도, 고정된 거리 유지",
            "focus": "입체감과 측면 디테일",
        },
        "side": {
            "name": "90도 측면",
            "camera": "제품 중심을 기준으로 정확히 90도 회전된 각도, 고정된 거리 유지",
            "focus": "측면 프로파일과 두께감",
        },
        "back_side": {
            "name": "135도 후측면",
            "camera": "제품 중심을 기준으로 정확히 135도 회전된 각도, 고정된 거리 유지",
            "focus": "후면 디자인과 마감",
        },
        "back": {
            "name": "후면",
            "camera": "제품 중심을 기준으로 정확히 180도 회전된 각도, 고정된 거리 유지",
            "focus": "후면 디자인과 구조",
        }
    }

    angle_info = angle_details.get(angle, angle_details["front"])

    fabric_desc = ""
    fabric_reference_section = ""

    if fabric:
        fabric_desc = f"\n- 소재/패브릭: {fabric}"
        if has_fabric_reference:
            fabric_reference_section = f"""

    **소재 참조:**
        첫 번째 이미지는 {fabric} 소재의 참조 이미지입니다.
        - 이 소재 이미지의 질감, 색상, 패턴을 정확히 분석하세요
        - 제품 이미지(두 번째 이미지 이후)에 이 소재의 느낌을 정확하게 적용하세요
        - 소재의 직조 패턴, 색감, 광택도를 그대로 재현하세요
    """
        else:
            fabric_desc += " (질감과 색상을 정확하게 표현)"

    return f"""
    당신은 전문 제품 사진 작가입니다. 가구 제품 카탈로그에 사용될 고품질 스튜디오 촬영 이미지를 생성하세요.
    해당 이미지들은 향후 Structure from Motion (SfM) 기술을 사용하여 3D 모델을 생성하는 데 사용될 예정입니다. 따라서 모든 렌더링 결과물은 동일한 가상 카메라 시점에서 촬영된 것처럼 일관성이 있어야 합니다.

    {fabric_reference_section}

    **제품 정보:**
    - 제품명: {product_name}{fabric_desc}
    - 촬영 각도: {angle_info['name']} ({angle})

    **촬영 세팅 요구사항:**
    1. **카메라 설정**
       - **일관된 시점**: 모든 각도의 결과물은 동일한 가상 카메라(동일한 렌즈, 동일한 거리)로 제품 중심을 촬영한 것처럼 보여야 합니다.
         피사체(제품)만 중심축을 기준으로 회전해야 합니다.
       - 위치: {angle_info['camera']}
       - 렌즈: 50mm 표준 렌즈 (왜곡 최소화)
       - 피사계 심도: 제품 전체에 선명한 초점(Deep Depth of Field)
    
    2. **조명 설정(일관성 유지)**
       - 조명은 월드 좌표계에 고정되어 있고, 제품만 회전해야 합니다.
       - 조명 스타일: {lighting_style}
       - 조명 품질: 부드럽고 확산된 빛 (소프트박스 또는 스카이라이트 HDRI 사용)

    3. **배경 및 환경**
       - 타입: {background_type}
       - 깨끗한 무한 배경 (seamless cyclorama background)
       - 바닥과 벽의 경계선이 보이지 않게 부드럽게 처리
       - 그림자: 사실적이지만 부드럽고 연한 그림자 (Ambient Occlusion 포함)

    4. **제품 렌더링 품질**
       - 렌더링 스타일: {rendering_style}
       - 재질: 패브릭, 가죽, 목재 등 재질감을 극도로 사실적으로 표현
       - 색상 정확도: 실제 제품 색상과 완벽히 일치
       - 디테일: 봉제선, 단추, 다리 마감 등 모든 디테일이 선명하게 보여야 함
       - 상태: 먼지, 주름, 오염 없이 완벽한 새 제품 상태
    
    5. **기술 사양**
       - 결과물 비율: {aspect_ratio}
       - 해상도: {resolution}
       - 파일 형식: {file_format}
    
    **최종 목표:**
    1. 이커머스 상세 페이지 및 3D 모델 생성을 위한 고품질 에셋 확보해야 한다.
    2. 제품의 실제 모습을 정확하게 전달해야 한다. 
    3. 모든 각도의 이미지가 완벽하게 일관성을 유지하는 것이 가장 중요하다.
    """


def create_background_removal_prompt(instruction: str = "배경을 완전히 제거하고 제품만 남기세요.") -> str:
    """
    배경 제거 프롬프트

    Args:
        instruction: 추가적인 배경 제거 지침

    Returns:
        배경 제거 프롬프트
    """
    return f"""
    제공된 제품 이미지에서 다음 지침에 따라 배경을 처리하세요:
    
    **지침:**
    {instruction}

    **상세 요구사항:**
    1. 제품의 가장자리를 정확하고 부드럽게 추출
    2. 머리카락이나 섬유질 같은 복잡한 가장자리도 정밀하게 처리
    3. 배경은 투명하게(알파 채널) 또는 깨끗한 단색으로 처리
    4. 제품의 색상이나 디테일 손실 없이 유지
    5. 불필요한 그림자는 제거하고, 지면과 맞닿는 부분의 아주 자연스러운 접촉 그림자만 남김
    
    결과물은 고품질의 투명 배경 PNG 또는 깔끔한 스튜디오 배경 이미지여야 합니다.
    """


def create_detail_shot_prompt(product_name: str, fabric: str, feature: str, resolution: str = "2K", aperture: str = "F/2.8-5.6", reference_image_path: str = None) -> str:
    """
    디테일 컷 생성 프롬프트

    Args:
        product_name: 제품명
        fabric: 패브릭 종류
        feature: 강조할 특징
        resolution: 결과물 해상도 (기본값: "2K")
        aperture: 조리개 값 (기본값: "F/2.8-5.6")
        reference_image_path: 구도 레퍼런스 이미지 경로 (있을 경우)

    Returns:
        디테일 컷 프롬프트
    """
    feature_guides = {
        "fabric_texture": {
            "name": "패브릭 질감",
            "focus": "직물의 짜임, 올의 방향, 표면 질감",
            "distance": "15-20cm 거리의 클로즈업",
            "lighting": "비스듬한 각도의 조명으로 질감 강조",
            "details": "실의 굵기, 직조 패턴, 색상의 깊이감"
        },
        "cushion": {
            "name": "쿠션감과 볼륨",
            "focus": "쿠션의 부드러운 곡선과 충전감",
            "distance": "30-40cm 거리",
            "lighting": "측면 조명으로 입체감 강조",
            "details": "봉제선, 쿠션 모서리 처리, 탄력감"
        },
        "stitching": {
            "name": "봉제선 디테일",
            "focus": "스티치의 정교함과 마감",
            "distance": "10-15cm 매크로 촬영",
            "lighting": "정밀한 조명으로 실선 하나하나 보이도록",
            "details": "스티치 간격, 실의 색상, 마감 처리"
        },
        "leg_design": {
            "name": "다리 디자인과 마감",
            "focus": "다리의 형태, 소재, 마감 품질",
            "distance": "전체 다리가 들어오는 거리",
            "lighting": "목재/금속 질감이 돋보이는 조명",
            "details": "나뭇결/금속 광택, 연결부 마감, 바닥 접촉부"
        },
        "armrest": {
            "name": "팔걸이 곡선과 구조",
            "focus": "팔걸이의 인체공학적 곡선",
            "distance": "팔걸이 전체가 보이는 각도",
            "lighting": "곡선미가 돋보이는 측면 조명",
            "details": "곡선의 흐름, 마감 처리, 본체 연결부"
        },
        "backrest": {
            "name": "등받이 형태와 디테일",
            "focus": "등받이의 각도와 편안함",
            "distance": "등받이 전체 또는 주요 부분",
            "lighting": "입체감을 강조하는 조명",
            "details": "쿠션 배치, 지지 구조, 마감 처리"
        }
    }

    guide = feature_guides.get(feature, {
        "name": feature,
        "focus": "해당 부분의 디테일",
        "distance": "적절한 클로즈업 거리",
        "lighting": "디테일이 잘 보이는 조명",
        "details": "모든 세부 사항"
    })

    fabric_info = f"- 소재: {fabric}\n" if fabric else ""

    reference_instruction = ""
    if reference_image_path:
        reference_instruction = """
        **구도 레퍼런스 적용 (최우선 순위):**
        첫 번째 이미지는 '구도 레퍼런스'입니다. 생성할 디테일 컷은 반드시 이 레퍼런스 이미지의 **구도, 촬영 각도, 줌 레벨, 프레이밍**을 정확히 따라야 합니다.
        - 레퍼런스 이미지의 피사체가 무엇이든 상관없이, 그 '보는 방식(시각적 구조)'을 제품에 적용하세요.
        - 제품의 해당 부위가 레퍼런스와 동일한 화면 점유율을 가져야 합니다.
        - 만약 레퍼런스가 극단적인 클로즈업이라면 똑같이 클로즈업하고, 여백이 있다면 똑같이 여백을 주세요.
        """

    return f"""
        당신은 전문 제품 매크로 포토그래퍼입니다. 제품의 특정 부분을 강조한 고품질 디테일 사진을 생성하세요.
        
        {reference_instruction}

        **제품 정보:**
        - 제품명: {product_name}
        {fabric_info}- 촬영 부위: {guide['name']}
        
        **촬영 가이드:**
        
        1. **구도**
           - 초점: {guide['focus']}
           - 거리: {guide['distance']}
           - 프레이밍: 강조할 부분이 화면의 60-70% 차지
           - 배경: 자연스럽게 흐릿하게 (얕은 피사계 심도)
        
        2. **조명**
           - 설정: {guide['lighting']}
           - 그림자: 매우 부드럽고 자연스럽게
           - 반사: 과도한 하이라이트 없이 질감만 강조
           - 색온도: 자연스러운 중성 색온도
        
        3. **세부 표현**
           - 주의할 디테일: {guide['details']}
           - 질감: 촉감이 느껴질 정도로 사실적으로
           - 선명도: 초점 부분은 매우 선명하게
           - 색상: 실제 소재의 색상을 정확하게
        
        4. **촬영 기법**
           - 매크로 렌즈 효과 (100mm 이상)
           - {aperture}의 얕은 피사계 심도
           - 초점은 가장 중요한 부분에 정확히
           - 배경은 부드럽게 흐림 처리 (bokeh)
        
        5. **품질 기준**
           - 해상도: 최소 {resolution}
           - 디테일: 확대해도 선명한 수준
           - 노이즈: 완전히 제거
           - 색감: 자연스럽고 정확하게
        
        **촬영 목적:**
        이 이미지는 온라인 쇼핑몰의 상세 페이지에서 제품의 품질과 마감을 보여주는 용도입니다.
        고객이 제품의 우수한 품질을 확인할 수 있도록 최고 수준으로 촬영하세요.
        """

def create_layout_composition_prompt(layout_type: str, elements: list, reference_layout_path: str = None) -> str:
    """
    레이아웃 합성 프롬프트

    Args:
        layout_type: 레이아웃 타입
        elements: 배치할 요소 리스트
        reference_layout_path: 레이아웃 참고 이미지 경로

    Returns:
        레이아웃 합성 프롬프트
    """
    elements_desc = "\n".join([f"   {i+1}. {elem}" for i, elem in enumerate(elements)])

    reference_section = ""
    if reference_layout_path:
        reference_section = """
        **레퍼런스 레이아웃:**
        첫 번째 이미지는 레이아웃의 참고 디자인입니다.
        이 레이아웃의 다음 요소들을 정확히 따르세요:
        - 전체적인 그리드 구조와 섹션 배치
        - 이미지와 텍스트 영역의 비율
        - 여백과 간격 시스템
        - 시각적 계층 구조
        - 타이포그래피 스타일 (폰트 크기, 굵기, 정렬)
        - 색상 배치와 톤
        """

    return f"""
        당신은 전문 그래픽 디자이너입니다. 제품 상세 페이지용 레이아웃을 디자인하세요.
        
        **레이아웃 타입:** {layout_type}
        {reference_section}
        **배치할 콘텐츠:**
        다음 이미지들을 레이아웃에 배치하세요:
        {elements_desc}
        
        **디자인 원칙:**
        
        1. **시각적 계층 구조**
           - 주요 제품 이미지: 가장 크고 시선을 끄는 위치
           - 디테일 이미지들: 적절한 크기로 균형있게 배치
           - 텍스트 영역: 가독성을 고려한 여백과 크기
        
        2. **그리드 시스템**
           - 일관된 그리드 기반 배치
           - 정렬선을 맞춘 깔끔한 레이아웃
           - 좌우 대칭 또는 균형잡힌 비대칭 구조
        
        3. **여백과 간격**
           - 이미지 간 일정한 간격 (예: 20-40px)
           - 섹션 간 명확한 구분 (60-80px)
           - 호흡할 수 있는 충분한 여백
           - 답답하지 않은 개방감
        
        4. **색상과 스타일**
           - 깔끔하고 전문적인 느낌
           - 화이트/그레이 기본 배경
           - 포인트 색상은 절제있게 사용
           - 제품이 주인공이 되도록
        
        5. **타이포그래피**
           - 제목: 큰 크기, 굵은 폰트 (36-48pt)
           - 소제목: 중간 크기 (24-30pt)
           - 본문: 가독성 좋은 크기 (14-16pt)
           - 폰트: 모던하고 깔끔한 산세리프
        
        6. **이미지 처리**
           - 모든 이미지는 동일한 스타일로 통일
           - 필요시 라운드 코너 또는 드롭 섀도우 일관되게 적용
           - 이미지 품질 유지 (압축 최소화)
           - 적절한 크롭과 비율 조정
        
        **기술 사양:**
        - 캔버스 크기: 1200px 너비 (반응형 고려)
        - 해상도: 72-144 DPI (웹용)
        - 파일 형식: 고품질 JPG 또는 PNG
        - 최적화: 로딩 속도 고려
        
        **체크리스트:**
        ✓ 레퍼런스 레이아웃의 구조를 따랐는가?
        ✓ 모든 요소가 그리드에 정렬되었는가?
        ✓ 시각적 계층이 명확한가?
        ✓ 여백이 충분하고 일관된가?
        ✓ 전체적으로 조화롭고 전문적인가?
        ✓ 제품의 매력이 최대한 표현되었는가?
        
        이 레이아웃은 온라인 쇼핑몰의 상세 페이지에 사용됩니다.
        구매 전환율을 높일 수 있도록 매력적이고 전문적으로 제작하세요.
        """

def create_layout_analysis_prompt() -> str:
    """
    레이아웃 레퍼런스 이미지 분석 프롬프트
    이미지 영역과 텍스트 영역을 파악하고 바운딩 박스 좌표 추출

    Returns:
        레이아웃 분석 프롬프트
    """
    return """
    당신은 웹 디자인 레이아웃 분석 전문가입니다.
    제공된 레이아웃 레퍼런스 이미지를 상세히 분석하여 다음 정보를 JSON 형식으로 추출하세요.

    **분석 항목:**
    1. **전체 레이아웃 구조**:
       - 레이아웃 타입 (단일 컬럼, 2단 구성, 그리드, 콜라주 등)
       - 섹션 구분 (헤더, 메인 이미지 영역, 특징 설명, 상세 정보 등)

    2. **이미지 영역 (바운딩 박스 포함)**:
       - 각 이미지 영역에 대한 바운딩 박스 좌표 (x, y, width, height - 이미지 전체 크기 대비 비율 0~1)
       - 고유 번호 (area_id)
       - 타입: 메인/서브/배경/장식
       - 콜라주 여부: 여러 이미지가 조합된 형태인지 확인
       - 스타일: 배경색, 여백, 그림자 효과 등

    3. **텍스트 영역 (바운딩 박스 포함)**:
       - 각 텍스트 영역에 대한 바운딩 박스 좌표 (x, y, width, height - 비율 0~1)
       - 고유 번호 (text_id)
       - 타입: 제목/부제목/본문/특징리스트/캡션
       - 폰트 크기 (대/중/소), 색상, 정렬 방식
       - 텍스트 내용 샘플 (있는 경우)

    4. **디자인 요소**:
       - 배경색/그라데이션
       - 여백 및 패딩 스타일
       - 테두리 및 구분선
       - 아이콘 또는 장식 요소

    5. **색상 톤**:
       - 주요 색상 (Primary Color)
       - 보조 색상 (Secondary Color)
       - 텍스트 색상
       - 배경 색상

    **응답 형식 (반드시 유효한 JSON):**
    ```json
    {
        "layout_type": "2단 구성",
        "sections": [
            {
                "name": "헤더",
                "position": "상단",
                "height_ratio": "10%"
            },
            {
                "name": "메인 이미지 영역",
                "position": "좌측",
                "width_ratio": "50%",
                "description": "제품 메인 이미지 배치"
            }
        ],
        "image_areas": [
            {
                "area_id": 1,
                "type": "main",
                "is_collage": false,
                "bbox": {
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.4,
                    "height": 0.6
                },
                "position_description": "좌측 중앙",
                "size_ratio": "40%",
                "style": "흰색 배경, 그림자 효과"
            },
            {
                "area_id": 2,
                "type": "sub",
                "is_collage": true,
                "bbox": {
                    "x": 0.6,
                    "y": 0.5,
                    "width": 0.3,
                    "height": 0.4
                },
                "position_description": "우측 하단",
                "size_ratio": "20%",
                "style": "여러 이미지 콜라주",
                "collage_layout": "2x2 그리드"
            }
        ],
        "text_areas": [
            {
                "text_id": 1,
                "type": "title",
                "bbox": {
                    "x": 0.55,
                    "y": 0.1,
                    "width": 0.4,
                    "height": 0.08
                },
                "position_description": "우측 상단",
                "font_size": "대",
                "alignment": "좌측 정렬",
                "color": "검정색",
                "sample_text": "제품명 예시"
            },
            {
                "text_id": 2,
                "type": "description",
                "bbox": {
                    "x": 0.55,
                    "y": 0.25,
                    "width": 0.4,
                    "height": 0.15
                },
                "position_description": "우측 중앙",
                "font_size": "중",
                "alignment": "좌측 정렬",
                "color": "회색"
            }
        ],
        "design_elements": {
            "background": "밝은 회색 그라데이션",
            "padding": "넉넉한 여백",
            "borders": "없음",
            "decorations": "최소화"
        },
        "color_scheme": {
            "primary": "#FFFFFF",
            "secondary": "#F5F5F5",
            "text": "#333333",
            "accent": "#0066CC"
        }
    }
    ```

    **중요:**
    - 반드시 유효한 JSON 형식으로만 응답하세요.
    - 바운딩 박스 좌표는 이미지 전체 크기 대비 비율(0~1)로 표현하세요.
    - 각 이미지 영역과 텍스트 영역에 고유 ID를 부여하세요.
    - 콜라주 형식의 이미지 영역인 경우 is_collage를 true로 설정하세요.
    - 이미지에서 관찰한 실제 레이아웃 구조를 정확히 기술하세요.
    """


def create_layout_generation_prompt(metadata: dict, layout_analysis: dict = None) -> str:
    """
    상세페이지 생성 프롬프트
    Args:
        metadata: 제품 메타데이터 (카테고리, 이름, 특징, 설명 등)
    Returns:
        상세페이지 생성 프롬프트
    """

    # 메타데이터를 텍스트로 변환
    metadata_text = ""
    if 'filename' in metadata:
        metadata_text += f"- 제품명: {metadata['filename']}\n"
    if 'category' in metadata:
        metadata_text += f"- 카테고리: {metadata['category']}\n"
    if 'key_features' in metadata:
        features = ", ".join(metadata['key_features'])
        metadata_text += f"- 주요 특징: {features}\n"
    
    # 설명이 dict인 경우와 str인 경우 처리
    description = metadata.get('description', '')
    if isinstance(description, dict):
        desc_text = description.get('description', '')
    else:
        desc_text = str(description)
    
    if desc_text:
        metadata_text += f"- 제품 설명: {desc_text}\n"

    return f"""
    당신은 전문 웹 디자이너이자 UX/UI 전문가입니다. 
    제공된 '레이아웃 레퍼런스 이미지'(첫 번째 이미지)의 구조와 스타일을 참고하여, 
    '제품 이미지'(두 번째 이미지)를 주인공으로 하는 고품질 제품 상세페이지 이미지를 생성하세요.

    **입력 정보:**
    1. **레이아웃 레퍼런스 (첫 번째 이미지)**: 
       - 이 이미지의 전체적인 레이아웃 구조, 텍스트 배치, 여백, 폰트 스타일, 색상 톤을 분석하여 적용하세요.
       - 단, 내용은 제공된 제품 정보로 완전히 대체해야 합니다.
    
    2. **제품 이미지 (두 번째 이미지)**:
       - 이 제품이 상세페이지의 메인 이미지가 되어야 합니다.
       - 제품의 형태와 색상을 정확하게 유지하세요.

    3. **제품 정보 (텍스트 콘텐츠)**:
       {metadata_text}

    **작업 지시사항:**
    1. **구조 모방**: 레퍼런스 이미지의 섹션 구분(헤더, 메인 이미지, 특징 설명, 상세 스펙 등)을 따르세요.
    2. **콘텐츠 대체**: 
       - 레퍼런스의 기존 텍스트 대신 위에서 제공한 '제품 정보'를 적절한 위치에 배치하세요.
       - 제품명은 가장 크고 눈에 띄게 배치하세요.
       - 주요 특징과 설명은 가독성 좋게 본문에 배치하세요.
    3. **디자인 통일성**: 레퍼런스의 디자인 언어(세련됨, 미니멀, 모던 등)를 유지하면서, 제품 이미지와 어울리는 배색을 사용하세요.
    4. **고품질 렌더링**: 텍스트는 선명하고 읽기 쉬워야 하며, 전체적인 이미지는 실제 웹페이지 스크린샷처럼 전문적이고 완성도 높아야 합니다.

    **결과물:**
    - 웹사이트 상세페이지의 한 부분을 캡처한 듯한 고해상도 이미지.
    - 제품이 매력적으로 돋보이며, 정보가 명확히 전달되는 디자인.
    """


# -----------------------------------------------------------------------------
# Agent Prompt Templates
# -----------------------------------------------------------------------------

PRODUCTION_ALARM_PROMPT_TEMPLATE = """
        당신은 매장 생산 관리 전문가입니다. 다음 데이터를 바탕으로 점주가 이해하기 쉬운 짧고 명확한 알림 메시지를 생성하세요.
        
        - 제품명: {sku}
        - 현재고: {current_stock}
        - 1시간 뒤 예상 재고: {predicted_stock:.1f}
        - 위험 여부: {risk_status}
        - 예상 품절 시점: {stockout_at}
        - 4주 평균 생산 패턴: {pattern_4w}
        
        알림 메시지 조건:
        - 현재고와 예상 재고를 포함할 것.
        - 생산이 필요한 경우 시점과 수량을 제안할 것.
        - 2-3문장 이내로 친절하게 작성할 것.
        """

ORDERING_REASONING_PROMPT_TEMPLATE = """
        당신은 매장 주문 관리 전문가입니다. 다음 데이터를 바탕으로 점주가 주문 옵션을 선택할 수 있도록 상세한 추천 근거를 생성하세요.
        
        - 매장 ID: {store_id}
        - 현재 날짜: {current_date}
        - 캠페인 여부: {campaign_status}
        - 공휴일/시즌 여부: {holiday_status}
        
        추천 옵션:
        {options_summary}
        
        추천 근거 생성 조건:
        - 캠페인이나 공휴일 신호가 있는 경우 이를 반영하여 특정 옵션을 더 강조할 것.
        - 점주가 의사결정을 내릴 수 있도록 각 옵션의 장단점을 짧게 언급할 것.
        - 자연스러운 문장으로 작성할 것.
        """

SALES_ANALYSIS_PROMPT_TEMPLATE = """
        당신은 베스킨라빈스/던킨 매장 매출 분석 AI 전문가입니다. 점주의 자연어 질의를 해석하고, 다음 형식의 JSON 응답을 생성하세요.
        
        질의: {prompt}
        
        응답 조건:
        - "text": 질의에 대한 자연스럽고 통찰력 있는 요약 설명.
        - "evidence": 분석의 근거가 되는 구체적인 데이터 포인트들 (리스트 형태).
        - "actions": 점주가 즉시 실행할 수 있는 액션 아이템들 (리스트 형태).
        - 비교군 데이터(전주, 전월, 동일상권 등)를 활용하여 답변을 구성할 것.
        
        반드시 다음 JSON 형식을 유지하세요:
        {{
            "text": "...",
            "evidence": ["...", "..."],
            "actions": ["...", "..."]
        }}
        """

def create_production_alarm_prompt(sku: str, current_stock: int, predicted_stock: float, risk_status: str, stockout_at: str, pattern_4w: list) -> str:
    return PRODUCTION_ALARM_PROMPT_TEMPLATE.format(
        sku=sku,
        current_stock=current_stock,
        predicted_stock=predicted_stock,
        risk_status=risk_status,
        stockout_at=stockout_at,
        pattern_4w=pattern_4w
    )

def create_ordering_reasoning_prompt(store_id: str, current_date: str, campaign_status: str, holiday_status: str, options_summary: str) -> str:
    return ORDERING_REASONING_PROMPT_TEMPLATE.format(
        store_id=store_id,
        current_date=current_date,
        campaign_status=campaign_status,
        holiday_status=holiday_status,
        options_summary=options_summary
    )

def create_sales_analysis_prompt(prompt: str) -> str:
    return SALES_ANALYSIS_PROMPT_TEMPLATE.format(prompt=prompt)