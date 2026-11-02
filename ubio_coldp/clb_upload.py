"""Upload all ColDP archives found in output/ to ChecklistBank.

    ./upload.sh                          # every archive in output/, smallest first
    ./upload.sh ubio-namebank 8-fish-w-t-eschmeyer
    ./upload.sh --skip=ubio-namebank
    ./upload.sh --dry-run                # only list what would be uploaded

Asks for the ChecklistBank username and password and logs in for a JWT (the password is never stored or passed on
a command line). For non-interactive use, set CLB_TOKEN to a JWT instead. Set CLB_API to use another environment,
e.g. https://api.dev.checklistbank.org.

Dataset keys are kept in config/checklistbank.yaml (archive name -> dataset key). An archive that is not listed yet
gets a new private dataset (origin external), and its key is added to the file. The import replaces the dataset
metadata with the archive's metadata.yaml.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import yaml

from .disk import OUTPUT, ROOT

API = os.environ.get("CLB_API", "https://api.checklistbank.org").rstrip("/")
KEYS = ROOT / "config" / "checklistbank.yaml"
NOMENCLATURAL = {"nomenclator-zoologicus", "index-animalium"}


def _request(method: str, path: str, token: str | None = None, data=None, headers: dict | None = None,
             basic: tuple[str, str] | None = None) -> str:
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers or {})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    elif basic:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} failed: HTTP {e.code} {e.read().decode()[:500]}") from None


def login() -> str:
    token = os.environ.get("CLB_TOKEN")
    if token:
        return token
    print(f"ChecklistBank login ({API})")
    user = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    token = _request("GET", "/user/login", basic=(user, password)).strip().strip('"')
    print("logged in", flush=True)
    return token


def archives() -> dict[str, Path]:
    return {z.stem: z for z in sorted(OUTPUT.rglob("*.zip"))}


def load_keys() -> dict[str, int]:
    return (yaml.safe_load(KEYS.read_text()) or {}) if KEYS.exists() else {}


def save_keys(keys: dict[str, int]) -> None:
    KEYS.write_text("# ChecklistBank dataset keys of the generated archives\n" + yaml.safe_dump(keys, sort_keys=True))


def create_dataset(token: str, name: str, z: Path) -> int:
    md = yaml.safe_load(zipfile.ZipFile(z).read("metadata.yaml"))
    body = {"title": md["title"], "alias": md.get("alias"), "origin": "external",
            "type": "nomenclatural" if name in NOMENCLATURAL else "taxonomic",
            "license": "cc by", "private": True}
    return int(_request("POST", "/dataset", token, json.dumps(body).encode(), {"Content-Type": "application/json"}))


def upload(token: str, key: int, z: Path) -> dict:
    with open(z, "rb") as f:
        resp = _request("POST", f"/importer/{key}", token, f,
                        {"Content-Type": "application/zip", "Content-Length": str(z.stat().st_size)})
    return json.loads(resp)


def main(args: list[str]) -> None:
    found = archives()
    if not found:
        raise SystemExit(f"no archives in {OUTPUT}; run the exporters first")
    skip = {x for a in args if a.startswith("--skip=") for x in a[7:].split(",")}
    names = [a for a in args if not a.startswith("--")]
    unknown = [n for n in names + sorted(skip) if n not in found]
    if unknown:
        raise SystemExit(f"no archive in output/ for: {', '.join(unknown)}")
    names = [n for n in (names or sorted(found, key=lambda n: found[n].stat().st_size)) if n not in skip]
    keys = load_keys()
    if "--dry-run" in args:
        for n in names:
            print(f"{n:70s} {found[n].stat().st_size / 1e6:8.1f} MB -> {keys.get(n, 'new dataset')}")
        return
    token = login()
    for n in names:
        z = found[n]
        if n not in keys:
            keys[n] = create_dataset(token, n, z)
            save_keys(keys)
            print(f"created private dataset {keys[n]} for {n}", flush=True)
        upload(token, keys[n], z)
        print(f"uploaded {n} ({z.stat().st_size / 1e6:.1f} MB) -> {API}/dataset/{keys[n]}", flush=True)
    print(f"{len(names)} archives uploaded; imports run in the background on ChecklistBank")


if __name__ == "__main__":
    main(sys.argv[1:])
