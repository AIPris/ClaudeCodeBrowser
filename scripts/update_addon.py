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
    run([shutil.which("npx") or "npx", "--yes", "web-ext@8.3.0", "build", "-s", "extension",
         "-a", str(ARTIFACTS), "--overwrite-dest"], stdout=subprocess.DEVNULL)
    zip_path = ARTIFACTS / f"claudecodebrowser-{version}.zip"
    print(f"Built {zip_path.name}; signing with Mozilla...")
    return sign_addon.sign(zip_path, addon_id, sign_addon.load_credentials(REPO / ".env.amo"), ARTIFACTS)


def install(xpi, addon_name, addon_id, version):
    subprocess.Popen([firefox_path(), str(xpi)])
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(REPO / "scripts" / "confirm-addon-install.ps1"), "-AddonName", addon_name])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if version in installed_versions(addon_id):
            print(f"Firefox reports {addon_name} {version} installed.")
            return
        time.sleep(1)
    raise RuntimeError(f"Firefox did not report {version} within 30 s.")


def restart_server():
    """Stop the installed MCP server; the native host relaunches it with the new code."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "? { $_.CommandLine -match 'server\\.py' -and $_.CommandLine -notmatch 'claudecodebrowser_host' } | "
              "% { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    run(["powershell", "-NoProfile", "-Command", script])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                print(f"Server back: {json.loads(resp.read())}")
                return
        except OSError:
            pass
    print("Server not back within 90 s. It starts with the next Claude Code session or Firefox start.")


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


if __name__ == "__main__":
    main()
