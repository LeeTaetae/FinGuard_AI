"""Read-only Orchestrator diagnostics. Run with python -m rpa.diagnose_orchestrator."""

import argparse
import logging
from importlib.metadata import version

import httpx

from rpa.orchestrator_client import (
    FOLDER_PATH, RELEASE_PATH, TOKEN_PATH, OrchestratorClient, OrchestratorError,
)


def diagnose(http, *, probe_sdk=False):
    def oauth(status):
        if status is None:
            print("[OAuth] SKIP — 기존 토큰을 사용합니다.")
        else:
            print(f"[OAuth] POST {TOKEN_PATH} HTTP {status} OK")

    try:
        client = OrchestratorClient.connect(http, report_oauth=oauth)
    except OrchestratorError as exc:
        print(f"[OAuth/SDK 설정] FAIL — {exc}")
        return 1

    folder_key = None
    if probe_sdk:
        try:
            folder_key = client.resolve_folder()
            print(f"[SDK 폴더 조회] GET {FOLDER_PATH} OK — 대상 폴더 확인")
        except OrchestratorError as exc:
            print(f"[SDK 폴더 조회] FAIL — {exc}")
            if exc.status == 403:
                print("  Client Credentials에서는 이 SDK 탐색 API가 403을 반환할 수 있습니다.")

    key = client.folder_key or folder_key
    probes = [("폴더 경로 헤더", client.path_header)]
    if key:
        probes.append(("폴더 Key 헤더", {"X-UIPATH-FolderKey": key}))
        if client.folder_key and folder_key and client.folder_key.casefold() != folder_key.casefold():
            print("[폴더 설정] FAIL — 설정된 폴더 Key와 SDK 조회 결과가 다릅니다.")
            key_mismatch = True
        else:
            key_mismatch = False
    else:
        print("[폴더 Key 헤더] SKIP — 선택 설정")
        key_mismatch = False

    results = {}
    for label, header in probes:
        try:
            _, status = client.lookup_release(folder_headers=header)
            print(f"[Release / {label}] GET {RELEASE_PATH} HTTP {status} OK")
            results[label] = True
        except OrchestratorError as exc:
            print(f"[Release / {label}] FAIL — {exc}")
            results[label] = False

    if client.folder_key:
        return int(key_mismatch or not results["폴더 Key 헤더"])
    return int(not results["폴더 경로 헤더"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-sdk-folder", action="store_true", help="SDK 탐색 API도 별도로 진단합니다.")
    args = parser.parse_args()
    # The SDK's DEBUG logs include headers. Diagnostics emit only fixed fields.
    logging.disable(logging.CRITICAL)
    try:
        print(f"UiPath SDK {version('uipath')}")
        with httpx.Client(timeout=15, transport=httpx.HTTPTransport(retries=0)) as http:
            return diagnose(http, probe_sdk=args.probe_sdk_folder)
    except Exception:
        print("[진단 환경] FAIL — Python 환경, UiPath SDK 설치 및 네트워크 설정을 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
