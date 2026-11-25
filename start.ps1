# Start the local CRM with the same project .env used by tests.
param(
    [string]$Python = 'python',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$mutex = [System.Threading.Mutex]::new($false, 'Global\FinGuardAI-CRM-127.0.0.1-5000')
$locked = $false
$previousSkipDotenv = $env:FLASK_SKIP_DOTENV
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $CheckOnly) {
        try {
            $locked = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $locked = $true
        }
        if (-not $locked) {
            throw 'Another FinGuard CRM launcher is already running.'
        }
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        if ($listeners | Where-Object { $_.Port -eq 5000 }) {
            throw 'TCP port 5000 is already in use. The existing process was left running.'
        }
    }

    $env:FLASK_SKIP_DOTENV = '1'
    $mode = if ($CheckOnly) { 'check' } else { 'start' }
    @'
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv
from uipath.llm_client.settings.platform.utils import is_token_expired

load_dotenv(Path(".env").resolve())


def value(name):
    return os.getenv(name, "")


def valid_https_url(raw, *, tenant_only=False):
    try:
        url = urlsplit(raw)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.query or url.fragment):
            return False
        if not tenant_only:
            return True
        segments = url.path.strip("/").split("/")
        return len(segments) == 2 and all(segments)
    except ValueError:
        return False


issues = []
for name in ("UIPATH_ACCESS_TOKEN", "UIPATH_URL", "UIPATH_TENANT_ID",
             "UIPATH_ORGANIZATION_ID", "FINGUARD_SECRET_KEY", "FINGUARD_ORCHESTRATOR_URL"):
    if not value(name).strip():
        issues.append(f"{name}: missing")

token = value("UIPATH_ACCESS_TOKEN")
if token.strip():
    try:
        if is_token_expired(token):
            issues.append("UIPATH_ACCESS_TOKEN: expired")
    except (TypeError, ValueError):
        issues.append("UIPATH_ACCESS_TOKEN: invalid expiry metadata")

for name, tenant_only in (("UIPATH_URL", False), ("FINGUARD_ORCHESTRATOR_URL", True)):
    if value(name).strip() and not valid_https_url(value(name), tenant_only=tenant_only):
        issues.append(f"{name}: invalid HTTPS URL")

robot_token = value("FINGUARD_ROBOT_TOKEN")
if len(robot_token) < 32 or any(not 33 <= ord(char) <= 126 for char in robot_token):
    issues.append("FINGUARD_ROBOT_TOKEN: expected at least 32 printable non-space ASCII characters")
if not value("FINGUARD_ORCHESTRATOR_TOKEN").strip():
    for name in ("FINGUARD_ORCHESTRATOR_CLIENT_ID", "FINGUARD_ORCHESTRATOR_CLIENT_SECRET"):
        if not value(name).strip():
            issues.append(f"{name}: missing (required without FINGUARD_ORCHESTRATOR_TOKEN)")
folder_key = value("FINGUARD_ORCHESTRATOR_FOLDER_KEY")
if folder_key:
    try:
        UUID(folder_key)
    except ValueError:
        issues.append("FINGUARD_ORCHESTRATOR_FOLDER_KEY: invalid UUID")
if value("FINGUARD_SECURE_COOKIE") == "1":
    issues.append("FINGUARD_SECURE_COOKIE: disable for local HTTP login")

if issues:
    for issue in issues:
        print(issue, file=sys.stderr)
    raise SystemExit(1)

print("FinGuard CRM environment check passed.", flush=True)
if sys.argv[1] == "start":
    from flask.cli import cli

    cli.main(args=["--app", "app", "run", "--host", "127.0.0.1", "--port", "5000",
                   "--no-reload", "--no-debugger"])
'@ | & $Python - $mode
    if ($LASTEXITCODE -ne 0) {
        throw "FinGuard CRM exited with code $LASTEXITCODE."
    }
} finally {
    $env:FLASK_SKIP_DOTENV = $previousSkipDotenv
    Pop-Location
    if ($locked) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
