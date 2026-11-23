# 상담 분류(category) 코드 → 화면 표시용 한글 이름
# 코드 값은 ai/consultation_analyzer.py 의 Category enum과 같다.
# DB·JSON·UiPath 연동에는 영문 코드를 쓰고, 화면에 보여줄 때만 이 표를 쓴다.
#
# 사용 예)  from database.category_labels import category_label
#           category_label("LOST_CARD")  ->  "카드 분실"

CATEGORY_LABELS = {
    "LOST_CARD": "카드 분실",
    "UNAUTHORIZED_TRANSACTION": "카드 부정사용 의심",
    "PAYMENT_INQUIRY": "일반 결제 문의",
    "GENERAL_INQUIRY": "일반 문의",
    "OTHER": "기타",
}


def category_label(code):
    """코드의 한글 이름을 반환한다. 표에 없는 값은 코드를 그대로 돌려준다."""
    return CATEGORY_LABELS.get(code, code)
