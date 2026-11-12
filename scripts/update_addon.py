#!/usr/bin/env python3
"""Update the local ClaudeCodeBrowser install from this checkout (Windows), privately.

    python scripts/update_addon.py --bump

1. --bump raises the patch version in extension/manifest.json (Mozilla signs
   each version number only once).
2. Copies the server, native host and agent files to ~/.claudecodebrowser.
3. Builds the add-on and signs it as an unlisted add-on (credentials in .env.amo).
4. Opens it in Firefox and presses "Add" on the install prompt itself
   (scripts/confirm-addon-install.ps1). Installing reloads the add-on, which
   restarts the native host with its new code.
5. Restarts the MCP server (the native host relaunches it) and waits until healthy.
6. Checks that Firefox reports the new version.

Nothing is uploaded anywhere except the build to Mozilla for signing.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign_addon  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "extension" / "manifest.json"
INSTALL_DIR = Path.home() / ".claudecodebrowser"
ARTIFACTS = REPO / "web-ext-artifacts"
# Same files scripts/install.ps1 installs.
COMPONENTS = ["native-host/claudecodebrowser_host.py", "agent/browser_agent.py"] + [
    f"mcp-server/{name}" for name in
    ("server.py", "safety.py", "headless_backend.py", "stdio_wrapper.py", "mcp_config.json")]
HEALTH_URL = "http://127.0.0.1:8765/health"


def version_tuple(version):
    return tuple(int(part) for part in version.split("."))


def bump_patch(manifest_text):
    """Return (new manifest text, new version) with the patch number raised by one."""
    match = re.search(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', manifest_text)
    if not match:
        raise ValueError('no "version": "x.y.z" in the manifest')
    major, minor, patch = (int(n) for n in match.groups())
    new_version = f"{major}.{minor}.{patch + 1}"
    return manifest_text[:match.start()] + f'"version": "{new_version}"' + manifest_text[match.end():], new_version


def installed_versions(addon_id, profiles_glob=None):
    """Versions of the add-on in every Firefox profile (from extensions.json)."""
    profiles_glob = profiles_glob or os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox",
                                                  "Profiles", "*", "extensions.json")
    versions = []
    for path in glob.glob(profiles_glob):
        try:
            addons = json.loads(Path(path).read_text(encoding="utf-8")).get("addons", [])
        except (OSError, ValueError):
            continue
        versions += [a["version"] for a in addons if a.get("id") == addon_id]
    return versions


def firefox_path():
    try:
        import winreg
        key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"
        return winreg.QueryValue(winreg.HKEY_LOCAL_MACHINE, key)
    except OSError:
        return r"C:\Program Files\Mozilla Firefox\firefox.exe"


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, cwd=REPO, **kwargs)


def deploy_components():
    for rel in COMPONENTS:
        target = INSTALL_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    print(f"Copied {len(COMPONENTS)} files to {INSTALL_DIR}")


def build_and_sign(addon_id, version):
    signed = ARTIFACTS / f"claudecodebrowser-{version}-signed.xpi"
    if signed.exists():  # Mozilla signs each version only once; reuse it on a re-run
        print(f"Using the already signed {signed.name}")
        return signed
    run([shutil.which("npx") or "npx", "--yes", "web-ext@8.3.0", "build", "-s", "extension",
         "-a", str(ARTIFACTS), "--overwrite-dest"], stdout=subprocess.DEVNULL)
    zip_path = ARTIFACTS / f"claudecodebrowser-{version}.zip"
    print(f"Built {zip_path.name}; signing with Mozilla...")
    return sign_addon.sign(zip_path, addon_id, sign_addon.load_credentials(REPO / ".env.amo"), ARTIFACTS)


def mcp_call(tool, arguments):
    """Call a browser tool through the local MCP server (same token as the wrapper)."""
    token = (INSTALL_DIR / "api_token").read_text().strip()
    request = urllib.request.Request("http://127.0.0.1:8765/mcp/call", method="POST",
                                     data=json.dumps({"name": tool, "arguments": arguments}).encode(),
                                     headers={"Content-Type": "application/json", "X-API-Key": token})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.loads(resp.read())


def close_xpi_tabs():
    """Firefox opens an empty tab for each add-on file it installs; close them."""
    try:
        tabs = mcp_call("browser_find_tabs", {"title": ".xpi"}).get("tabs", [])
        for tab in tabs:
            mcp_call("browser_close_tab", {"tab_id": tab["id"]})
        if tabs:
            print(f"Closed {len(tabs)} leftover install tab(s).")
        return len(tabs)
    except OSError as e:
        print(f"Could not close the install tab(s): {e}")
        return 0


def wait_for_version(addon_id, version, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if version in installed_versions(addon_id):
            return True
        time.sleep(1)
    return False


def open_in_firefox(xpi):
    if close_xpi_tabs():  # closing a tab cancels an install still waiting there
        time.sleep(2)     # let those prompts go away before the new one opens
    subprocess.Popen([firefox_path(), str(xpi)])


def install(xpi, addon_name, addon_id, version):
    confirm = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(REPO / "scripts" / "confirm-addon-install.ps1"), "-AddonName", addon_name, "-TimeoutSeconds", "15"]
    # Verify instead of trusting the click, and open the file again when
    # Firefox showed no prompt for it.
    for _ in range(3):
        open_in_firefox(xpi)
        if subprocess.run(confirm, cwd=REPO).returncode == 0 and wait_for_version(addon_id, version, 20):
            print(f"Firefox reports {addon_name} {version} installed.")
            return
    raise RuntimeError(f"Firefox did not report {version} as installed.")


def wait_for_server(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                return json.loads(resp.read())
        except OSError:
            time.sleep(3)
    return None


def restart_server():
    """Stop the installed MCP server; the native host relaunches it with the new code."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "? { $_.CommandLine -match 'server\\.py' -and $_.CommandLine -notmatch 'claudecodebrowser_host' } | "
              "% { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    run(["powershell", "-NoProfile", "-Command", script])
    time.sleep(3)
    health = wait_for_server(90)
    print(f"Server back: {health}" if health else
          "Server not back within 90 s. It starts with the next Claude Code session or Firefox start.")


def main():
    parser = argparse.ArgumentParser(description="Update the local ClaudeCodeBrowser install from this checkout.")
    parser.add_argument("--bump", action="store_true", help="raise the patch version first")
    parser.add_argument("--extension-only", action="store_true", help="skip server and native host files")
    args = parser.parse_args()

    text = MANIFEST.read_text(encoding="utf-8")
    if args.bump:
        text, _ = bump_patch(text)
        MANIFEST.write_text(text, encoding="utf-8")
    manifest = json.loads(text)
    name, version = manifest["name"], manifest["version"]
    addon_id = manifest["browser_specific_settings"]["gecko"]["id"]
    current = installed_versions(addon_id)
    if current and max(map(version_tuple, current)) >= version_tuple(version):
        sys.exit(f"Installed {', '.join(current)} is not older than {version}: use --bump.")
    print(f"Updating {name} {', '.join(current) or '(not installed)'} -> {version}")

    if not args.extension_only:
        deploy_components()
    xpi = build_and_sign(addon_id, version)
    install(xpi, name, addon_id, version)
    if not args.extension_only:
        restart_server()
    # Reloading the add-on restarts the native host and, with it, the server;
    # the empty tab Firefox opened for the file can be closed once it is back.
    if wait_for_server(60):
        close_xpi_tabs()


if __name__ == "__main__":
    main()
