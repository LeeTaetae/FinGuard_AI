
import re


# ==========================================
# 1. 금융 상담 위험 표현 패턴
# ==========================================

UNSAFE_PATTERNS = [
    # 실제 조회하지 않은 거래를 확인했다고 주장
    r"거래.{0,15}확인(?:했|하였|되었|됐)습니다",
    r"거래.{0,15}조회(?:했|하였|되었|됐)습니다",
    r"이상\s*거래.{0,15}확인(?:되었|됐)습니다",

    # 실제 사고 접수를 완료했다고 주장
    r"사고\s*접수.{0,15}(?:완료|처리)(?:되었|됐|했습니다)",
    r"사고\s*접수를?\s*진행하겠습니다",
    r"사고\s*접수를?\s*완료했습니다",

    # 실제 카드 정지 및 재발급을 완료했다고 주장
    r"카드.{0,15}(?:정지|차단)(?:했|하였|되었|됐)습니다",
    r"재발급.{0,15}(?:완료|처리)(?:되었|됐|했습니다)",

    # 실제 환불을 완료했다고 주장
    r"환불.{0,15}(?:완료|처리)(?:되었|됐|했습니다)",

    # 미승인 거래나 사기 여부를 확정
    r"승인하지\s*않은\s*거래로\s*보입니다",
    r"미승인\s*거래로\s*(?:확인|판명)",
    r"사기\s*거래로\s*(?:확인|판명)",
]


# ==========================================
# 2. 상담 유형별 안전한 기본 답변
# ==========================================

SAFE_REPLIES = {
    "LOST_CARD": (
        "고객님, 카드 분실로 불편을 겪으셨겠습니다. "
        "안전한 공식 절차에 따라 분실 신고 및 "
        "카드 이용 정지를 요청하실 수 있습니다. "
        "담당 상담원이 필요한 본인 확인과 "
        "후속 절차를 안내해 드리겠습니다."
    ),

    "UNAUTHORIZED_TRANSACTION": (
        "고객님, 말씀하신 거래는 확인이 필요합니다. "
        "고객님께서 승인하지 않았다고 말씀하신 "
        "결제 내역에 대해 담당 상담원이 "
        "정해진 본인 확인 절차를 진행한 뒤 "
        "거래 조회 및 사고 접수 방법을 "
        "안내해 드리겠습니다."
    ),

    "PAYMENT_INQUIRY": (
        "고객님, 결제 금액이나 이용 내역은 "
        "카드사 공식 앱 또는 고객센터에서 "
        "확인하실 수 있습니다. "
        "정확한 확인 방법은 담당 상담원이 "
        "안내해 드리겠습니다."
    ),

    "GENERAL_INQUIRY": (
        "고객님, 문의하신 내용은 "
        "담당 상담원의 확인이 필요합니다. "
        "확인 후 적절한 절차를 "
        "안내해 드리겠습니다."
    ),

    "OTHER": (
        "죄송하지만 해당 문의는 "
        "금융 상담 서비스 범위에 포함되지 않습니다. "
        "카드 및 금융 서비스 관련 문의가 "
        "있으시면 안내해 드리겠습니다."
    ),
}


# ==========================================
# 3. 위험 표현 탐지 함수
# ==========================================

def detect_unsafe_reply(reply: str) -> bool:
    """
    AI가 생성한 답변에 위험 표현이 있는지 검사합니다.

    True: 위험 표현 발견
    False: 등록된 위험 표현 없음
    """

    if not isinstance(reply, str):
        return True

    if not reply.strip():
        return True

    for pattern in UNSAFE_PATTERNS:
        if re.search(
            pattern,
            reply,
            flags=re.IGNORECASE,
        ):
            return True

    return False


# ==========================================
# 4. 상담 안전성 검증 함수
# ==========================================

def validate_safety(result: dict) -> dict:
    """
    ChatGPT 상담 분석 결과를 검사합니다.

    1. 카드 분실 및 미승인 거래는 사람 검토 강제
    2. 위험 표현 탐지
    3. 위험 표현 발견 시 안전한 답변으로 교체
    4. 기존 JSON 스키마 유지
    """

    if not isinstance(result, dict):
        raise ValueError(
            "AI 분석 결과는 딕셔너리여야 합니다."
        )

    # 원본 데이터 변경 방지
    validated_result = result.copy()

    category = validated_result.get(
        "category",
        "OTHER"
    )

    reply = validated_result.get(
        "suggested_reply",
        ""
    )

    # ======================================
    # 사고 관련 상담은 사람 검토 필수
    # ======================================

    if category in (
        "LOST_CARD",
        "UNAUTHORIZED_TRANSACTION",
    ):
        validated_result["requires_human_review"] = True

    # ======================================
    # 위험 표현 검사
    # ======================================

    unsafe = detect_unsafe_reply(reply)

    if unsafe:
        # 위험 표현 발견 시 상담원 검토 필수
        validated_result["requires_human_review"] = True

        # 상담 유형별 안전한 답변으로 교체
        validated_result["suggested_reply"] = (
            SAFE_REPLIES.get(
                category,
                SAFE_REPLIES["GENERAL_INQUIRY"]
            )
        )

    return validated_result
