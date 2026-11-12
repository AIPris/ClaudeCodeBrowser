"""Issue #6: browser_upload_file attaches local files to an <input type=file>.

The server checks the paths against the safety policy, then serves each file
once from a short-lived URL that the extension fetches (native messaging caps
messages from the host at 1 MB, too small for photos or model files).
"""
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def files(temp_home):
    docs = temp_home / "Documents" / "uploads"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "cover.png").write_bytes(b"\x89PNG fake image")
    (docs / "model.zip").write_bytes(b"PK zip bytes")
    hidden = temp_home / ".ssh"
    hidden.mkdir(exist_ok=True)
    (hidden / "id_rsa").write_text("secret")
    profile = temp_home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "cookies.sqlite").write_bytes(b"cookies")
    library = temp_home / "Desktop" / "Library"
    library.mkdir(parents=True, exist_ok=True)
    (library / "Keychain.db").write_bytes(b"keys")
    (docs / ".env").write_text("API_KEY=secret")
    (docs / ".git").mkdir(exist_ok=True)
    (docs / ".git" / "config").write_text("[core]")
    (temp_home / "notes.txt").write_text("loose file in home")
    return docs


@pytest.fixture
def guard(server):
    from safety import SafetyGuard
    return SafetyGuard()


# --- path policy ------------------------------------------------------------

def test_files_in_the_content_folders_are_allowed(guard, files):
    resolved, denial = guard.check_upload_paths([str(files / "cover.png"), str(files / "model.zip")])
    assert denial is None
    assert [p.name for p in resolved] == ["cover.png", "model.zip"]


@pytest.mark.parametrize("make_path, reason", [
    (lambda home, docs: home / ".ssh" / "id_rsa", "outside"),
    (lambda home, docs: home / ".claudecodebrowser" / "api_token", "outside"),
    (lambda home, docs: docs / ".env", "hidden"),
    (lambda home, docs: docs / ".git" / "config", "hidden"),
    (lambda home, docs: docs / "missing.png", "not found"),
    (lambda home, docs: docs, "not a file"),
    (lambda home, docs: home.parent / "elsewhere.txt", "outside"),
    # Home itself is not an upload folder by default: AppData holds browser
    # profiles (cookies, logins) and other apps' credentials.
    (lambda home, docs: home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles" / "cookies.sqlite", "outside"),
    (lambda home, docs: home / "notes.txt", "outside"),
    # ... and app-data folders are refused even inside an allowed folder.
    (lambda home, docs: home / "Desktop" / "Library" / "Keychain.db", "application data"),
])
def test_unsafe_or_invalid_paths_are_refused(guard, files, temp_home, make_path, reason):
    target = make_path(temp_home, files)
    if reason == "outside" and not target.exists():
        target.write_text("x")
    resolved, denial = guard.check_upload_paths([str(target)])
    assert resolved is None
    assert denial["success"] is False
    assert reason in denial["error"]


def test_total_size_is_capped(guard, files):
    guard.config["max_upload_mb"] = 0.000001
    resolved, denial = guard.check_upload_paths([str(files / "cover.png")])
    assert resolved is None
    assert "too large" in denial["error"]


# --- one-time download URLs ---------------------------------------------------

@pytest.fixture
def live_server(server):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.MCPHTTPHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def _get(httpd, path):
    url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read(), resp.headers.get("Content-Type")


def test_a_file_url_works_once(server, live_server, files):
    token = server.upload_store.register(files / "cover.png")
    body, content_type = _get(live_server, f"/upload/{token}")
    assert body == b"\x89PNG fake image"
    assert content_type == "image/png"
    with pytest.raises(urllib.error.HTTPError) as second:
        _get(live_server, f"/upload/{token}")
    assert second.value.code == 404


def test_unknown_or_expired_tokens_are_refused(server, live_server, files, monkeypatch):
    with pytest.raises(urllib.error.HTTPError):
        _get(live_server, "/upload/not-a-token")
    token = server.upload_store.register(files / "cover.png")
    monkeypatch.setattr(server, "UPLOAD_URL_TTL", -1)
    with pytest.raises(urllib.error.HTTPError):
        _get(live_server, f"/upload/{token}")


# --- the tool ----------------------------------------------------------------

def test_upload_tool_sends_urls_not_bytes_and_cleans_up(server, files, monkeypatch):
    sent = {}

    def fake_dispatch(self, action, tab_id, arguments):
        sent.update(action=action, tab_id=tab_id, arguments=arguments)
        return {"success": True, "uploaded": len(arguments["files"])}
    monkeypatch.setattr(server.MCPHTTPHandler, "_dispatch_action", fake_dispatch)
    handler = server.MCPHTTPHandler.__new__(server.MCPHTTPHandler)

    result = handler.execute_tool("browser_upload_file", {
        "tab_id": 3, "selector": "input[type=file]",
        "paths": [str(files / "cover.png"), str(files / "model.zip")]})

    assert result == {"success": True, "uploaded": 2}
    assert sent["action"] == "uploadFile" and sent["tab_id"] == 3
    assert sent["arguments"]["selector"] == "input[type=file]"
    names = [f["name"] for f in sent["arguments"]["files"]]
    assert names == ["cover.png", "model.zip"]
    assert sent["arguments"]["files"][1]["type"] in ("application/zip", "application/x-zip-compressed")  # OS mime table
    token = sent["arguments"]["files"][0]["url"].rsplit("/", 1)[1]
    assert server.upload_store.take(token) is None  # unused URLs are revoked afterwards


def test_upload_tool_reports_policy_denials(server, files, temp_home):
    handler = server.MCPHTTPHandler.__new__(server.MCPHTTPHandler)
    result = handler.execute_tool("browser_upload_file", {
        "selector": "input[type=file]", "paths": [str(files / ".env")]})
    assert result["success"] is False
    assert "hidden" in result["error"]


def test_upload_is_blocked_in_read_only_mode(server, files):
    guard = server.get_safety_guard()
    guard.config["read_only"] = True
    try:
        handler = server.MCPHTTPHandler.__new__(server.MCPHTTPHandler)
        result = handler.execute_tool("browser_upload_file", {
            "selector": "input[type=file]", "paths": [str(files / "cover.png")]})
    finally:
        guard.config["read_only"] = False
    assert result["success"] is False
    assert "read-only" in result["error"]
