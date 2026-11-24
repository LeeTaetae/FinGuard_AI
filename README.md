# FinGuard_AI

## UiPath Gateway 상담 분석

루트 Flask 앱과 `FinGuard_AI/`의 CRM·FDS 앱은 각각 해당 디렉터리에서 실행합니다.
두 앱 모두 `UiPathAzureChatOpenAI.with_structured_output(ConsultationResult)`로 Gateway를 호출합니다.

### 실행

Python 3.11 이상과 대상 테넌트의 UiPath Gateway 모델 접근 권한이 필요합니다.
사용할 앱 디렉터리에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
uipath auth
python app.py
```

인증에 사용한 `UIPATH_URL`, `UIPATH_ACCESS_TOKEN`, `UIPATH_TENANT_ID`,
`UIPATH_ORGANIZATION_ID`는 실행 환경이나 앱의 로컬 `.env`에 설정합니다.
`uipath auth`로 발급받은 토큰이 만료되면 다시 인증합니다.
`.env`, `.env.*` 및 `.uipath/`는 Git에서 무시합니다.
[UiPath SDK 인증 안내](https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/authenticating-with-interactive-login)

`UIPATH_LLM_MODEL`의 기본값은 `gpt-4.1-mini-2025-04-14`입니다.
다른 모델을 사용하려면 앱의 `.env` 또는 실행 환경에 이 변수를 설정합니다.
Gateway 인증에는 UiPath 자격 증명을 사용합니다.

`POST /api/ai/analyze`에 `{"conversation":"카드를 잃어버렸어요."}`를 보냅니다.
HTTP 200 응답에는 `category`, `recommended_action`, `requires_human_review`,
`suggested_reply`, `summary`가 포함됩니다.
인증, 시간 초과, 응답 형식 등의 오류에는 HTTP 502, `error`,
`recommended_action: ESCALATE_TO_AGENT`, `requires_human_review: true`를 반환합니다.

### 테스트

각 앱 디렉터리에서 아래 명령을 실행합니다. Mock 테스트는 UiPath 인증 없이 실행됩니다.
Gateway 테스트는 별도 실행하며, 모델에 보내는 입력은 가상의 상담 문장입니다.

```powershell
python -m pytest -m "not gateway"
$env:RUN_UIPATH_GATEWAY_TESTS = "1"
python -m pytest -m gateway -s
Remove-Item Env:RUN_UIPATH_GATEWAY_TESTS
```

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
