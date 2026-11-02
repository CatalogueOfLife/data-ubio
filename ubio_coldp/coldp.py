"""Shared helpers for writing ColDP archives from the DuckDB staging database."""
from __future__ import annotations

import html
import os
import re
import shutil
import zipfile
from pathlib import Path

import duckdb
import ftfy
import yaml

from .disk import OUTPUT, WORK, check_budget

DB_PATH = Path(os.environ.get("UBIO_DB", WORK / "ubio.duckdb"))
SCHEMA_LINE = "# yaml-language-server: $schema=https://raw.githubusercontent.com/CatalogueOfLife/coldp/master/metadata.json\n"

_TAGS = re.compile(r"<[^>]+>")
_ANCHOR = re.compile(r"""<a\s[^>]*href\s*=\s*["']?([^"'\s>]+)["']?[^>]*>(.*?)</a>""", re.I | re.S)


def _anchor(m: re.Match) -> str:
    href, text = m.group(1), _TAGS.sub("", m.group(2)).strip()
    return f"{text} ({href})" if text and text != href else href
_WS = re.compile(r"\s+")


def fix_text(s: str | None) -> str | None:
    """Repair mojibake, unescape HTML entities, drop tags and collapse whitespace."""
    if s is None:
        return None
    s = ftfy.fix_text(html.unescape(s))
    s = _ANCHOR.sub(_anchor, s)
    s = _TAGS.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s or None


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    con.execute("SET preserve_insertion_order = false")
    # t(x): text cleaned for TSV output (no tabs / newlines, empty -> NULL)
    con.execute(r"""CREATE OR REPLACE TEMP MACRO t(x) AS
        nullif(trim(regexp_replace(CAST(x AS VARCHAR), '[\t\r\n]+', ' ', 'g')), '')""")
    con.create_function("fix_text", fix_text, ["VARCHAR"], "VARCHAR", null_handling="special")
    return con


class Archive:
    """A ColDP archive being written to output/<name>/ and zipped to output/<name>.zip."""

    def __init__(self, name: str, subdir: str | None = None):
        base = OUTPUT / subdir if subdir else OUTPUT
        self.dir = base / name
        self.zip_path = base / f"{name}.zip"
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)
        self.counts: dict[str, int] = {}

    def write(self, con: duckdb.DuckDBPyConnection, entity: str, sql: str) -> int:
        """Write the result of sql as <entity>.tsv. Column aliases are ColDP terms without prefix."""
        path = self.dir / f"{entity}.tsv"
        rel = con.sql(sql)
        cols = ", ".join(f't("{c}") AS "col:{c}"' for c in rel.columns)
        con.execute(f"COPY (SELECT {cols} FROM ({sql})) TO '{path}' "
                    "(HEADER, DELIMITER '\t', QUOTE '', ESCAPE '')")
        n = con.execute(f"SELECT count(*) FROM read_csv('{path}', delim='\t', quote='', escape='', header=true, all_varchar=true)").fetchone()[0]
        self.counts[entity] = n
        if n == 0:
            path.unlink()
            print(f"  {self.dir.name}/{entity}.tsv: empty, skipped", flush=True)
            return 0
        print(f"  {self.dir.name}/{entity}.tsv: {n:,} rows", flush=True)
        check_budget(f"{self.dir.name}/{entity}")
        return n

    def metadata(self, meta: dict) -> None:
        with open(self.dir / "metadata.yaml", "w", encoding="utf-8") as f:
            f.write(SCHEMA_LINE)
            yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False, width=120)

    def finish(self, keep: bool = False) -> Path:
        if self.zip_path.exists():
            self.zip_path.unlink()
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in sorted(self.dir.iterdir()):
                zf.write(p, p.name)
        check_budget(f"zipped {self.zip_path.name}")
        if not keep:
            shutil.rmtree(self.dir)
        print(f"  -> {self.zip_path} ({self.zip_path.stat().st_size / 1e6:.1f} MB)", flush=True)
        return self.zip_path


def load_metadata_template(name: str) -> dict:
    path = Path(__file__).resolve().parent.parent / "metadata" / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
