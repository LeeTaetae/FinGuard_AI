import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import create_autospec
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import pytest
from uipath.platform.orchestrator import Job
from uipath.platform.orchestrator._folder_service import FolderService
from uipath.platform.orchestrator._jobs_service import JobsService
from uipath.platform.common.paging import PagedResult

import app as web
from rpa.orchestrator_client import OrchestratorClient, OrchestratorError
from rpa.diagnose_orchestrator import diagnose
from test_automation import FIELDS, URL, client, create, decide, identity, rows


TOKEN = "robot-test-token-for-mocked-orchestrator"
TENANT = "https://cloud.uipath.com/test/DefaultTenant"


@pytest.fixture
def orchestrator(monkeypatch):
    from types import SimpleNamespace

    job_key, release_key, folder_key = (str(uuid4()) for _ in range(3))
    jobs = create_autospec(JobsService, instance=True)
    folders = create_autospec(FolderService, instance=True)
    folders.retrieve_key.return_value = folder_key
    sdk = SimpleNamespace(jobs=jobs, folders=folders)
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/odata/Releases")
            return httpx.Response(200, json={"value": [{"Key": release_key, "Name": "FinGuard_AI"}]})
        assert request.url.path.endswith("/UiPath.Server.Configuration.OData.StartJobs")
        return httpx.Response(200, json={"value": [{"Key": job_key, "State": "Pending"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        adapter = OrchestratorClient(sdk, http, base_url=TENANT, folder="Shared",
                                     process="FinGuard_AI", token="mock-token")
        monkeypatch.setattr(OrchestratorClient, "connect", lambda *_: adapter)
        monkeypatch.setenv("FINGUARD_ROBOT_TOKEN", TOKEN)
        monkeypatch.setitem(web.app.config, "AUTOMATION_AUTHORIZER",
                            lambda actor, permission: (
                                actor == "approver" or (actor == "operator" and permission in ("create", "read"))
                            ))
        yield SimpleNamespace(adapter=adapter, job_key=job_key, jobs=jobs, folders=folders,
                              calls=calls, release_key=release_key)


def action(client, request_id, name, actor="approver"):
    return client.post(f"{URL}/{request_id}/{name}", json={}, environ_overrides=identity(actor))


def robot(client, request_id, dispatch_id, token=TOKEN, **extra):
    return client.post(f"/api/automation/robot/requests/{request_id}/receipt",
                       json={"dispatch_id": dispatch_id, **extra},
                       headers={"Authorization": f"Bearer {token}"} if token else {})


def inputs(orchestrator):
    post, = (request for request in orchestrator.calls if request.method == "POST")
    start = json.loads(post.content)["startInfo"]
    assert (start["ReleaseKey"], start["JobsCount"], start["RuntimeType"]) == (
        orchestrator.release_key, 1, "Unattended",
    )
    return json.loads(start["InputArguments"])


def approved_request(client):
    request_id = create(client).json["request_id"]
    assert decide(client, request_id).status_code == 200
    return request_id


def test_pending_and_unprivileged_dispatch_are_blocked(client, orchestrator):
    request_id = create(client).json["request_id"]
    assert action(client, request_id, "dispatch").status_code == 409
    assert action(client, request_id, "dispatch", "operator").status_code == 403
    assert orchestrator.calls == []
    assert robot(client, request_id, uuid4().hex).status_code == 409


def test_concurrent_start_claims_once(client, orchestrator, monkeypatch):
    request_id = approved_request(client)
    wait = Barrier(2, timeout=5)
    release_key = orchestrator.adapter.release_key

    def discover():
        wait.wait()
        return release_key()

    monkeypatch.setattr(orchestrator.adapter, "release_key", discover)

    def dispatch(_):
        with web.app.test_client() as separate:
            return action(separate, request_id, "dispatch").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(dispatch, range(2))) == [200, 409]
    assert set(inputs(orchestrator)) == {"in_RequestId", "in_DispatchId"}
    assert action(client, request_id, "dispatch").status_code == 409
    assert len(rows("SELECT * FROM orchestrator_executions")) == 1


def sdk_job(orchestrator, state, *, arguments_in=None, output=None):
    return Job(Key=orchestrator.job_key, Id=1234, State=state,
               InputArguments=json.dumps(arguments_in), OutputArguments=json.dumps(output))


def test_robot_auth_snapshot_deduplication_and_success(client, orchestrator, monkeypatch):
    request_id = approved_request(client)
    dispatched = action(client, request_id, "dispatch")
    assert dispatched.json["external_execution_id"] == orchestrator.job_key
    assert dispatched.json["status"] == "QUEUED"
    orchestrator.folders.retrieve_key.assert_not_called()
    dispatch_id = inputs(orchestrator)["in_DispatchId"]
    # The bearer-authenticated robot endpoint has its own auth even with local login enabled.
    with monkeypatch.context() as local:
        local.setitem(web.app.config, "SECRET_KEY", "unit-test")
        assert robot(client, request_id, dispatch_id, token=None).status_code == 401
        assert robot(client, request_id, dispatch_id, token="wrong").status_code == 401
        assert robot(client, request_id, uuid4().hex).status_code == 409

        def receipt(_):
            with web.app.test_client() as separate:
                return robot(separate, request_id, dispatch_id, category="OTHER", summary="변조")

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(receipt, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    incident_id, = {response.json["incident_id"] for response in responses}
    incident, = rows("SELECT * FROM incidents WHERE request_id = ?", (request_id,))
    assert incident["category"] == FIELDS["category"] and incident["summary"] == FIELDS["summary"]
    assert incident["consultation_id"]

    orchestrator.jobs.retrieve.return_value = sdk_job(orchestrator, "running")
    assert action(client, request_id, "refresh-job").json["status"] == "RUNNING"
    orchestrator.jobs.retrieve.return_value = sdk_job(orchestrator, "successful", output={
        "out_RequestId": request_id, "out_IncidentId": incident_id, "out_Status": "DUPLICATE",
    })
    result = action(client, request_id, "refresh-job").json
    assert result["status"] == "SUCCEEDED" and result["incident_id"] == incident_id
    orchestrator.jobs.retrieve.assert_called_with(job_key=orchestrator.job_key, folder_path="Shared")
    events = client.get(f"{URL}/{request_id}/events", environ_overrides=identity("approver")).json
    assert sum(event["result_status"] == "RECEIVED" for event in events) == 1
    assert events[-1]["job_key"] == orchestrator.job_key
    assert events[-1]["incident_id"] == incident_id


def test_successful_job_without_verified_crm_receipt_fails(client, orchestrator):
    request_id = approved_request(client)
    assert action(client, request_id, "dispatch").status_code == 200
    orchestrator.jobs.retrieve.return_value = sdk_job(orchestrator, "Successful", output={
        "out_RequestId": request_id, "out_IncidentId": "INC-FAKE", "out_Status": "SUCCESS",
    })
    result = action(client, request_id, "refresh-job").json
    assert result["status"] == "FAILED" and result["incident_id"] is None
    assert result["orchestrator"]["job_state"] == "Successful"
    assert result["orchestrator"]["error_message"]


@pytest.mark.parametrize("code", [
    "ROBOT_CONFIG", "ROBOT_REQUEST_ID_MISSING", "ROBOT_DISPATCH_ID_MISSING",
    "ROBOT_TOKEN_MISSING", "ROBOT_CRM_URL_INVALID",
])
def test_workflow_configuration_errors_survive_job_reconciliation(client, orchestrator, code):
    request_id = approved_request(client)
    assert action(client, request_id, "dispatch").status_code == 200
    orchestrator.jobs.retrieve.return_value = sdk_job(orchestrator, "Successful", output={
        "out_RequestId": request_id, "out_Status": "FAILED", "out_Error": code,
    })
    refreshed = action(client, request_id, "refresh-job").json
    assert refreshed["status"] == "FAILED"
    assert refreshed["orchestrator"]["error_message"] == code
    assert refreshed["incident_id"] is None


def test_main_xaml_reads_process_then_user_and_logs_only_scope():
    namespace = "{http://schemas.uipath.com/workflow/activities}"
    root = ElementTree.parse(web.BASE_DIR / "uipath" / "Main.xaml").getroot()
    activity, = root.iter(namespace + "InvokeCode")
    source = activity.attrib["Code"]
    for name, variable in (("FINGUARD_ROBOT_TOKEN", "token"), ("FINGUARD_CRM_BASE_URL", "baseUrl")):
        get_variable = f'Environment.GetEnvironmentVariable("{name}", EnvironmentVariableTarget.'
        process = source.index(get_variable + "Process)")
        fallback = source.index(f"If String.IsNullOrWhiteSpace({variable}) Then", process)
        user = source.index(get_variable + "User)", fallback)
        assert user < source.index("End If", fallback)
        assert source.count(get_variable) == 2
    assert "EnvironmentVariableTarget.Machine" not in source
    scope_log, = (log for log in root.iter(namespace + "LogMessage")
                  if log.get("DisplayName") == "로봇 설정 조회 범위")
    assert scope_log.get("Message") == "[configScopes]"
    assert 'configScopes = "Token=" & tokenScope & " CrmUrl=" & urlScope' in source


def test_start_timeout_stays_claimed_and_recovers_job(client, orchestrator, monkeypatch):
    request_id = approved_request(client)
    original = orchestrator.adapter.http.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if method == "POST":
            raise httpx.ReadTimeout("sensitive network detail")
        return response

    monkeypatch.setattr(orchestrator.adapter.http, "request", request)
    failed = action(client, request_id, "dispatch")
    assert failed.status_code == 502 and "sensitive" not in failed.json["error"]
    assert action(client, request_id, "dispatch").status_code == 409
    orchestrator.jobs.list.return_value = PagedResult(items=[], has_more=False)
    assert action(client, request_id, "refresh-job").status_code == 409
    assert rows("SELECT job_state FROM orchestrator_executions")[0][0] == "UNKNOWN"
    orchestrator.jobs.list.return_value = PagedResult(
        items=[sdk_job(orchestrator, "Pending", arguments_in=inputs(orchestrator))], has_more=False,
    )
    orchestrator.jobs.retrieve.return_value = sdk_job(orchestrator, "Pending")
    result = action(client, request_id, "refresh-job").json
    assert result["external_execution_id"] == orchestrator.job_key
    assert result["status"] == "QUEUED"


@pytest.mark.parametrize(("code", "message"), [(401, "인증"), (403, "권한")])
def test_discovery_auth_errors_are_sanitized(orchestrator, code, message):
    request = httpx.Request("GET", TENANT)
    response = httpx.Response(code, request=request, text="private response")
    orchestrator.folders.retrieve_key.side_effect = httpx.HTTPStatusError(
        "private response", request=request, response=response,
    )
    with pytest.raises(OrchestratorError, match=message) as caught:
        orchestrator.adapter.resolve_folder()
    assert "private" not in str(caught.value)


def test_configured_folder_key_bypasses_sdk_navigation(orchestrator):
    adapter = orchestrator.adapter
    adapter.folder_key = str(uuid4())
    assert adapter.release_key() == orchestrator.release_key
    orchestrator.folders.retrieve_key.assert_not_called()
    request, = orchestrator.calls
    assert request.headers["X-UIPATH-FolderKey"] == adapter.folder_key
    assert "X-UIPATH-FolderPath-Encoded" not in request.headers


def test_diagnostics_isolate_sdk_403_and_redact_provider_response(orchestrator, monkeypatch, capsys):
    from uipath.platform.errors import EnrichedException

    request = httpx.Request("GET", TENANT, headers={"Authorization": "Bearer private-header"})
    response = httpx.Response(403, request=request, json={
        "errorCode": 1000, "message": "private-secret", "access_token": "private-token",
    })
    original = httpx.HTTPStatusError("private-error", request=request, response=response)
    wrapped = EnrichedException(original)
    wrapped.__cause__ = original
    orchestrator.folders.retrieve_key.side_effect = wrapped
    orchestrator.adapter.folder_key = str(uuid4())

    def connect(http, *, report_oauth):
        report_oauth(200)
        return orchestrator.adapter

    monkeypatch.setattr(OrchestratorClient, "connect", connect)
    assert diagnose(orchestrator.adapter.http, probe_sdk=True) == 0
    output = capsys.readouterr().out
    assert "POST /identity_/connect/token HTTP 200 OK" in output
    assert "GetFoldersForCurrentUser 403 1000" in output
    assert output.count("/orchestrator_/odata/Releases HTTP 200 OK") == 2
    assert "private" not in output and "Authorization" not in output
    assert {request.method for request in orchestrator.calls} == {"GET"}


def test_diagnostics_distinguishes_release_header_failure(orchestrator, monkeypatch, capsys):
    adapter = orchestrator.adapter
    adapter.folder_key = orchestrator.folders.retrieve_key.return_value
    original = adapter.http.request

    def request(method, url, **kwargs):
        if "X-UIPATH-FolderPath" in kwargs["headers"]:
            return httpx.Response(403, request=httpx.Request(method, url), json={"errorCode": 1000})
        return original(method, url, **kwargs)

    monkeypatch.setattr(adapter.http, "request", request)
    monkeypatch.setattr(OrchestratorClient, "connect", lambda *_, **__: adapter)
    assert diagnose(adapter.http) == 0
    output = capsys.readouterr().out
    assert "[Release / 폴더 경로 헤더] FAIL — GET /orchestrator_/odata/Releases 403 1000" in output
    assert "[Release / 폴더 Key 헤더] GET /orchestrator_/odata/Releases HTTP 200 OK" in output


@pytest.mark.parametrize(("folder", "header", "value"), [
    ("Shared", "X-UIPATH-FolderPath", "Shared"),
    ("공유", "X-UIPATH-FolderPath-Encoded", "9awgxw=="),
])
def test_folder_path_scopes_release_start_status_and_recovery(folder, header, value, monkeypatch):
    from types import SimpleNamespace
    from uipath.platform import UiPath

    release_key, job_key = str(uuid4()), str(uuid4())
    job = {"Key": job_key, "Id": 1, "State": "Pending", "InputArguments": json.dumps({
        "in_RequestId": "request", "in_DispatchId": "dispatch",
    })}
    calls = []

    def respond(request):
        path = request.url.path
        calls.append((request.method, path))
        assert request.headers[header] == value
        assert "X-UIPATH-FolderKey" not in request.headers
        if path.endswith("/odata/Releases"):
            return httpx.Response(200, json={"value": [{"Key": release_key, "Name": "FinGuard_AI"}]})
        if path.endswith(".StartJobs"):
            return httpx.Response(200, json={"value": [job]})
        if path.endswith("/odata/Jobs"):
            return httpx.Response(200, json={"value": [job]})
        assert ".GetByKey(identifier=" in path
        return httpx.Response(200, json=job)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        jobs = UiPath(base_url=TENANT, secret="mock-token").jobs
        monkeypatch.setattr(jobs, "request", lambda method, url, **kw: http.request(
            method, TENANT + str(url), **kw,
        ))
        folders = create_autospec(FolderService, instance=True)
        adapter = OrchestratorClient(SimpleNamespace(jobs=jobs, folders=folders), http,
                                     base_url=TENANT, folder=folder, process="FinGuard_AI",
                                     token="mock-token")
        assert adapter.release_key() == release_key
        assert adapter.start(release_key, "request", "dispatch").key == job_key
        assert adapter.retrieve(job_key).state == "Pending"
        assert adapter.recover("request", "dispatch") == job_key
        folders.retrieve_key.assert_not_called()
    assert [method for method, _ in calls] == ["GET", "POST", "GET", "GET"]


def test_path_only_diagnostics_succeed_without_sdk_probe(orchestrator, monkeypatch):
    monkeypatch.setattr(OrchestratorClient, "connect", lambda *_, **__: orchestrator.adapter)
    assert diagnose(orchestrator.adapter.http) == 0
    orchestrator.folders.retrieve_key.assert_not_called()


@pytest.mark.orchestrator_live
@pytest.mark.skipif(os.getenv("FINGUARD_RUN_ORCHESTRATOR_LIVE") != "1",
                    reason="requires external app credentials and a deployed process")
def test_published_process_discovery_live():
    with httpx.Client(timeout=15, transport=httpx.HTTPTransport(retries=0)) as http:
        assert OrchestratorClient.connect(http).release_key()
