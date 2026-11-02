"""Create ChecklistBank datasets for the generated archives (if missing) and upload them.

    CLB_AUTH_HEADER=/path/to/file uv run python -m ubio_coldp.clb_upload [archive-name ...] [--skip=name,name]

Without archive names all archives are uploaded, smallest first.

The header file contains a single line "Authorization: Bearer <jwt>". Dataset keys are kept in
config/checklistbank.yaml (archive name -> dataset key); archives already listed there are only re-uploaded.
New datasets are created private, origin external; import replaces their metadata with the archive's metadata.yaml.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

from .disk import OUTPUT, ROOT

API = os.environ.get("CLB_API", "https://api.checklistbank.org")
KEYS = ROOT / "config" / "checklistbank.yaml"
NOMENCLATURAL = {"nomenclator-zoologicus", "index-animalium"}


def _curl(*args: str) -> str:
    header = os.environ["CLB_AUTH_HEADER"]
    out = subprocess.run(["curl", "-sS", "--fail-with-body", "-H", f"@{header}", *args],
                         capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"curl failed ({out.returncode}): {out.stdout[:500]} {out.stderr[:500]}")
    return out.stdout


def archives() -> dict[str, Path]:
    return {z.stem: z for z in sorted(OUTPUT.rglob("*.zip"))}


def main(names: list[str]) -> None:
    keys: dict[str, int] = yaml.safe_load(KEYS.read_text()) if KEYS.exists() else {}
    keys = keys or {}
    found = archives()
    skip = {x for a in names if a.startswith("--skip=") for x in a[7:].split(",")}
    names = [n for n in names if not n.startswith("--")] or sorted(found, key=lambda n: found[n].stat().st_size)
    for name in [n for n in names if n not in skip]:
        z = found[name]
        md = yaml.safe_load(zipfile.ZipFile(z).read("metadata.yaml"))
        if name not in keys:
            body = {"title": md["title"], "alias": md.get("alias"), "origin": "external",
                    "type": "nomenclatural" if name in NOMENCLATURAL else "taxonomic",
                    "license": "cc by", "private": True}
            key = int(_curl("-H", "Content-Type: application/json", "-X", "POST", f"{API}/dataset",
                            "-d", json.dumps(body)))
            keys[name] = key
            KEYS.write_text("# ChecklistBank dataset keys of the generated archives\n"
                            + yaml.safe_dump(keys, sort_keys=True))
            print(f"created {key} for {name}", flush=True)
        key = keys[name]
        resp = json.loads(_curl("-H", "Content-Type: application/zip", "-X", "POST",
                                "--data-binary", f"@{z}", f"{API}/importer/{key}"))
        print(f"uploaded {name} -> {key} ({z.stat().st_size / 1e6:.1f} MB), import queued: {resp.get('datasetKey')}",
              flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
