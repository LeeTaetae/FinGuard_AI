"""UiPath Jobs adapter, separate from the LLM Gateway configuration."""

import base64
import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx


TOKEN_PATH = "/identity_/connect/token"
FOLDER_PATH = "/orchestrator_/api/FoldersNavigation/GetFoldersForCurrentUser"
RELEASE_PATH = "/orchestrator_/odata/Releases"


class OrchestratorError(RuntimeError):
    def __init__(self, message, *, endpoint=None, status=None, code=None):
        self.endpoint = endpoint
        self.status = status
        self.code = code
        details = " ".join(str(item) for item in (endpoint, status, code) if item is not None)
        super().__init__(f"{details} — {message}" if details else message)


@dataclass(frozen=True)
class Job:
    key: str
    state: str
    outputs: dict


def arguments(value):
    if isinstance(value, str) and value.strip():
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _job(key, state, outputs):
    if not state:
        raise ValueError("Job state missing")
    return Job(str(UUID(str(key))), str(getattr(state, "value", state)).capitalize(), arguments(outputs))


def _error(exc, endpoint=None):
    status = None
    code = None
    timed_out = False
    cause = exc
    while cause is not None:
        response = getattr(cause, "response", None)
        status = getattr(response, "status_code", None) or getattr(cause, "status_code", None) or status
        timed_out |= isinstance(cause, (httpx.TimeoutException, TimeoutError))
        if endpoint and isinstance(response, httpx.Response):
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                error = body.get("error")
                raw_code = (error.get("code") if isinstance(error, dict) else error) or body.get("errorCode")
                if raw_code in ("invalid_client", "invalid_scope", "unauthorized_client",
                                "invalid_grant", "invalid_request", "unsupported_grant_type"):
                    code = raw_code
                elif type(raw_code) is int and 0 <= raw_code <= 999999:
                    code = str(raw_code)
        cause = cause.__cause__
    messages = {
        "invalid_client": "OAuth 클라이언트 자격 증명을 확인하세요.",
        "invalid_scope": "토큰 요청 범위와 External Application 설정을 확인하세요.",
        401: "Orchestrator 인증에 실패했습니다.",
        403: "Orchestrator가 이 API에 대한 접근 권한을 거부했습니다.",
        404: "Orchestrator API 경로 또는 대상 리소스를 확인하세요.",
    }
    message = messages.get(code) or messages.get(status)
    if timed_out or status in (408, 504):
        message = "Orchestrator 호출 시간이 초과되었습니다."
    return OrchestratorError(message or "Orchestrator 응답을 확인할 수 없습니다.",
                             endpoint=endpoint, status=status, code=code)


class OrchestratorClient:
    def __init__(self, sdk, http, *, base_url, folder, process, token, folder_key=None):
        self.sdk = sdk
        self.http = http
        self.base_url = base_url
        self.folder = folder
        self.process = process
        self.folder_key = folder_key
        self.headers = {"Authorization": f"Bearer {token}"}
        if folder.isascii():
            self.path_header = {"X-UIPATH-FolderPath": folder}
        else:
            encoded = base64.b64encode(folder.encode("utf-16-le")).decode("ascii")
            self.path_header = {"X-UIPATH-FolderPath-Encoded": encoded}

    @classmethod
    def connect(cls, http, *, report_oauth=None):
        from uipath.platform import UiPath

        base_url = os.getenv("FINGUARD_ORCHESTRATOR_URL", "").rstrip("/")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment or len(parsed.path.strip("/").split("/")) != 2):
            raise OrchestratorError("Orchestrator 테넌트 HTTPS 주소를 설정하세요.")
        folder = os.getenv("FINGUARD_ORCHESTRATOR_FOLDER", "Shared")
        process = os.getenv("FINGUARD_ORCHESTRATOR_PROCESS", "FinGuard_AI")
        configured_key = os.getenv("FINGUARD_ORCHESTRATOR_FOLDER_KEY")
        try:
            folder_key = str(UUID(configured_key)) if configured_key else None
        except ValueError:
            raise OrchestratorError("FINGUARD_ORCHESTRATOR_FOLDER_KEY에 Shared 폴더 UUID를 설정하세요.") from None
        token = os.getenv("FINGUARD_ORCHESTRATOR_TOKEN")
        token_status = None
        try:
            if not token:
                client_id = os.environ["FINGUARD_ORCHESTRATOR_CLIENT_ID"]
                secret = os.environ["FINGUARD_ORCHESTRATOR_CLIENT_SECRET"]
                identity_url = f"{parsed.scheme}://{parsed.netloc}{TOKEN_PATH}"
                response = http.post(identity_url, data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": os.getenv(
                        "FINGUARD_ORCHESTRATOR_SCOPE", "OR.Execution OR.Jobs.Read OR.Folders OR.Robots.Read",
                    ),
                })
                response.raise_for_status()
                token_status = response.status_code
                token = response.json()["access_token"]
                if not isinstance(token, str) or not token:
                    raise ValueError("Missing access token")
        except KeyError:
            raise OrchestratorError("Orchestrator 인증 환경변수 또는 토큰 응답을 확인하세요.",
                                    endpoint=f"POST {TOKEN_PATH}", status=token_status) from None
        except Exception as exc:
            raise _error(exc, f"POST {TOKEN_PATH}") from None
        if report_oauth:
            report_oauth(token_status)
        try:
            sdk = UiPath(base_url=base_url, secret=token)
        except Exception:
            raise OrchestratorError("UiPath SDK의 테넌트 또는 인증 설정을 확인하세요.",
                                    code="SDK_CONFIGURATION") from None
        return cls(sdk, http, base_url=base_url, folder=folder, process=process, token=token,
                   folder_key=folder_key)

    def _odata(self, method, path, *, folder_headers=None, **kwargs):
        if folder_headers is None:
            folder_headers = ({"X-UIPATH-FolderKey": self.folder_key}
                              if self.folder_key else self.path_header)
        response = self.http.request(
            method, f"{self.base_url}/orchestrator_/odata/{path}",
            headers={**self.headers, **folder_headers}, **kwargs,
        )
        response.raise_for_status()
        return response

    def resolve_folder(self):
        endpoint = f"GET {FOLDER_PATH}"
        try:
            folder_key = self.sdk.folders.retrieve_key(folder_path=self.folder)
        except Exception as exc:
            raise _error(exc, endpoint) from None
        if not folder_key:
            raise OrchestratorError("SDK 조회 결과에 대상 폴더가 없습니다.",
                                    endpoint=endpoint, status=200, code="FOLDER_NOT_FOUND")
        return folder_key

    def lookup_release(self, *, folder_headers=None):
        endpoint = f"GET {RELEASE_PATH}"
        status = None
        try:
            response = self._odata("GET", "Releases", folder_headers=folder_headers, params={
                "$filter": "Name eq '" + self.process.replace("'", "''") + "'",
                "$select": "Key,Name",
                "$top": 2,
            })
            status = response.status_code
            releases = response.json()["value"]
            if len(releases) != 1 or releases[0]["Name"] != self.process:
                raise OrchestratorError("대상 폴더에서 게시된 프로세스 하나를 확인할 수 없습니다.",
                                        endpoint=endpoint, status=status, code="RELEASE_MATCH_COUNT")
            return str(UUID(releases[0]["Key"])), status
        except OrchestratorError:
            raise
        except Exception as exc:
            if status:
                raise OrchestratorError("Release 응답 형식을 확인하세요.", endpoint=endpoint,
                                        status=status, code="INVALID_RELEASE_RESPONSE") from None
            raise _error(exc, endpoint) from None

    def release_key(self):
        key, _ = self.lookup_release()
        return key

    def _sdk_folder(self):
        return {"folder_key": self.folder_key} if self.folder_key else {"folder_path": self.folder}

    def start(self, release_key, request_id, dispatch_id):
        # The SDK's default request policy can retry POST. Send StartJobs once over
        # the dedicated, non-retrying transport and recover via read-only SDK calls.
        try:
            rows = self._odata("POST", "Jobs/UiPath.Server.Configuration.OData.StartJobs", json={
                "startInfo": {
                    "ReleaseKey": release_key,
                    "Strategy": "JobsCount",
                    "JobsCount": 1,
                    "RuntimeType": "Unattended",
                    "InputArguments": json.dumps({
                        "in_RequestId": request_id, "in_DispatchId": dispatch_id,
                    }),
                },
            })
            row, = rows.json()["value"]
            return _job(row["Key"], row["State"], row.get("OutputArguments"))
        except Exception as exc:
            raise _error(exc) from None

    def retrieve(self, key):
        try:
            job = self.sdk.jobs.retrieve(job_key=key, **self._sdk_folder())
            return _job(job.key, job.state, job.output_arguments)
        except Exception as exc:
            raise _error(exc) from None

    def recover(self, request_id, dispatch_id):
        try:
            matches = set()
            jobs = self.sdk.jobs
            for skip in range(0, 10001, 100):
                page = jobs.list(**self._sdk_folder(), orderby="Id desc", skip=skip, top=100)
                for job in page.items:
                    inputs = arguments(job.input_arguments)
                    if (inputs.get("in_RequestId"), inputs.get("in_DispatchId")) == (request_id, dispatch_id):
                        matches.add(str(UUID(str(job.key))))
                if not page.has_more:
                    if len(matches) > 1:
                        raise OrchestratorError("같은 실행 식별자로 생성된 Job이 여러 개입니다.")
                    return matches.pop() if matches else None
            raise OrchestratorError("Job 조회 범위를 초과했습니다. Orchestrator에서 실행 식별자를 확인하세요.")
        except OrchestratorError:
            raise
        except Exception as exc:
            raise _error(exc) from None
