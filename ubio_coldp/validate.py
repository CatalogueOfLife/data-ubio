"""Integrity checks for the generated ColDP archives.

    uv run python -m ubio_coldp.validate                 # every zip under output/
    uv run python -m ubio_coldp.validate output/index-animalium.zip

Each zip is extracted into work/validate/, checked with DuckDB and removed again.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

import duckdb
import yaml

from .disk import OUTPUT, WORK


def _read(path: Path) -> str:
    return f"read_csv('{path}', delim='\t', quote='', escape='', header=true, all_varchar=true)"


def check_dir(d: Path) -> list[str]:
    errors: list[str] = []
    con = duckdb.connect()
    files = {p.stem: p for p in d.glob("*.tsv")}
    if not (d / "metadata.yaml").exists():
        errors.append("metadata.yaml missing")
    else:
        md = yaml.safe_load((d / "metadata.yaml").read_text())
        todo = [k for k, v in md.items() if v == "TODO"]
        if todo:
            print(f"    note: metadata TODO fields: {', '.join(todo)}")
    for stem, p in files.items():
        con.execute(f'CREATE VIEW "{stem}" AS SELECT * FROM {_read(p)}')
        n = con.execute(f'SELECT count(*) FROM "{stem}"').fetchone()[0]
        print(f"    {stem}: {n:,}")

    def q(label: str, sql: str) -> None:
        n = con.execute(sql).fetchone()[0]
        if n:
            errors.append(f"{label}: {n:,}")

    if "NameUsage" in files:
        u = "NameUsage"
        ucols = set(con.sql(f"SELECT * FROM {u} LIMIT 0").columns)
        if "col:parentID" not in ucols:
            con.execute(f'CREATE OR REPLACE VIEW {u}_p AS SELECT *, NULL::VARCHAR AS "col:parentID" FROM {u}')
            u = f"{u}_p"
        q("usage ID missing", f'SELECT count(*) FROM {u} WHERE "col:ID" IS NULL')
        q("usage ID duplicated", f'SELECT count(*) - count(DISTINCT "col:ID") FROM {u}')
        q("scientificName missing", f'SELECT count(*) FROM {u} WHERE "col:scientificName" IS NULL')
        q("parentID not found", f'''SELECT count(*) FROM {u} c ANTI JOIN {u} p ON p."col:ID" = c."col:parentID"
                                     WHERE c."col:parentID" IS NOT NULL''')
        q("synonym without accepted parent", f'''SELECT count(*) FROM {u} c LEFT JOIN {u} p ON p."col:ID" = c."col:parentID"
            WHERE c."col:status" NOT IN ('accepted', 'provisionally accepted', 'bare name')
              AND (p."col:status" IS NULL OR p."col:status" NOT IN ('accepted', 'provisionally accepted'))''')
        q("bare name with parent", f'''SELECT count(*) FROM {u} WHERE "col:status" = 'bare name' AND "col:parentID" IS NOT NULL''')
        # cycles: walk up from every accepted node
        edges = dict(con.execute(f'''SELECT "col:ID", "col:parentID" FROM {u}
                                     WHERE "col:status" IN ('accepted', 'provisionally accepted')''').fetchall())
        cyc, ok = 0, set()
        for node in edges:
            path, cur = set(), node
            while cur is not None and cur not in ok:
                if cur in path:
                    cyc += 1
                    break
                path.add(cur)
                cur = edges.get(cur)
            ok |= path
        if cyc:
            errors.append(f"cycles in classification: {cyc}")
        if "Reference" in files:
            if "col:nameReferenceID" in ucols:
                q("nameReferenceID not found", f'''SELECT count(*) FROM {u} ANTI JOIN Reference r ON r."col:ID" = "col:nameReferenceID"
                                                WHERE "col:nameReferenceID" IS NOT NULL''')
            if "col:referenceID" in ucols:
                q("referenceID not found", f'''SELECT count(*) FROM (SELECT unnest(string_split("col:referenceID", ',')) AS rid
                    FROM {u} WHERE "col:referenceID" IS NOT NULL) ANTI JOIN Reference r ON r."col:ID" = rid''')
        if "Reference" in files:
            q("reference ID duplicated", 'SELECT count(*) - count(DISTINCT "col:ID") FROM Reference')
        if "NameRelation" in files:
            for col in ("col:nameID", "col:relatedNameID"):
                q(f"NameRelation {col} not found", f'''SELECT count(*) FROM NameRelation ANTI JOIN {u} ON {u}."col:ID" = NameRelation."{col}"''')
        for ent, col in (("VernacularName", "col:taxonID"), ("Distribution", "col:taxonID")):
            if ent in files:
                q(f"{ent} taxon not an accepted usage", f'''SELECT count(*) FROM {ent} ANTI JOIN {u}
                    ON {u}."col:ID" = {ent}."{col}" AND {u}."col:status" IN ('accepted', 'provisionally accepted')''')
        if "TypeMaterial" in files:
            q("TypeMaterial nameID not found", f'SELECT count(*) FROM TypeMaterial ANTI JOIN {u} ON {u}."col:ID" = TypeMaterial."col:nameID"')
    con.close()
    return errors


def main(args: list[str]) -> None:
    zips = [Path(a) for a in args] or sorted(OUTPUT.rglob("*.zip"))
    tmp = WORK / "validate"
    failed = 0
    for z in zips:
        print(f"== {z.relative_to(OUTPUT) if z.is_relative_to(OUTPUT) else z}")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        errors = check_dir(tmp)
        for e in errors:
            print(f"    ERROR {e}")
        failed += bool(errors)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(zips)} archives checked, {failed} with errors")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
