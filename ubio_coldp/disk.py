"""Disk budget guard: the whole pipeline (work/ + output/) must stay below the budget."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
BUDGET_GB = float(os.environ.get("UBIO_DISK_BUDGET_GB", "45"))


def used_bytes() -> int:
    total = 0
    for base in (WORK, OUTPUT):
        for dirpath, _, files in os.walk(base):
            for f in files:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
                except FileNotFoundError:
                    pass
    return total


def check_budget(step: str = "") -> float:
    gb = used_bytes() / 1e9
    print(f"[disk] {step}: work+output = {gb:.2f} GB (budget {BUDGET_GB:.0f} GB)", flush=True)
    if gb > BUDGET_GB:
        raise SystemExit(f"Disk budget exceeded ({gb:.1f} GB > {BUDGET_GB} GB) at step {step}")
    return gb
