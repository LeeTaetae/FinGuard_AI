import io
import re
from uuid import uuid4

import httpx
from openpyxl import load_workbook

import app as web
from excel_intake import create_app
from test_customer_portal import approve, customer, employee, submit, QUEUE
from test_local_auth import demo
from test_orchestrator import inputs, orchestrator, robot

TOKEN = "fixture-excel-intake-token-for-testing"


def site(tmp_path):
    app = create_app(token=TOKEN, workbook_path=tmp_path / "receipts.xlsx")
    app.testing = True
    return app.test_client()


def csrf(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)[1]


def sign_in(client):
    assert client.post("/login", data={"password": TOKEN, "csrf": csrf(client, "/login")}).status_code == 303


def submit_api(client, payload):
    return client.post("/api/drafts", json=payload, headers={"Authorization": f"Bearer {TOKEN}"})


def test_workbook_login_form_charts_and_literal_text(tmp_path):
    client = site(tmp_path)
    assert client.get("/records.xlsx").status_code == 302
    assert client.post("/api/drafts", json={}).status_code == 401
    sign_in(client)
    assert "아직 등록된 접수가 없습니다" in client.get("/records").text
    form = client.get("/").text
    key = re.search(r'name="request_id" value="([^"]+)"', form)[1]
    payload = {"request_id": key, "category": "LOST_CARD", "summary": "=2+3"}
    assert client.post("/", data=payload).status_code == 403
    assert client.post("/", data={**payload, "csrf": csrf(client, "/")}).status_code == 303
    api_payload = {"request_id": f"AUTO-{uuid4().hex}", "incident_id": "INC-20261125-424550",
                   "category": "LOST_CARD", "summary": "테스트 접수", "risk_level": "HIGH"}
    assert submit_api(client, api_payload).status_code == 201
    assert submit_api(client, api_payload).json["status"] == "DUPLICATE"
    assert submit_api(client, {**api_payload, "summary": "다른 내용"}).status_code == 409
    draft = "/drafts/" + api_payload["request_id"]
    assert api_payload["request_id"] in client.get("/").text
    assert api_payload["summary"] in client.get(draft).text
    assert api_payload["summary"] not in client.get("/records").text
    assert client.post(draft).status_code == 403
    confirmation = {"csrf": csrf(client, draft), "summary": "변조 시도"}
    assert client.post(draft, data=confirmation).status_code == 303
    assert client.post(draft, data=confirmation).status_code == 303
    assert submit_api(client, api_payload).json["status"] == "DUPLICATE"
    assert api_payload["request_id"] not in client.get("/").text
    records = client.get("/records").text
    assert "2건 · 100.0%" in records and "conic-gradient(" in records
    download = client.get("/records.xlsx")
    workbook = load_workbook(io.BytesIO(download.data))
    try:
        sheet = workbook["접수 목록"]
        assert sheet.max_row == 3
        assert sheet["F2"].value == "=2+3" and sheet["F2"].data_type == "s"
        assert sheet["F3"].value == api_payload["summary"]
    finally:
        workbook.close()


def test_staff_approval_robot_prefills_approved_snapshot_for_explicit_save(demo, orchestrator, monkeypatch, tmp_path):
    client = site(tmp_path)
    monkeypatch.setenv("FINGUARD_EXCEL_INTAKE_URL", "http://127.0.0.1:5001")
    monkeypatch.setenv("FINGUARD_EXCEL_INTAKE_TOKEN", TOKEN)
    delivered = []

    def forward(url, *, headers, json, **kwargs):
        assert url.endswith("/api/drafts")
        result = client.post("/api/drafts", headers=headers, json=json)
        delivered.append(json)
        return httpx.Response(result.status_code, json=result.json,
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(web.httpx, "post", forward)
    def check_ready(url, *, headers, **kwargs):
        result = client.get(url.removeprefix("http://127.0.0.1:5001"), headers=headers)
        return httpx.Response(result.status_code, json=result.json)

    monkeypatch.setattr(web.httpx, "get", check_ready)
    owner = customer("excel-test")
    report_path, _ = submit(owner)
    staff_path = QUEUE + "/" + report_path.rsplit("/", 1)[-1]
    agent = employee()
    approval = approve(agent, staff_path)
    assert approval.status_code == 303
    assert "data-excel-ready" in agent.get(approval.location).text
    assert agent.get(staff_path + "/excel-ready").json == {"url": None}
    assert delivered == []
    args = inputs(orchestrator)
    receipt = robot(owner, args["in_RequestId"], args["in_DispatchId"])
    assert receipt.status_code == 201
    assert robot(owner, args["in_RequestId"], args["in_DispatchId"]).status_code == 200
    assert delivered[0] == delivered[1]
    assert delivered[0]["incident_id"] == receipt.json["incident_id"]
    assert "customer_id" not in delivered[0] and "dispatch_id" not in delivered[0]
    assert not (tmp_path / "receipts.xlsx").exists()
    draft = "/drafts/" + args["in_RequestId"]
    handoff = agent.get(staff_path + "/excel-ready").json
    assert handoff["url"] == "http://127.0.0.1:5001/drafts/open"
    assert client.get(draft).status_code == 302
    assert client.post("/drafts/open", data={"ticket": "invalid"}).status_code == 403
    assert client.post("/drafts/open", data={"ticket": handoff["ticket"]}).location == draft
    assert client.post("/drafts/open", data={"ticket": handoff["ticket"]}).status_code == 403
    assert client.get("/records").status_code == 302
    assert receipt.json["incident_id"] in client.get(draft).text
    assert client.post(draft, data={"csrf": csrf(client, draft)}).status_code == 303
    assert "엑셀에 저장했습니다" in client.get(draft).text
    workbook = load_workbook(tmp_path / "receipts.xlsx")
    try:
        assert workbook.active.max_row == 2
    finally:
        workbook.close()
