class QueryClassifier:
    """간단한 의도 분류기 가드레일"""
    def classify(self, query: str) -> str:
        # 민감어 필터링 등
        sensitive_keywords = ["개인정보", "주민번호", "비밀번호"]
        if any(keyword in query for keyword in sensitive_keywords):
            return "SENSITIVE"
        return "NORMAL"
