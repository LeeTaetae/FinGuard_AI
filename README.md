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
- 결과 판단 (UiPath는 제출 후 아래 중 어느 ID가 나타나는지로 판단한다)
  | 나타나는 ID | 의미 | UiPath 처리 |
  |---|---|---|
  | `incident-id` | 접수 성공, 접수번호 회수 | 성공 기록 |
  | `incident-id` + `duplicate-notice` | 이미 접수된 요청, 기존 번호 | 성공 기록 |
  | `form-error` | 입력값 오류 (400) | 재시도 금지, 실패 기록 |
  | `crm-error` | DB 일시 오류 (503) | **같은 request_id로** 재시도, 최대 3회. 그래도 실패하면 실패 기록 |
  | `crm-setup-error` | DB 미초기화 또는 예전 스키마 (500) | 재시도 금지, 실패 기록 (DB 재생성 필요) |
- 응답을 못 받았거나 중간에 끊겨도(타임아웃, 브라우저 종료) **같은 request_id로 다시 접수**하면 된다. 이미 저장됐으면 기존 번호가 나오고, 저장 전이었으면 새로 접수된다. 반쯤 저장되는 경우는 없다.

스키마가 바뀌었으면 `database/finguard.db`를 지우고 `python database/seed.py`를 다시 실행한다.
