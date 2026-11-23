# FinGuard_AI

## 모의 CRM (담당 5)

| URL | 설명 |
|---|---|
| `GET /crm/incidents/new` | 사고 접수 양식 (UiPath 입력 대상) |
| `POST /crm/incidents` | 접수 처리 → 상세 화면으로 이동 |
| `GET /crm/incidents/<접수번호>` | 접수 완료/상세 |
| `GET /crm/incidents` | 접수 목록·처리 상태 |

UiPath 고정 HTML ID

- 입력: `request_id`, `customer_id`, `category`, `risk_level`, `summary`, 버튼 `submit-incident`
- `category`, `risk_level`은 드롭다운이다. UiPath는 Select Item으로 **화면에 보이는 한글 라벨**을 고른다. 요청 JSON 값도 이 라벨과 글자까지 같아야 한다.
  - `category`: `카드 분실`, `카드 부정사용 의심`, `결제 문의`, `일반 문의`, `기타`
  - `risk_level`: `낮음`, `보통`, `높음` (선택, 없으면 `없음`)
- AI는 `UNAUTHORIZED_TRANSACTION`, FDS는 `HIGH` 같은 영문 코드를 돌려준다. 요청 JSON을 만들 때 위 한글 라벨로 바꿔 넣는다. (서버에는 영문 코드로 저장된다.)
- 결과: 접수번호 `incident-id`, 입력 오류 `form-error`, 중복 요청 `duplicate-notice`
- 같은 `request_id`로 다시 접수하면 새로 만들지 않고 기존 접수번호를 보여준다.

스키마가 바뀌었으면 `database/finguard.db`를 지우고 `python database/seed.py`를 다시 실행한다.
