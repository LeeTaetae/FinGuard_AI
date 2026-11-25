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
python -m pytest -m "not gateway and not orchestrator_live"
$env:RUN_UIPATH_GATEWAY_TESTS = "1"
python -m pytest -m gateway -v -s -o log_cli=true --log-cli-level=WARNING
Remove-Item Env:RUN_UIPATH_GATEWAY_TESTS
```

Gateway 오류 로그에는 `stage`, `reason`, `gateway_http`만 기록됩니다.
`stage=configure reason=token_expired gateway_http=-`이면 SDK 초기화에서 만료를
감지한 상태입니다. 실제 Gateway HTTP 요청은 이 단계 전에 차단됩니다.
앱 디렉터리에서 `uipath auth --force`로 재인증하고 Flask를 재시작합니다.
PowerShell에 오래된 `UIPATH_ACCESS_TOKEN`이 남아 있으면 로컬 `.env`보다 우선합니다.
갱신된 `.env`를 쓰려면 `Remove-Item Env:UIPATH_ACCESS_TOKEN -ErrorAction SilentlyContinue`로
해당 셸의 토큰 변수를 제거합니다. `FINGUARD_ORCHESTRATOR_*`는 별도 설정입니다.

라이브 테스트는 `/api/ai/analyze`와 로그인·CSRF를 거치는 `/api/automation/requests`를
각각 실제 Gateway로 호출합니다. 상담 요청 테스트는 임시 SQLite DB와 임시 상담원 계정을
사용하고, 저장된 AI 결과와 같은 멱등 키의 재전송을 검증합니다. Job 생성은 수행하지 않습니다.

CLI와 pytest의 환경을 맞추려면 CRM 저장소에서 테스트한 같은 PowerShell과 Python을
사용합니다. Flask CLI가 상위 디렉터리의 dotenv 파일을 먼저 읽는 경로를 피하려면
`$env:FLASK_SKIP_DOTENV = "1"`을 설정한 뒤
`python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload`로 실행합니다.
앱은 자체 CRM 디렉터리의 `.env`를 읽습니다. 시작 전 기존 서버를 종료하고
`netstat -ano -p tcp`에서 5000 포트의 리스너가 하나인지 확인합니다.

같은 상담 입력과 `Idempotency-Key`의 재전송은 이미 저장된 요청을 반환합니다.
토큰을 복구한 뒤 같은 내용으로 AI를 다시 호출하려면 새 상담 화면에서 새 요청을 등록합니다.
테스트 중 중복 승인 방지를 위해 이전 검토 요청은 승인 대기 목록에서 정리할 수 있습니다.

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
  | `crm-setup-error` | DB 미초기화 또는 예전 스키마 (500) | 재시도 금지, 실패 기록 (DB 초기화 또는 마이그레이션 필요) |
- 응답을 못 받았거나 중간에 끊겨도(타임아웃, 브라우저 종료) **같은 request_id로 다시 접수**하면 된다. 이미 저장됐으면 기존 번호가 나오고, 저장 전이었으면 새로 접수된다. 반쯤 저장되는 경우는 없다.

## 자동화 요청 기반

기존 DB에는 저장소 루트에서 `python -m database.migrate`를 실행합니다.
이 명령은 기존 데이터를 보존하며 반복 실행할 수 있습니다.
구형 사고 스키마에서는 기존 접수번호와 상태, 위험 점수 및 RPA 상태를 보존하고
상담에서 고객·분류·요약을 연결해 현재 CRM 스키마로 옮깁니다.
다른 파일을 지정하려면 `python -m database.migrate --db 경로`를 사용합니다.
새 DB는 `python database/seed.py` 실행 시 같은 마이그레이션이 적용됩니다.

신규 API는 로그인한 상담원 권한을 확인합니다. 외부 WSGI 인증 모드는 서버에서
신뢰된 사용자 식별자와 권한 검증기가 구성된 경우에만 사용할 수 있습니다.
HTTP 사용자 헤더나 JSON의 승인자 값은 신원으로 사용하지 않습니다.

- `create`와 `read` 권한이 있으면 상담 화면에서 자동화 요청을 생성할 수 있습니다.
- `read` 권한으로 본인 요청을 조회할 수 있습니다. `decide` 권한도 있으면 다른 사용자의 요청을 검토할 수 있습니다.
- `read`와 `decide` 권한이 있으면 승인하거나 거부할 수 있습니다.
- 기존 모의 CRM 직접 접수는 계속 사용할 수 있습니다. 신규 `AUTO-` 요청 ID는 기존 폼에서 신규 접수할 수 없습니다.

| API | 요청과 결과 |
|---|---|
| `POST /api/automation/requests` | JSON `customer_id`, `conversation`과 `Idempotency-Key` 헤더. 최초 201, 같은 사용자가 같은 키·입력으로 재전송하면 200과 기존 요청. 같은 키에 다른 입력은 409 |
| `GET /api/automation/requests/<request_id>` | 요청 상태, AI·FDS 스냅샷, 결정·실행 식별자 조회 |
| `GET /api/automation/requests/<request_id>/events` | 생성과 상태 변경 이력 조회 |
| `POST /api/automation/requests/<request_id>/approve` | 최종 CRM `category`, `summary`, 선택적 `risk_level`을 JSON으로 전송 |
| `POST /api/automation/requests/<request_id>/reject` | JSON `{}`로 거부 기록 |

AI 호출이 실패하면 상담 원문과 FDS 결과를 보존하고 `ai_result=null`,
`PENDING_APPROVAL`, `review_required=true`로 저장합니다. 이때 승인하려면 검토자가
접수 분류와 요약을 직접 작성해야 합니다.

`APPROVED`와 `dispatch_pending=true`인 요청은 아래 Orchestrator 연동에서 실행할 수 있습니다.

## 로컬 상담원 로그인

저장소 루트에서 DB 마이그레이션을 적용하고 필요한 시연용 계정만 생성합니다.
계정 생성 명령은 비밀번호를 입력받아 해시로 저장합니다. 비밀번호는 12~128자입니다.

```powershell
python -m database.migrate
python -m flask --app app create-user demo-agent --role AGENT
python -m flask --app app create-user demo-supervisor --role SUPERVISOR
python -m flask --app app create-user demo-admin --role ADMIN
$env:FINGUARD_SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))").Trim()
python -m flask --app app run --host 127.0.0.1 --port 5000
```

`http://127.0.0.1:5000/login`에서 로그인합니다. AGENT는 요청을 생성하고 자신의
요청을 조회할 수 있습니다. SUPERVISOR와 ADMIN은 승인 대기 목록에서 다른 상담원의
요청을 읽고 승인·거부할 수 있습니다. ADMIN은 계정 목록도 볼 수 있습니다.

- 세션 키를 설정한 실행에서는 Flask 로그인만 상담원 신원으로 사용합니다.
  키를 설정하지 않은 기존 실행에서는 로컬 로그인이 비활성화되고, 기존
  `REMOTE_USER` 연동은 별도 권한 검증기가 구성된 경우에만 사용됩니다.
- 세션은 최대 8시간 유지되며 로그아웃하면 서버에서도 즉시 폐기됩니다.
  JSON 변경 요청은 `X-CSRF-Token`을, HTML 폼은 숨겨진 토큰 필드를 사용합니다.
  로컬 HTTP 설정은 루프백 시연용입니다. HTTPS 배포에서는
  `FINGUARD_SECURE_COOKIE=1`을 설정하고 재시작 간 유지할 세션 키를 외부 비밀 저장소에서 주입합니다.
- 로봇은 별도 Bearer 토큰과 요청별 실행 식별자로 전용 API에 인증합니다.
- 인증된 모드에서 기존 Studio 양식을 사용하려면 브라우저가 로그인 상태여야 합니다.
  URL과 고정 HTML ID는 유지했습니다.

## UiPath Orchestrator Jobs 연동

### 실제 E2E 결과

2026-11-25 KST에 DefaultTenant의 Shared 폴더에서 **FinGuard_AI 1.0.3**을 Production
Unattended로 실행했습니다. 상담 분석과 승인 이후 Orchestrator는 `Successful`,
로봇은 `SUCCESS`, CRM은 `SUCCEEDED`를 기록했습니다. RPA 실행 시간은 5.086초였습니다.
SQLite에서 승인·Job·로봇 접수 감사 이력이 연결되고 같은 요청의 사고가 정확히 한 건임을
확인했습니다. 이전 `ROBOT_CONFIG` 실패 요청의 사고는 0건이었습니다.
실행 로그에는 패키지 1.0.3과 `Token=User CrmUrl=User`가 기록됐습니다.

### 로컬 시작

기존 DB에 마이그레이션이 필요하면 저장소 루트에서 `python -m database.migrate`를
실행합니다. 같은 PowerShell의 환경 또는 Git에서 제외된 프로젝트 `.env`에 Gateway,
Orchestrator, Flask 세션 키와 로봇 토큰 설정을 준비합니다. Flow 실행에 사용할
`FINGUARD_CRM_BASE_URL`은 로봇의 실행 환경에 설정합니다.

```powershell
.\start.ps1 -CheckOnly
.\start.ps1
```

`-CheckOnly`는 설정만 검증합니다. Conda 등에서 다른 Python을 선택하려면
`-Python 'C:\path\to\python.exe'`를 넘깁니다. 일반 시작은 시스템 전역 뮤텍스로
동시 실행을 막고 5000 포트가 열려 있으면 기존 서버를 유지한 채 종료합니다. 검증
오류는 변수 이름과 원인만 표시합니다. 서버는 루프백에서 포그라운드로 실행됩니다.

CRM에서 테스트 상담을 등록·분석하고 승인자가 검토합니다. 실행이 허용된 승인 요청에서
**자동화 실행**을 한 번 누른 뒤 **Job 상태 조회**로 성공과 접수번호를 대조합니다.
이미 실행된 요청에는 기존 Job을 조회합니다. 아래 라이브 pytest는 Gateway와
게시 프로세스를 각각 확인하며 Orchestrator Job을 생성하지 않습니다.

승인 대기 화면에서 **승인된 요청 및 Job 이력**을 열고 요청을 선택합니다. SUPERVISOR와
ADMIN에게 **자동화 실행** 및 **Job 상태 조회**가 제공됩니다. 외부 WSGI 인증 모드는
서버 권한 검증기에서 `read`, `decide`, `dispatch`를 부여한 사용자로 운영합니다.
실행할 때 서버가 승인자와 승인 이력을 확인하고 DB에 실행을 선점한 후 Job을 한 번 시작합니다.
실행 응답을 잃으면 `UNKNOWN`으로 기록합니다. 상태 조회는 폴더의 기존 Job에서 요청 ID와
실행 식별자를 대조해 Job을 복구합니다. 검색 결과가 아직 없으면 실행은 잠긴 상태로 유지됩니다.

### 환경변수

| 환경변수 | 설정 |
|---|---|
| `FINGUARD_ORCHESTRATOR_URL` | Flask 환경의 테넌트 URL. 예: `https://cloud.uipath.com/<organization>/DefaultTenant` |
| `FINGUARD_ORCHESTRATOR_FOLDER` | Flask 환경. 기본값 `Shared`. Release와 Job 요청에 폴더 경로를 직접 전달합니다. |
| `FINGUARD_ORCHESTRATOR_FOLDER_KEY` | 선택 설정. 값이 있으면 폴더 경로 대신 이 UUID를 사용합니다. |
| `FINGUARD_ORCHESTRATOR_PROCESS` | Flask 환경. 기본값 `FinGuard_AI`. Shared에 배포된 프로세스 표시 이름과 일치해야 합니다. |
| `FINGUARD_ORCHESTRATOR_CLIENT_ID`, `FINGUARD_ORCHESTRATOR_CLIENT_SECRET` | Flask 환경의 External Application 클라이언트 자격 증명 |
| `FINGUARD_ORCHESTRATOR_SCOPE` | Flask 환경. 기본값 `OR.Execution OR.Jobs.Read OR.Folders OR.Robots.Read`. 앱에 등록된 범위에 맞춥니다. |
| `FINGUARD_ORCHESTRATOR_TOKEN` | Flask 환경. 진단 등에 사용할 단기 토큰. 지정 시 Client ID와 Secret 대신 사용합니다. |
| `FINGUARD_ROBOT_TOKEN` | Flask와 Robot 업무 프로세스 양쪽에 주입할 동일한 32자 이상 ASCII 비밀값 |
| `FINGUARD_CRM_BASE_URL` | Robot 업무 프로세스에서 접근 가능한 CRM 기본 URL. 로컬 시연은 `http://127.0.0.1:5000`, 다른 호스트는 HTTPS를 사용합니다. |

1.0.3의 `Main.xaml`은 로봇용 토큰과 CRM URL을 각각 **Process → User** 순서로 읽습니다.
Process 값이 없거나 공백이면 Executor를 실행하는 Windows 계정의 User 값을 읽습니다.
Machine 범위를 조회하는 별도 호출은 없습니다. 로봇 로그에는 `Token=User CrmUrl=Process`
처럼 조회 범위만 남으며, 값이 없거나 조회 전에 종료되면 `Missing` 또는 `Unread`로 표시합니다.
Flask는 계속 프로세스 환경의 토큰을 쓰므로 로봇과 같은 비밀값을 주입합니다.

로봇 설정 오류는 `ROBOT_REQUEST_ID_MISSING`, `ROBOT_DISPATCH_ID_MISSING`,
`ROBOT_TOKEN_MISSING`, `ROBOT_CRM_URL_INVALID`로 구분됩니다. Flask의 Job 상태 조회는
1.0.1의 `ROBOT_CONFIG`와 새 패키지의 세부 오류 코드를 모두 표시합니다.

토큰과 Secret은 비밀 저장소 또는 실행 환경으로 주입합니다. Windows 로그인 자격 증명은
Orchestrator에서 관리합니다. External Application에 `Shared`의 프로세스 조회 및
Job 생성·조회 권한을 직접 할당합니다. Release 조회 및 `StartJobs`는 인증된 HTTP
클라이언트로 폴더 헤더를 보냅니다. Job 조회와 응답 유실 복구는 SDK 2.15.6의
`jobs.retrieve`와 `jobs.list`에 같은 폴더 경로 또는 Key를 전달합니다. ASCII 경로는
`X-UIPATH-FolderPath`, 그 외 경로는 `X-UIPATH-FolderPath-Encoded`를 사용합니다.
`StartJobs`는 재전송을 차단한 단일 HTTP 요청으로 보내며 Job UUID Key를 저장합니다.

### API 및 DB

| API | 용도 |
|---|---|
| `POST /api/automation/requests/<request_id>/dispatch` | 승인 요청을 실행합니다. JSON `{}`와 실행 권한이 필요합니다. |
| `POST /api/automation/requests/<request_id>/refresh-job` | 기존 Job을 찾거나 상태와 출력을 조회합니다. JSON `{}`와 실행 권한이 필요합니다. |
| `POST /api/automation/robot/requests/<request_id>/receipt` | Bearer 인증과 JSON `{"dispatch_id":"..."}`로 접수합니다. 최초 201, 중복 200이며 동일 접수번호를 반환합니다. |
| `GET /consultation/executions` | 승인된 실행 대기 요청과 Job 이력을 표시합니다. |

로컬 로그인 모드에서 상담원 POST는 기존 CSRF 토큰을 사용합니다. 로봇은 고객 ID, 분류,
요약, 위험도를 서버의 승인 스냅샷에서 읽습니다. Job이 `Successful`이어도 출력의 요청 ID와
접수번호가 CRM의 실제 레코드와 일치해야 업무가 `SUCCEEDED`가 됩니다.

`python -m database.migrate`는 `orchestrator_executions`에 실행 식별자, Job Key,
상태 및 결과를 기록할 구조를 추가합니다. `orchestrator_execution_events`는 기존
`automation_events`에 Job 및 접수 세부 정보를 연결합니다.

### Studio 확인 및 Publish

1. Studio Desktop에서 `uipath/project.json`을 열고 `Main.xaml`의 Arguments를 확인합니다.
   입력은 `in_RequestId`, `in_DispatchId`이고 출력은 `out_RequestId`, `out_IncidentId`,
   `out_Status`, `out_Error`입니다. 모두 String입니다.
2. Validate File과 Validate Project를 실행합니다. 입력이 빈 로컬 테스트에서는
   `Data/Config.json`의 경로와 브라우저 로그인 상태를 확인하고 기존 `request.json`,
   화면 셀렉터, `result.json` 및 이력 기반 중복 방지를 검증합니다. Job 경로에서는
   자동화 요청과 함께 두 입력을 전달하고 Invoke Code VB 컴파일, Robot 환경변수,
   CRM 네트워크 접근 및 출력 인수 반환을 확인합니다.
3. Studio의 **Publish**에서 배포할 버전을 확인하고 **Orchestrator Tenant Processes Feed**로 게시합니다.
   Orchestrator **DefaultTenant → Shared → Automations → Processes**에서 패키지와
   `Main.xaml`을 선택해 프로세스를 추가하거나 기존 `FinGuard_AI`의 패키지 버전을 올립니다.
   `FinGuard-Robot`과 `FinGuard-Machine`의 폴더 할당 및 Production Unattended 슬롯을 확인합니다.
   Job은 사용 가능한 할당을 동적으로 선택합니다.
4. Flask를 실행하고 테스트 상담을 생성·승인한 뒤 CRM에서 자동화 실행을 누릅니다.
   Shared의 Jobs에서 동일 UUID와 Robot 할당을 확인하고, CRM에서 Job 상태를 조회해
   `out_IncidentId`와 사고 상세 화면의 접수번호를 대조합니다. Job 경로는 로컬 JSON 파일을 사용하지 않습니다.

### 테스트 실행

```powershell
python -m pytest -m "not gateway and not orchestrator_live"
$env:FINGUARD_ORCHESTRATOR_SCOPE = "OR.Default"
$env:FINGUARD_ORCHESTRATOR_FOLDER = "Shared"
Remove-Item Env:FINGUARD_ORCHESTRATOR_FOLDER_KEY -ErrorAction SilentlyContinue
python -m rpa.diagnose_orchestrator
$env:FINGUARD_RUN_ORCHESTRATOR_LIVE = "1"
python -m pytest -m orchestrator_live -v -s
```

라이브 pytest는 배포 프로세스 조회만 수행합니다. 실제 Job 발행과 CRM 접수 확인은 위의
Studio·Orchestrator 절차에서 별도로 수행합니다.

### 403 읽기 전용 진단

자격 증명을 설정한 PowerShell에서 `python -m rpa.diagnose_orchestrator`를 실행합니다.
`OR.Default`를 사용한다면 `FINGUARD_ORCHESTRATOR_SCOPE`에 명시합니다. 기존 토큰
환경변수가 설정된 경우 OAuth 발급 단계는 건너뜁니다. Client Credentials 자체를
확인할 때는 해당 변수만 제거한 뒤 진단합니다.

SDK 2.15.6의 `GetFoldersForCurrentUser`는 Client Credentials에서 403을 반환할 수
있습니다. 일반 연결과 기본 진단은 이 API 없이 폴더 경로를 사용합니다. 탐색 API를
별도로 확인하려면 `python -m rpa.diagnose_orchestrator --probe-sdk-folder`를 실행합니다.
선택 설정으로 Shared의 `Key` UUID를 지정하면 진단에서 두 헤더 방식을 비교합니다.
Release 조회의 `Processes: View`는 Shared의 Automation User 역할에 포함됩니다.
`OR.Default`의 역할 기반 접근에는 Confidential 앱에 직접 할당한 역할이 적용됩니다.
스크립트는 OAuth와 선택된 조회 단계를 구분해서 출력하고, 서버 원문 대신
허용된 오류 코드와 고정 메시지를 사용합니다. Job 생성과 실행 API는 호출하지 않습니다.
