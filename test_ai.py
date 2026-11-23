
import json

from ai.consultation_analyzer import analyze_consultation

test_cases = [
    {
        "name": "카드 분실",
        "conversation": "오늘 카드를 잃어버렸어요. 정지하고 싶어요.",
        "expected": "LOST_CARD",
    },
    {
        "name": "미승인 거래",
        "conversation": "미국에서 120만 원이 결제됐는데 제가 결제한 적이 없어요.",
        "expected": "UNAUTHORIZED_TRANSACTION",
    },
    {
        "name": "결제 문의",
        "conversation": "이번 달 카드 결제 금액을 확인하고 싶어요.",
        "expected": "PAYMENT_INQUIRY",
    },
    {
        "name": "일반 문의",
        "conversation": "카드사 고객센터 운영시간이 궁금해요.",
        "expected": "GENERAL_INQUIRY",
    },
    {
        "name": "기타 문의",
        "conversation": "오늘 날씨가 어떤가요?",
        "expected": "OTHER",
    },
]

passed = 0

for case in test_cases:
    print(f"\n===== {case['name']} =====")

    try:
        result = analyze_consultation(case["conversation"])

        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ))

        if result["category"] == case["expected"]:
            print("PASS: 상담 분류 일치")
            passed += 1
        else:
            print(
                "FAIL: 예상 분류",
                case["expected"],
                "/ 실제 분류",
                result["category"],
            )

    except Exception as error:
        print(f"ERROR: {error}")

print(f"\n최종 결과: {passed}/{len(test_cases)}개 분류 일치")
