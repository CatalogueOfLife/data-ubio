"""Write the subset of the uBio dump that the exporters need to source/<schema>/<table>.parquet (zstd).

    uv run python -m ubio_coldp.archive_source

The result is small enough for a git repository. `stage --source` rebuilds work/ubio.duckdb from it, so the 5.4 GB
ubio-db.tar is no longer needed. Rows and columns the exporters never read are left out (see FILTERS).
"""
from __future__ import annotations

import shutil

import duckdb

from .coldp import DB_PATH
from .disk import ROOT
from .export_classification import configured_cids
from .export_main import LEXICAL, SCOPES, SCIENTIFIC_LANGS

SOURCE = ROOT / "source"
GITHUB_FILE_LIMIT = 50 * 1000 * 1000  # GitHub warns above 50 MB and rejects above 100 MB


def filters() -> dict[str, str]:
    cids = ", ".join(str(c) for c in sorted(set(configured_cids()) | {57, 104}))
    sci = ", ".join(map(str, SCIENTIFIC_LANGS))
    return {
        # NameBank
        "nb2.names": "SELECT namebankID, nameString, canonicalFormID, visibleFlag, clientClassID, supercedureID FROM nb2.names",
        "nb2.canonicalForms": "SELECT canonicalFormID, canonicalForm FROM nb2.canonicalForms",
        "nb2.nameRanks": "SELECT * FROM nb2.nameRanks",
        "nb2.ranks": "SELECT * FROM nb2.ranks",
        # only names that have a vernacular language row are needed to tell scientific from vernacular names
        "nb2.nameLanguages": f"""SELECT * FROM nb2.nameLanguages WHERE namebankID IN
                                 (SELECT namebankID FROM nb2.nameLanguages WHERE languageID NOT IN ({sci}))""",
        "nb2.languages": "SELECT * FROM nb2.languages",
        "nb2.lexicalGroups": f"""SELECT * FROM nb2.lexicalGroups
                                 WHERE lexicalQualifierID IN ({", ".join(map(str, LEXICAL))}) AND namebankID <> lexicalGroupID""",
        "nb2.lexicalQualifiers": "SELECT * FROM nb2.lexicalQualifiers",
        "nb2.basionymGroups": "SELECT * FROM nb2.basionymGroups WHERE namebankID <> basionymGroupID",
        "nb2.citations": "SELECT citationsID, authority, year, source, title, pubMedID, citation FROM nb2.citations",
        "nb2.chresonym": "SELECT namebankID, citationsID FROM nb2.chresonym",
        "nb2.mappings": f"""SELECT namebankID, collectionsID, foreignKey FROM nb2.mappings
                            WHERE collectionsID IN ({", ".join(map(str, SCOPES))})""",
        "nb2.collections": "SELECT collectionsID, collectionsTitle, abbreviation, description, collectionLink FROM nb2.collections",
        "nb2.commentLayer": "SELECT namebankID, collectionsID, typeID, valueID FROM nb2.commentLayer",
        "nb2.commentValues": "SELECT * FROM nb2.commentValues",
        # old NameBank: only the extinct flag is used
        "nb1.names": "SELECT namebankID, extinctFlag FROM nb1.names WHERE extinctFlag = 1",
        # ClassificationBank: only the exported classifications
        "cb.Classifications": "SELECT * FROM cb.Classifications",
        "cb.ClassificationStructure": "SELECT * FROM cb.ClassificationStructure",
        "cb.ClassificationGroups": "SELECT * FROM cb.ClassificationGroups",
        "cb.SynonymRelations": "SELECT * FROM cb.SynonymRelations",
        "cb.Hierarchies": f"""SELECT hierarchiesID, childID, parentID, _classificationsID, rankID, ancestry
                              FROM cb.Hierarchies WHERE _classificationsID IN ({cids})""",
        "cb.Synonyms": f"SELECT * FROM cb.Synonyms WHERE _classificationsID IN ({cids})",
        "cb.Chresonym": f"""SELECT * FROM cb.Chresonym WHERE synonymsID IN
                            (SELECT synonymsID FROM cb.Synonyms WHERE _classificationsID IN ({cids}))""",
        # uBiota vernacular names
        "ubt.name_links": "SELECT nb_id, common_name, lang FROM ubt.name_links",
        "ubt.common_search": "SELECT nb_id, common_name, lang FROM ubt.common_search",
        # Nomenclator Zoologicus: the spam comments with links are dropped here already
        "nz.entries": "SELECT * FROM nz.entries",
        "nz.additions": "SELECT * FROM nz.additions",
        "nz.xref": "SELECT * FROM nz.xref",
        "nz.mapping": "SELECT * FROM nz.mapping",
        "nz.edComments": r"""SELECT * REPLACE (regexp_replace(comment, '[\w.+-]+@[\w-]+\.[\w.-]+', '[email removed]', 'g') AS comment)
                             FROM nz.edComments WHERE NOT regexp_matches(comment, '(?i)http|www\.|<a ')""",
        "nz.editors": "SELECT * FROM nz.editors",
        # Index Animalium
        "ia.data": "SELECT * FROM ia.data",
        "ia.names": "SELECT * FROM ia.names",
        "ia.links": "SELECT * FROM ia.links",
        "ia.genus_links": "SELECT * FROM ia.genus_links",
        "ia.bibliography": "SELECT * FROM ia.bibliography",
        # Hershkovitz
        "hk.validNames": "SELECT * FROM hk.validNames",
    }


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    shutil.rmtree(SOURCE, ignore_errors=True)
    total = 0
    too_big = []
    for name, sql in filters().items():
        schema, table = name.split(".")
        out = SOURCE / schema / table  # a folder of one or more parquet files
        out.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY ({sql}) TO '{out}' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 19, "
                    "ROW_GROUP_SIZE 500000, FILE_SIZE_BYTES '40MB', FILENAME_PATTERN 'part{i}')")
        files = sorted(out.glob("*.parquet"))
        size = sum(f.stat().st_size for f in files)
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{out}/*.parquet')").fetchone()[0]
        total += size
        print(f"  {name:28s} {rows:>12,} rows {size / 1e6:8.1f} MB in {len(files)} file(s)", flush=True)
        too_big += [str(f) for f in files if f.stat().st_size > GITHUB_FILE_LIMIT]
    print(f"total {total / 1e6:.1f} MB in {SOURCE}")
    if too_big:
        raise SystemExit(f"files above 50 MB: {too_big}")


if __name__ == "__main__":
    main()
