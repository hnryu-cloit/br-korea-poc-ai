from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.sql_pipeline import SQLGenerator

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "알려줘",
    "보여줘",
    "확인",
    "핵심",
    "지금",
    "오늘",
    "어제",
    "지난",
    "최근",
    "이번",
    "대비",
    "뭐",
    "무엇",
    "어때",
    "해줘",
    "기준",
    "수치",
    "조회",
    "좀",
}

_PHRASE_CANONICAL_MAP = {
    "지난주": "전주",
    "전전주": "2주전",
    "지난달": "전월",
    "이번달": "당월",
    "최근 일주일": "최근7일",
    "지난 일주일": "최근7일",
    "지난 한달": "최근30일",
    "지난 한 달": "최근30일",
    "보여줘": "조회",
    "알려줘": "조회",
    "말해줘": "조회",
    "짚어줘": "조회",
}

_TOKEN_CANONICAL_MAP = {
    "판매": "매출",
    "판매량": "수량",
    "주문": "발주",
    "주문량": "발주수량",
    "주문수량": "발주수량",
    "오더": "주문",
    "입고": "납품",
    "품절": "재고부족",
}

_KOREAN_PARTICLE_SUFFIXES = (
    "으로",
    "에서",
    "까지",
    "부터",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "의",
    "도",
    "만",
    "와",
    "과",
    "로",
    "랑",
)

_DOMAIN_ANCHORS: dict[str, set[str]] = {
    "sales": {"매출", "객단가", "채널", "결제", "매입", "수익", "판매", "주문건수"},
    "production": {"생산", "재고", "품절", "폐기", "소진", "생산량", "재고율"},
    "ordering": {"주문", "발주", "납품", "마감", "확정수량", "추천발주", "erp"},
}

_NON_BUSINESS_HINTS = {
    "와이파이",
    "wifi",
    "비밀번호",
    "날씨",
    "교통",
    "택시",
    "음악",
    "노래",
    "영화",
    "식당",
    "맛집",
}

_STORE_PARAM_ALIASES = {
    "target_store_cd",
    "store_cd",
    "stor_cd",
    "storeid",
    "target_store_id",
}


@dataclass(frozen=True)
class GoldenQueryCandidate:
    query_id: str
    intent_id: str
    domain: str
    question: str
    sql_template: str
    expected_answer: str
    relevant_tables: list[str]
    tokens: set[str]
    slots: set[str]
    synonym_tokens: set[str]
    anchors: set[str]
    kpi_tags: set[str]
    abstract_signature: set[str]
    embedding_text: str
    normalized_question: str


@dataclass(frozen=True)
class GoldenQueryMatch:
    candidate: GoldenQueryCandidate
    rule_score: float
    embedding_score: float
    final_score: float


class GoldenQueryEngine:
    """의도카드 기반 하이브리드 골든쿼리 검색기"""

    def __init__(
        self,
        gemini_client: Any,
        csv_path: str | None = None,
        min_score: float = 0.45,
        min_margin: float | None = None,
    ) -> None:
        self.gemini = gemini_client
        env_score = os.getenv("GOLDEN_QUERY_MIN_SCORE", "").strip()
        self.min_score = float(env_score) if env_score else min_score
        env_margin = os.getenv("GOLDEN_QUERY_MIN_MARGIN", "").strip()
        if min_margin is not None:
            self.min_margin = min_margin
        else:
            self.min_margin = float(env_margin) if env_margin else 0.0
        self.follow_up_min_score = float(os.getenv("GOLDEN_QUERY_FOLLOWUP_MIN", "0.30"))
        self.enable_llm_rerank = os.getenv("GOLDEN_QUERY_LLM_RERANK", "0") != "0"
        self.embed_top_k = max(1, int(os.getenv("GOLDEN_QUERY_EMBED_TOP_K", "12")))
        self.csv_path = self._resolve_csv_path(csv_path)
        self.candidates = self._load_candidates(self.csv_path)
        self._embedding_cache: dict[str, list[float]] = {}
        self._embedding_enabled = True

    @staticmethod
    def _resolve_csv_path(csv_path: str | None) -> Path:
        if csv_path:
            return Path(csv_path)

        env_path = os.getenv("GOLDEN_QUERY_CSV", "").strip()
        if env_path:
            return Path(env_path)

        return Path(__file__).resolve().parents[2] / "br-korea-poc-backend" / "docs" / "golden-queries.csv"

    @staticmethod
    def _map_domain(agent_name: str) -> str | None:
        text = agent_name.strip()
        if "생산" in text:
            return "production"
        if "주문" in text or "발주" in text:
            return "ordering"
        if "매출" in text:
            return "sales"
        return None

    @staticmethod
    def _extract_tables(table_column: str) -> list[str]:
        tables: list[str] = []
        for raw in re.split(r"[,/]+", table_column):
            token = raw.strip()
            if not token:
                continue
            table = token.split(".", 1)[0].strip()
            if table and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = text.lower()
        for src, dst in _PHRASE_CANONICAL_MAP.items():
            lowered = lowered.replace(src, dst)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    @staticmethod
    def _extract_tokens(normalized_text: str) -> set[str]:
        tokens: set[str] = set()
        for token in re.findall(r"[0-9A-Za-z_]+|[가-힣]{2,}", normalized_text):
            word = token.strip()
            if not word or word in _STOPWORDS:
                continue
            if len(word) > 2:
                for suffix in _KOREAN_PARTICLE_SUFFIXES:
                    if word.endswith(suffix) and len(word) > len(suffix) + 1:
                        word = word[: -len(suffix)]
                        break
            word = _TOKEN_CANONICAL_MAP.get(word, word)
            tokens.add(word)
        return tokens

    def _extract_slots(self, query: str) -> set[str]:
        slots: set[str] = set()
        compact = re.sub(r"\s+", "", self._normalize_text(query))

        if "오늘" in compact:
            slots.add("today")
        if "어제" in compact:
            slots.add("yesterday")
        if any(token in compact for token in ("전주", "2주전")):
            slots.add("weekly_compare")
        if any(token in compact for token in ("전월", "당월")):
            slots.add("monthly_compare")
        if re.search(r"최근\d+일", compact):
            slots.add("recent_days")
        if re.search(r"\d{4}-\d{2}-\d{2}|\d{8}", compact):
            slots.add("explicit_date")
        if re.search(r"[가-힣A-Za-z0-9_]+(도넛|케이크|케익|커피|라떼|머핀|샌드|상품|품목)", compact):
            slots.add("product")

        return slots

    def _extract_synonym_tokens(self, synonyms: str) -> set[str]:
        if not synonyms.strip():
            return set()
        merged: set[str] = set()
        for raw in re.split(r"[|,;/]+", synonyms):
            token = raw.strip()
            if not token:
                continue
            merged.update(self._extract_tokens(self._normalize_text(token)))
        return merged

    def _extract_kpi_tags(self, question: str, tables: list[str], sql_template: str) -> set[str]:
        source = f"{question} {' '.join(tables)} {sql_template}".lower()
        tags: set[str] = set()
        rules = {
            "sales": ["sale", "매출", "객단가", "ord_cnt"],
            "order": ["ord", "발주", "납품", "confrm"],
            "stock": ["stock", "재고", "품절", "stk_rt"],
            "waste": ["폐기", "disuse", "waste"],
            "ratio": ["비율", "%", "ratio"],
        }
        for tag, hints in rules.items():
            if any(h in source for h in hints):
                tags.add(tag)
        return tags

    def _build_abstract_signature(self, query: str) -> set[str]:
        compact = re.sub(r"\s+", "", self._normalize_text(query))
        abstracted = compact
        abstracted = re.sub(r"\d{8}", "{date}", abstracted)
        abstracted = re.sub(r"\d{4}-\d{2}-\d{2}", "{date}", abstracted)
        abstracted = re.sub(r"최근\d+일", "최근{n}일", abstracted)
        abstracted = re.sub(r"\d+", "{n}", abstracted)
        abstracted = re.sub(
            r"[가-힣A-Za-z0-9_]+(도넛|케이크|케익|커피|라떼|머핀|샌드)",
            "{product}",
            abstracted,
        )
        return self._extract_tokens(abstracted)

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _embed(self, text: str) -> list[float]:
        if not self._embedding_enabled:
            return []
        key = text.strip()
        if not key:
            return []
        cached = self._embedding_cache.get(key)
        if cached is not None:
            return cached
        try:
            vector = self.gemini.get_embeddings(key)
            values = [float(v) for v in vector]
            self._embedding_cache[key] = values
            return values
        except Exception as exc:
            self._embedding_enabled = False
            logger.warning("골든쿼리 임베딩 비활성화: %s", exc)
            return []

    def _load_candidates(self, path: Path) -> list[GoldenQueryCandidate]:
        if not path.exists():
            logger.warning("골든쿼리 파일이 없어 매칭을 비활성화합니다: %s", path)
            return []

        loaded: list[GoldenQueryCandidate] = []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if (row.get("가용여부") or "").strip() != "✅":
                    continue

                query_id = str(row.get("질문번호") or "").strip()
                question = str(row.get("질문") or "").strip()
                sql_template = str(row.get("일반화 쿼리") or "").strip()
                if not query_id or not question or not sql_template:
                    continue

                domain = self._map_domain(str(row.get("에이전트") or ""))
                if not domain:
                    continue

                intent_id_raw = str(row.get("의도ID") or row.get("intent_id") or "").strip()
                intent_id = intent_id_raw or query_id.split("-", 1)[0] or query_id
                normalized_question = self._normalize_text(question)
                tokens = self._extract_tokens(normalized_question)
                slots = self._extract_slots(question)
                synonyms = str(row.get("동의어") or row.get("synonyms") or "")
                synonym_tokens = self._extract_synonym_tokens(synonyms)
                tables = self._extract_tables(str(row.get("테이블/컬럼") or ""))
                anchors = set(tokens)
                anchors.update(synonym_tokens)
                anchors.update(_DOMAIN_ANCHORS.get(domain, set()))
                kpi_tags = self._extract_kpi_tags(question, tables, sql_template)
                abstract_signature = self._build_abstract_signature(question)
                embedding_text = "\n".join(
                    [
                        f"domain={domain}",
                        f"intent_id={intent_id}",
                        f"question={normalized_question}",
                        f"synonyms={synonyms}",
                        f"kpi_tags={','.join(sorted(kpi_tags))}",
                        f"slots={','.join(sorted(slots))}",
                    ]
                )
                loaded.append(
                    GoldenQueryCandidate(
                        query_id=query_id,
                        intent_id=intent_id,
                        domain=domain,
                        question=question,
                        sql_template=sql_template,
                        expected_answer=str(row.get("예상 답변") or "").strip(),
                        relevant_tables=tables,
                        tokens=tokens,
                        slots=slots,
                        synonym_tokens=synonym_tokens,
                        anchors=anchors,
                        kpi_tags=kpi_tags,
                        abstract_signature=abstract_signature,
                        embedding_text=embedding_text,
                        normalized_question=normalized_question,
                    )
                )

        logger.info("골든쿼리 로드 완료: %d건 (%s)", len(loaded), path)
        return loaded

    def _is_business_query(self, query_tokens: set[str]) -> bool:
        if any(token in _NON_BUSINESS_HINTS for token in query_tokens):
            return False
        return True

    def _extract_query_signals(self, query: str, domain: str) -> dict[str, Any]:
        normalized = self._normalize_text(query)
        tokens = self._extract_tokens(normalized)
        slots = self._extract_slots(query)
        abstract = self._build_abstract_signature(query)
        domain_anchors = _DOMAIN_ANCHORS.get(domain, set())
        anchor_hits = tokens & domain_anchors
        return {
            "normalized": normalized,
            "tokens": tokens,
            "slots": slots,
            "abstract": abstract,
            "anchor_hits": anchor_hits,
            "business": self._is_business_query(tokens),
            "kpi_tags": self._extract_kpi_tags(query, [], ""),
        }

    def _rule_score(self, signals: dict[str, Any], candidate: GoldenQueryCandidate) -> float:
        token_pool = set(candidate.tokens) | set(candidate.synonym_tokens)
        token_score = self._jaccard(signals["tokens"], token_pool)
        slot_score = self._jaccard(signals["slots"], candidate.slots) if signals["slots"] or candidate.slots else 0.5
        abstract_score = self._jaccard(signals["abstract"], candidate.abstract_signature)
        kpi_score = self._jaccard(signals["kpi_tags"], candidate.kpi_tags) if signals["kpi_tags"] or candidate.kpi_tags else 0.5
        anchor_score = self._jaccard(signals["anchor_hits"], candidate.anchors & _DOMAIN_ANCHORS.get(candidate.domain, set())) if signals["anchor_hits"] else 0.0
        return (token_score * 0.35) + (slot_score * 0.2) + (abstract_score * 0.2) + (kpi_score * 0.15) + (anchor_score * 0.1)

    def _llm_rerank(
        self,
        *,
        query: str,
        domain: str,
        candidates: list[GoldenQueryMatch],
    ) -> tuple[str | None, float]:
        if not self.enable_llm_rerank or len(candidates) < 2:
            return None, 0.0
        preview = [
            {
                "query_id": item.candidate.query_id,
                "intent_id": item.candidate.intent_id,
                "question": item.candidate.question,
                "score": round(item.final_score, 4),
            }
            for item in candidates[:3]
        ]
        prompt = f"""
다음 사용자 질문에 가장 적합한 골든쿼리 후보 1개를 선택하세요.

[도메인]
{domain}

[질문]
{query}

[후보]
{json.dumps(preview, ensure_ascii=False)}

규칙:
- 의미적으로 가장 가까운 후보 1개를 고르세요.
- 모두 부적합하면 query_id를 빈 문자열로 반환하세요.
- confidence는 0~1 범위.

JSON:
{{"query_id":"...","confidence":0.0}}
""".strip()
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(raw) if isinstance(raw, str) else raw
            query_id = str(data.get("query_id") or "").strip()
            confidence = float(data.get("confidence") or 0.0)
            return query_id or None, max(0.0, min(confidence, 1.0))
        except Exception as exc:
            logger.debug("llm rerank skipped: %s", exc)
            return None, 0.0

    def _score_candidates(self, query: str, domain: str) -> list[GoldenQueryMatch]:
        candidates = [c for c in self.candidates if c.domain == domain]
        if not candidates:
            return []

        signals = self._extract_query_signals(query, domain)
        if not signals["business"]:
            return []

        query_embedding = self._embed(signals["normalized"])
        scored: list[GoldenQueryMatch] = [
            GoldenQueryMatch(
                candidate=candidate,
                rule_score=self._rule_score(signals, candidate),
                embedding_score=0.0,
                final_score=0.0,
            )
            for candidate in candidates
        ]
        scored.sort(key=lambda item: item.rule_score, reverse=True)

        for idx, item in enumerate(scored):
            use_embedding = bool(query_embedding) and idx < self.embed_top_k
            embedding_score = 0.0
            if use_embedding:
                candidate_embedding = self._embed(item.candidate.embedding_text)
                if candidate_embedding:
                    embedding_score = self._cosine(query_embedding, candidate_embedding)
            final_score = (
                (item.rule_score * 0.55) + (embedding_score * 0.45)
                if use_embedding and embedding_score > 0
                else item.rule_score
            )
            scored[idx] = GoldenQueryMatch(
                candidate=item.candidate,
                rule_score=item.rule_score,
                embedding_score=embedding_score,
                final_score=final_score,
            )

        scored.sort(key=lambda item: item.final_score, reverse=True)
        if not scored:
            return []

        top = scored[0]
        second = scored[1] if len(scored) > 1 else None
        should_rerank = bool(
            second
            and self.enable_llm_rerank
            and top.final_score < 0.9
            and (top.final_score - second.final_score) <= 0.25
        )
        rerank_id, rerank_conf = (
            self._llm_rerank(query=query, domain=domain, candidates=scored)
            if should_rerank
            else (None, 0.0)
        )
        if rerank_id and rerank_conf >= 0.60:
            for idx, item in enumerate(scored):
                if item.candidate.query_id == rerank_id:
                    boosted = GoldenQueryMatch(
                        candidate=item.candidate,
                        rule_score=item.rule_score,
                        embedding_score=item.embedding_score,
                        final_score=min(1.0, (item.final_score * 0.8) + (rerank_conf * 0.2)),
                    )
                    scored.insert(0, scored.pop(idx))
                    scored[0] = boosted
                    break

        return scored

    def rank_candidates(self, query: str, domain: str, limit: int = 3) -> list[GoldenQueryMatch]:
        return self._score_candidates(query, domain)[:limit]

    def match(self, query: str, domain: str) -> GoldenQueryMatch | None:
        ranked = self._score_candidates(query, domain)
        if not ranked:
            return None
        best = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        margin = best.final_score - (second.final_score if second else 0.0)
        if best.final_score >= self.min_score and margin >= self.min_margin:
            return best
        return None

    def suggest_follow_up_queries(
        self,
        *,
        query: str,
        domain: str,
        limit: int = 3,
        exclude_query_id: str | None = None,
    ) -> list[str]:
        ranked = self._score_candidates(query, domain)
        picked: list[str] = []
        for item in ranked:
            if exclude_query_id and item.candidate.query_id == exclude_query_id:
                continue
            if item.final_score < self.follow_up_min_score:
                continue
            question = item.candidate.question
            if question in picked:
                continue
            picked.append(question)
            if len(picked) >= limit:
                break
        return picked

    @staticmethod
    def _resolve_reference_date(reference_date: str | None) -> datetime:
        raw = (reference_date or os.getenv("SQL_REFERENCE_DATE") or "").strip()
        if raw:
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        return datetime.now()

    def _infer_period(self, query: str, domain: str, reference_date: str | None) -> dict[str, str]:
        ref = self._resolve_reference_date(reference_date)
        query_type = {"sales": "sales", "production": "production", "ordering": "order"}.get(
            domain, "general"
        )
        inferred = SQLGenerator._infer_period(query, query_type, ref.strftime("%Y-%m-%d"))
        date_from = datetime.strptime(inferred["from"], "%Y-%m-%d")
        date_to = datetime.strptime(inferred["to"], "%Y-%m-%d")

        compact = re.sub(r"\s+", "", query)
        if "오늘" in compact:
            date_from = ref
            date_to = ref
        if "지난달" in compact:
            start_of_this_month = ref.replace(day=1)
            end_of_last_month = start_of_this_month - timedelta(days=1)
            date_from = end_of_last_month.replace(day=1)
            date_to = end_of_last_month

        this_month_start = ref.replace(day=1)
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        return {
            "reference_dt": ref.strftime("%Y%m%d"),
            "date_from": date_from.strftime("%Y%m%d"),
            "date_to": date_to.strftime("%Y%m%d"),
            "this_month_start": this_month_start.strftime("%Y%m%d"),
            "this_month_end": ref.strftime("%Y%m%d"),
            "last_month_start": last_month_start.strftime("%Y%m%d"),
            "last_month_end": last_month_end.strftime("%Y%m%d"),
            "label": inferred.get("label", ""),
        }

    @staticmethod
    def _normalize_sql_placeholders(sql: str) -> str:
        normalized = sql
        for alias in _STORE_PARAM_ALIASES:
            normalized = re.sub(
                rf":{alias}\b",
                ":store_id",
                normalized,
                flags=re.IGNORECASE,
            )
        return normalized

    @staticmethod
    def _format_row(row: dict[str, Any]) -> str:
        cells: list[str] = []
        for _, value in row.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            cells.append(text)
        return " | ".join(cells)

    @staticmethod
    def _serialize_rows_for_llm(rows: list[dict[str, Any]], max_rows: int = 30) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for row in rows[:max_rows]:
            clean: dict[str, Any] = {}
            for key, value in row.items():
                if value is None:
                    clean[key] = None
                    continue
                if isinstance(value, (int, float, bool, str)):
                    if isinstance(value, float) and not math.isfinite(value):
                        clean[key] = None
                    else:
                        clean[key] = value
                else:
                    clean[key] = str(value)
            serialized.append(clean)
        return serialized

    def _llm_format_answer(
        self,
        *,
        query: str,
        candidate: GoldenQueryCandidate,
        rows: list[dict[str, Any]],
        period: dict[str, str],
    ) -> dict[str, Any] | None:
        if not rows or self.gemini is None:
            return None

        prompt_payload = {
            "user_question": query,
            "domain": candidate.domain,
            "matched_intent": candidate.intent_id or candidate.query_id,
            "expected_answer_template": candidate.expected_answer or "",
            "queried_period": period,
            "relevant_tables": candidate.relevant_tables,
            "row_count": len(rows),
            "sample_rows": self._serialize_rows_for_llm(rows),
        }

        system_instruction = (
            "당신은 매장 운영 데이터를 친절하게 풀어주는 한국어 분석가입니다.\n"
            "사용자 질문과 SQL 결과(rows)만 근거로 점주가 즉시 이해할 수 있는 답변을 작성합니다.\n"
            "- 표/숫자는 한국어 단위(원, 건, 개, 명, %)로 변환하고, 큰 금액은 천 단위 콤마 또는 만/억 단위 요약을 활용하세요.\n"
            "- 절대로 rows에 없는 수치를 만들어내지 마세요.\n"
            "- text는 800자 이내, 핵심 요약 1~2문장 + 필요한 경우 마크다운 표/불릿으로 정리하세요.\n"
            "- evidence 2~3개, actions 2~3개(각 80자 이내).\n"
            "[follow_up_questions 작성 규칙 — 매우 중요]\n"
            "1. 사용자 원 질문(user_question)과 동일하거나 의미가 거의 같은 문장은 절대 포함 금지.\n"
            "2. 3개 모두 서로 다른 관점이어야 함: (A) 다른 기간(전주/전월/지난달 등)으로 비교, "
            "(B) 다른 분해축(채널·결제·상품·시간대 중 원 질문에 없던 축), (C) 심화·원인 분석(왜? 어디서?).\n"
            "3. 각 30자 이내, 자연스러운 점주 화법, 명령형보다 의문형 또는 평서형 권장.\n"
            "4. 직전 응답에 노출된 질문과 표현이 겹치지 않도록 어휘를 다양화."
        )

        prompt = (
            "아래 JSON 입력을 바탕으로 점주에게 보낼 답변을 만들어 주세요.\n"
            "특히 follow_up_questions 3개는 user_question과 의미가 다르고 서로도 겹치지 않는,\n"
            "이 데이터의 후속 분석으로 이어질 만한 질문이어야 합니다.\n\n"
            f"[입력]\n{json.dumps(prompt_payload, ensure_ascii=False, default=str)}\n\n"
            "반드시 아래 JSON 스키마로만 응답하세요.\n"
            "{\n"
            '  "text": "마크다운 답변 본문",\n'
            '  "evidence": ["근거1", "근거2"],\n'
            '  "actions": ["액션1", "액션2"],\n'
            '  "follow_up_questions": ["추가질문1", "추가질문2", "추가질문3"]\n'
            "}"
        )

        try:
            raw = self.gemini.call_gemini_text(
                prompt=prompt,
                system_instruction=system_instruction,
                response_type="application/json",
            )
        except Exception as exc:
            logger.warning("골든쿼리 LLM 포맷팅 실패: %s", exc)
            return None

        if not raw:
            return None

        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            logger.warning("골든쿼리 LLM 응답 파싱 실패: %s | raw=%s", exc, str(raw)[:200])
            return None

        text = str(parsed.get("text") or "").strip()
        if not text:
            return None
        evidence = [str(item).strip() for item in parsed.get("evidence", []) if str(item).strip()][:3]
        actions = [str(item).strip() for item in parsed.get("actions", []) if str(item).strip()][:3]

        def _normalize_for_compare(value: str) -> str:
            return re.sub(r"[\s\.\?!,]+", "", value).lower()

        original_norm = _normalize_for_compare(query)
        seen_norms: set[str] = {original_norm}
        follow_ups: list[str] = []
        for item in parsed.get("follow_up_questions", []):
            cleaned = str(item).strip()
            if not cleaned:
                continue
            norm = _normalize_for_compare(cleaned)
            if not norm or norm in seen_norms:
                continue
            seen_norms.add(norm)
            follow_ups.append(cleaned)
            if len(follow_ups) >= 3:
                break

        return {
            "text": text,
            "evidence": evidence,
            "actions": actions,
            "follow_up_questions": follow_ups,
        }

    def _build_response_text(self, query: str, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "요청 조건에 맞는 데이터가 없습니다."

        rendered = [self._format_row(row) for row in rows[:3]]
        rendered = [row for row in rendered if row]
        if not rendered:
            return "요청 조건에 맞는 데이터가 없습니다."
        if len(rendered) == 1:
            return f"{query} 조회 결과는 {rendered[0]}입니다."
        return f"{query} 조회 결과는 {', '.join(rendered)}입니다."

    def resolve_and_execute(
        self,
        *,
        query: str,
        domain: str,
        store_id: str,
        reference_date: str | None,
        executor: Any,
    ) -> dict[str, Any] | None:
        match = self.match(query, domain)
        if not match:
            return None

        period = self._infer_period(query, domain, reference_date)
        sql = self._normalize_sql_placeholders(match.candidate.sql_template)

        params = {
            "store_id": store_id,
            "target_store_cd": store_id,
            "reference_dt": period["reference_dt"],
            "date_from": period["date_from"],
            "date_to": period["date_to"],
            "start_date": period["date_from"],
            "end_date": period["date_to"],
            "this_month_start": period["this_month_start"],
            "this_month_end": period["this_month_end"],
            "last_month_start": period["last_month_start"],
            "last_month_end": period["last_month_end"],
        }

        rows, _ = executor.run(
            sql,
            store_id,
            agent_name=f"{domain}_golden_query",
            target_tables=match.candidate.relevant_tables,
            params=params,
        )

        llm_answer = self._llm_format_answer(
            query=query,
            candidate=match.candidate,
            rows=rows,
            period=period,
        )

        base_evidence = [
            f"골든쿼리 매칭: {match.candidate.query_id} (score={match.final_score:.2f})",
            f"조회 테이블: {', '.join(match.candidate.relevant_tables) or 'N/A'}",
            f"조회 기간: {period.get('date_from')} ~ {period.get('date_to')} (행 {len(rows)}건)",
        ]

        if llm_answer is not None:
            text = llm_answer["text"]
            evidence = llm_answer["evidence"] or base_evidence
            actions = llm_answer["actions"] or [
                "현재 결과의 핵심 지표 1개를 골라 즉시 점검",
                "동일 기간으로 다른 도메인(생산/주문) 지표를 비교",
            ]
            follow_ups = llm_answer["follow_up_questions"]
        else:
            text = self._build_response_text(query, rows)
            evidence = base_evidence
            if match.candidate.expected_answer:
                evidence.append(f"예상 응답 템플릿: {match.candidate.expected_answer}")
            actions = ["질문 조건을 구체화해 재조회", "동일 기간으로 도메인 비교 조회"]
            follow_ups = []

        return {
            "text": text,
            "evidence": evidence,
            "actions": actions,
            "follow_up_questions": follow_ups,
            "intent": f"golden query match: {match.candidate.query_id}",
            "relevant_tables": match.candidate.relevant_tables,
            "sql": sql,
            "queried_period": period,
            "row_count": len(rows),
            "matched_query_id": match.candidate.query_id,
            "match_score": round(match.final_score, 4),
        }


_default_resolver: GoldenQueryEngine | None = None


def get_default_resolver(gemini_client: Any) -> GoldenQueryEngine:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = GoldenQueryEngine(gemini_client)
    return _default_resolver
