"""Stream rows out of the gzipped MySQL dumps inside ubio-db.tar without extracting anything.

The tar itself is uncompressed, so members are opened with random access; each member is a
gzip-compressed mysqldump (MySQL 5.1, extended inserts, one INSERT statement per line).
"""
from __future__ import annotations

import gzip
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

TAR = Path(__file__).resolve().parent.parent / "ubio-db.tar"

_CREATE = re.compile(r"^CREATE TABLE `([^`]+)` \(")
_COLUMN = re.compile(r"^\s+`([^`]+)`\s+([a-zA-Z]+)")
_INSERT = re.compile(r"^INSERT INTO `([^`]+)` VALUES ")
# one value: a quoted string with MySQL backslash escapes, or a bare token (number / NULL)
_FIELD = r"('(?:[^'\\]|\\.)*'|[^,()']*)"
_ESC = re.compile(r"\\(.)", re.S)
_ESC_MAP = {"0": "", "n": "\n", "r": "\r", "t": "\t", "b": "\b", "Z": "\x1a", "%": "\\%", "_": "\\_"}


def _unescape(m: re.Match) -> str:
    c = m.group(1)
    return _ESC_MAP.get(c, c)


def _value(tok: str):
    if tok.startswith("'"):
        s = tok[1:-1]
        return _ESC.sub(_unescape, s) if "\\" in s else s
    if tok == "NULL" or tok == "":
        return None
    return tok


@dataclass
class Table:
    name: str
    columns: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    _row_re: re.Pattern | None = None

    @property
    def row_re(self) -> re.Pattern:
        if self._row_re is None:
            self._row_re = re.compile(r"\(" + ",".join([_FIELD] * len(self.columns)) + r"\)", re.S)
        return self._row_re


def members() -> list[str]:
    with tarfile.open(TAR, "r") as tf:
        return [m.name for m in tf.getmembers() if m.isfile()]


def member_name(db: str) -> str:
    return f"./{db}.sql.gz"


def stream_tables(db: str, wanted: set[str] | None) -> Iterator[tuple[Table, list[tuple]]]:
    """Yield (table, rows) for every INSERT line of the wanted tables (all tables if wanted is None).

    Rows are tuples of str|None in column order. Raises if an INSERT line cannot be fully tokenised.
    """
    with tarfile.open(TAR, "r") as tf:
        fobj = tf.extractfile(member_name(db))
        assert fobj is not None
        with gzip.GzipFile(fileobj=fobj) as gz:
            tables: dict[str, Table] = {}
            current: Table | None = None
            for raw in gz:
                if current is not None:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith(")"):
                        tables[current.name] = current
                        current = None
                        continue
                    m = _COLUMN.match(line)
                    if m:
                        current.columns.append(m.group(1))
                        current.types.append(m.group(2).lower())
                    continue
                if raw.startswith(b"CREATE TABLE"):
                    m = _CREATE.match(raw.decode("utf-8", "replace"))
                    current = Table(m.group(1))
                    continue
                if not raw.startswith(b"INSERT INTO"):
                    continue
                # cheap table-name check before decoding the (possibly huge) line
                tname = raw[13:raw.index(b"`", 13)].decode()
                if wanted is not None and tname not in wanted:
                    continue
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                m = _INSERT.match(line)
                t = tables[tname]
                body = line[m.end():]
                if body.endswith(";"):
                    body = body[:-1]
                rows = []
                pos = 0
                for rm in t.row_re.finditer(body):
                    if rm.start() != pos:
                        raise ValueError(f"{db}.{tname}: unparsed text at {pos}: {body[pos:pos+200]!r}")
                    rows.append(tuple(_value(v) for v in rm.groups()))
                    pos = rm.end() + 1  # skip the comma between rows
                if pos < len(body):
                    raise ValueError(f"{db}.{tname}: trailing unparsed text: {body[pos:pos+200]!r}")
                yield t, rows
