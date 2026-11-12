#!/usr/bin/env python3
"""Sign an extension zip as an unlisted (private) add-on with Mozilla's AMO API.

Usage: python scripts/sign_addon.py <zip> [--env .env.amo] [--out web-ext-artifacts]

Credentials come from an env file with WEB_EXT_API_KEY / WEB_EXT_API_SECRET
(create them at https://addons.mozilla.org/developers/addon/api/key/).
web-ext's own `sign` fails on Windows with "Unsupported file type", hence this.
"""
import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://addons.mozilla.org/api/v5"


def load_credentials(env_file):
    lines = Path(env_file).read_text(encoding="utf-8").splitlines()
    values = dict(line.strip().split("=", 1) for line in lines if "=" in line)
    return values["WEB_EXT_API_KEY"], values["WEB_EXT_API_SECRET"]


def _jwt(key, secret):
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")
    now = int(time.time())
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iss": key, "jti": str(uuid.uuid4()), "iat": now, "exp": now + 60}).encode())
    sig = b64(hmac.new(secret.encode(), head + b"." + body, hashlib.sha256).digest())
    return (head + b"." + body + b"." + sig).decode()


def _call(creds, method, url, data=None, headers=None, raw=False):
    request = urllib.request.Request(url if url.startswith("http") else API + url, data=data, method=method,
                                     headers={"Authorization": "JWT " + _jwt(*creds), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = resp.read()
            return body if raw else json.loads(body or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {e.read()[:1500].decode(errors='replace')}")


def _multipart(fields, file_path):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; '
                 f'filename="{Path(file_path).name}"\r\nContent-Type: application/zip\r\n\r\n'.encode()
                 + Path(file_path).read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def sign(zip_path, addon_id, creds, out_dir, log=print, timeout=1200):
    """Upload, wait for validation and the signature, download. Returns the signed XPI path."""
    data, headers = _multipart({"channel": "unlisted"}, zip_path)
    upload = _call(creds, "POST", "/addons/upload/", data, headers)
    while not upload.get("processed"):
        time.sleep(5)
        upload = _call(creds, "GET", f"/addons/upload/{upload['uuid']}/")
    if not upload.get("valid"):
        errors = [m["message"] for m in upload["validation"]["messages"] if m["type"] == "error"]
        raise RuntimeError("Mozilla rejected the build: " + "; ".join(errors))
    log("Validated by Mozilla, waiting for the signature (usually 1-3 minutes)...")

    body = json.dumps({"version": {"upload": upload["uuid"]}}).encode()
    addon = _call(creds, "PUT", f"/addons/addon/{addon_id}/", body, {"Content-Type": "application/json"})
    version_id = (addon.get("version") or {}).get("id") or _call(
        creds, "GET", f"/addons/addon/{addon_id}/versions/?filter=all_with_unlisted")["results"][0]["id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        file_info = _call(creds, "GET", f"/addons/addon/{addon_id}/versions/{version_id}/").get("file") or {}
        if file_info.get("status") == "public" and file_info.get("url"):
            target = Path(out_dir) / f"{Path(zip_path).stem}-signed.xpi"
            target.write_bytes(_call(creds, "GET", file_info["url"], raw=True))
            return target
        time.sleep(10)
    raise RuntimeError("Timed out waiting for Mozilla to sign the build.")


def main():
    parser = argparse.ArgumentParser(description="Sign an extension zip as an unlisted add-on.")
    parser.add_argument("zip")
    parser.add_argument("--id", help="add-on ID (default: from extension/manifest.json)")
    parser.add_argument("--env", default=".env.amo")
    parser.add_argument("--out", default="web-ext-artifacts")
    args = parser.parse_args()
    addon_id = args.id or json.loads(Path("extension/manifest.json").read_text(encoding="utf-8"))[
        "browser_specific_settings"]["gecko"]["id"]
    print(sign(args.zip, addon_id, load_credentials(args.env), args.out))


if __name__ == "__main__":
    sys.exit(main())
