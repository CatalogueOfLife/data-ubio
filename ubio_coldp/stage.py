"""Stage selected tables from ubio-db.tar into work/ubio.duckdb.

Each source dump is parsed in its own process into zstd parquet files (all VARCHAR), which are
then loaded into typed DuckDB tables (one schema per dump) and deleted.

    uv run python -m ubio_coldp.stage            # all dumps
    uv run python -m ubio_coldp.stage NZ Hershkovitz
    uv run python -m ubio_coldp.stage --source   # rebuild from the parquet subset in source/ (no tar needed)
"""
from __future__ import annotations

import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .coldp import DB_PATH
from .disk import ROOT, WORK, check_budget
from .dump import stream_tables

SOURCE = ROOT / "source"
PARQUET = WORK / "parquet"

# dump -> (duckdb schema, {table: columns to keep or None for all})
STAGE: dict[str, tuple[str, dict[str, list[str] | None]]] = {
    "namebank2": ("nb2", {t: None for t in [
        "names", "canonicalForms", "nameRanks", "ranks", "nameLanguages", "languages",
        "namePackages", "packages", "lexicalGroups", "lexicalQualifiers", "basionymGroups",
        "citations", "chresonym", "mappings", "collections", "weblinks", "webservices",
        "commentLayer", "commentTypes", "commentValues", "clientClasses", "datasets",
    ]}),
    "namebank": ("nb1", {
        "names": ["namebankID", "languageCode", "parentID", "homonymFlag", "inferredRank",
                  "extinctFlag", "lexicalQualifier"],
        "ranks": None, "collections": None, "languages": None, "sourcePackages": None,
        "packages": None,
    }),
    "classificationbank": ("cb", {t: None for t in [
        "Hierarchies", "Synonyms", "SynonymRelations", "Classifications", "ClassificationGroups",
        "ClassificationStructure", "Chresonyms", "Chresonym", "Justifications", "Interpretations",
    ]}),
    "ubt_nb": ("ubt", {"name_links": None, "common_search": None, "UBT_XREF": None}),
    "NZ": ("nz", {
        "entries": None, "additions": ["additionID", "name", "nameString", "author", "publication",
                                       "category", "extinct", "date_entered"],
        "xref": None, "mapping": None, "edComments": None,
        "editors": ["editorID", "salutation", "first", "last"],
    }),
    "Index_Animalium": ("ia", {t: None for t in [
        "bibliography", "brackets", "data", "genus_links", "links", "names", "variants"]}),
    "Hershkovitz": ("hk", {"validNames": None, "bibliography": None, "pages": None}),
}

_INT = {"int", "tinyint", "smallint", "mediumint", "bigint"}
_FLOAT = {"float", "double", "decimal"}
BATCH = 200_000


def _parse_dump(db: str) -> dict[str, tuple[int, list[str], list[str]]]:
    """Parse one dump into parquet files; returns {table: (rows, columns, types)}."""
    schema, tables = STAGE[db]
    outdir = PARQUET / schema
    outdir.mkdir(parents=True, exist_ok=True)
    writers: dict[str, pq.ParquetWriter] = {}
    buffers: dict[str, list[tuple]] = {}
    meta: dict[str, tuple[int, list[str], list[str]]] = {}
    keep_idx: dict[str, list[int]] = {}

    def flush(tname: str):
        rows = buffers[tname]
        if not rows:
            return
        cols = meta[tname][1]
        arrays = [pa.array([r[i] for r in rows], type=pa.string()) for i in range(len(cols))]
        tbl = pa.Table.from_arrays(arrays, names=cols)
        if tname not in writers:
            writers[tname] = pq.ParquetWriter(outdir / f"{tname}.parquet", tbl.schema, compression="zstd")
        writers[tname].write_table(tbl)
        buffers[tname] = []

    t0 = time.time()
    for t, rows in stream_tables(db, set(tables)):
        if t.name not in meta:
            want = tables[t.name]
            idx = list(range(len(t.columns))) if want is None else [t.columns.index(c) for c in want]
            keep_idx[t.name] = idx
            meta[t.name] = (0, [t.columns[i] for i in idx], [t.types[i] for i in idx])
            buffers[t.name] = []
        idx = keep_idx[t.name]
        if len(idx) != len(t.columns):
            rows = [tuple(r[i] for i in idx) for r in rows]
        buffers[t.name].extend(rows)
        n, c, ty = meta[t.name]
        meta[t.name] = (n + len(rows), c, ty)
        if len(buffers[t.name]) >= BATCH:
            flush(t.name)
    for tname in list(buffers):
        flush(tname)
    for w in writers.values():
        w.close()
    print(f"[{db}] parsed {len(meta)} tables in {time.time() - t0:.0f}s", flush=True)
    return meta


def _load(con: duckdb.DuckDBPyConnection, schema: str, meta: dict) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for tname, (n, cols, types) in meta.items():
        path = PARQUET / schema / f"{tname}.parquet"
        exprs = []
        for c, ty in zip(cols, types):
            if ty in _INT:
                exprs.append(f'TRY_CAST("{c}" AS BIGINT) AS "{c}"')
            elif ty in _FLOAT:
                exprs.append(f'TRY_CAST("{c}" AS DOUBLE) AS "{c}"')
            else:
                exprs.append(f'"{c}"')
        con.execute(f'DROP TABLE IF EXISTS {schema}."{tname}"')
        if path.exists():
            con.execute(f'CREATE TABLE {schema}."{tname}" AS SELECT {", ".join(exprs)} FROM read_parquet(?)',
                        [str(path)])
        else:  # table without data
            con.execute(f'CREATE TABLE {schema}."{tname}" ({", ".join(chr(34) + c + chr(34) + " VARCHAR" for c in cols)})')
        got = con.execute(f'SELECT count(*) FROM {schema}."{tname}"').fetchone()[0]
        print(f"  {schema}.{tname}: {got:,} rows", flush=True)
        assert got == n, f"row count mismatch for {schema}.{tname}: parsed {n}, loaded {got}"
        path.unlink(missing_ok=True)


def load_source() -> None:
    """Rebuild the staging database from source/<schema>/<table>/*.parquet."""
    WORK.mkdir(exist_ok=True)
    check_budget("stage --source start")
    con = duckdb.connect(str(DB_PATH))
    for tdir in sorted(SOURCE.glob("*/*")):
        schema, table = tdir.parent.name, tdir.name
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.execute(f'CREATE OR REPLACE TABLE {schema}."{table}" AS SELECT * FROM read_parquet(?)',
                    [str(tdir / "*.parquet")])
        n = con.execute(f'SELECT count(*) FROM {schema}."{table}"').fetchone()[0]
        print(f"  {schema}.{table}: {n:,} rows", flush=True)
    con.execute("CHECKPOINT")
    con.close()
    check_budget("stage --source done")


def main(dbs: list[str]) -> None:
    WORK.mkdir(exist_ok=True)
    check_budget("stage start")
    unknown = [d for d in dbs if d not in STAGE]
    if unknown:
        raise SystemExit(f"unknown dumps: {unknown}; choose from {list(STAGE)}")
    results: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=min(len(dbs), 6)) as ex:
        futs = {ex.submit(_parse_dump, db): db for db in dbs}
        for f in as_completed(futs):
            results[futs[f]] = f.result()
            check_budget(f"parsed {futs[f]}")
    con = duckdb.connect(str(DB_PATH))
    for db in dbs:
        print(f"[{db}] loading into duckdb", flush=True)
        _load(con, STAGE[db][0], results[db])
    con.execute("CHECKPOINT")
    con.close()
    shutil.rmtree(PARQUET, ignore_errors=True)
    check_budget("stage done")


if __name__ == "__main__":
    if "--source" in sys.argv[1:]:
        load_source()
    else:
        main(sys.argv[1:] or list(STAGE))
