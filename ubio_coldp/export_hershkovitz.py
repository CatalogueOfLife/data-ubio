"""Hershkovitz (1966) Catalog of Living Whales: ClassificationBank 57 enriched with the uBio Hershkovitz database.

    uv run python -m ubio_coldp.export_hershkovitz [--keep]
"""
from __future__ import annotations

import sys

from .coldp import load_metadata_template
from .disk import check_budget
from .export_classification import export_classification

CID = 57


def before(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE extra_remarks AS
        SELECT ubioID AS ID,
               concat_ws('; ', 'type species: ' || nullif(trim(fix_text(type_species)), ''),
                         nullif(trim(fix_text(remarks)), ''),
                         'Hershkovitz 1966, p. ' || nullif(page, 0)) AS remarks
        FROM hk.validNames WHERE ubioID > 0""")


def after(con, arc) -> None:
    arc.write(con, "TypeMaterial", """
        SELECT CAST(v.ubioID AS VARCHAR) AS nameID, fix_text(v.type) AS citation,
               fix_text(v.type_locality) AS locality, NULL AS status
        FROM hk.validNames v JOIN tree t ON t.ID = v.ubioID
        WHERE trim(v.type) <> '' OR trim(v.type_locality) <> ''
        UNION ALL
        SELECT CAST(v.ubioID AS VARCHAR), fix_text(v.lectotype), NULL, 'lectotype'
        FROM hk.validNames v JOIN tree t ON t.ID = v.ubioID WHERE trim(v.lectotype) <> ''
        ORDER BY 1""")
    arc.write(con, "Distribution", """
        SELECT CAST(v.ubioID AS VARCHAR) AS taxonID, fix_text(v.distribution) AS area, 'text' AS gazetteer
        FROM hk.validNames v JOIN tree t ON t.ID = v.ubioID
        WHERE trim(v.distribution) <> '' ORDER BY 1""")


def main(args: list[str]) -> None:
    check_budget("hershkovitz start")
    export_classification(CID, keep="--keep" in args, archive_name="hershkovitz-whales", subdir=None,
                          before=before, after=after, metadata=load_metadata_template("hershkovitz"))


if __name__ == "__main__":
    main(sys.argv[1:])
