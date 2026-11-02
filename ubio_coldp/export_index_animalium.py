"""Sherborn's Index Animalium (uBio / Smithsonian Libraries digitisation) as ColDP.

    uv run python -m ubio_coldp.export_index_animalium [--keep]

ia.data holds the parsed entries: col1 = epithet, col2 = genus as used (with "(Auth.)" for the author of the original
combination, brackets when the genus was not stated explicitly), subgenus, col3 = author, col4 = microcitation,
publ = abbreviated title, leftovers (e.g. "[n. n.]"), afterDash = original genus. ia.links points a recombination to
its original entry; ia.genus_links gives the original genus and year when that entry is not indexed.
"""
from __future__ import annotations

import sys

from .coldp import Archive, connect, load_metadata_template
from .disk import check_budget


def main(args: list[str]) -> None:
    keep = "--keep" in args
    check_budget("ia start")
    con = connect(read_only=False)
    arc = Archive("index-animalium")

    con.execute(r"""
        CREATE OR REPLACE TEMP TABLE ia_e AS
        SELECT d.uid,
               trim(regexp_replace(fix_text(d.col1), '[,.;]+$', '')) AS epithet,
               fix_text(d.col2) AS genus_raw,
               trim(regexp_replace(regexp_replace(fix_text(d.col2), '\([^)]*\)', '', 'g'), '[\[\]?]', '', 'g')) AS genus,
               nullif(trim(fix_text(d.subgenus)), '') AS subgenus,
               nullif(trim(regexp_replace(fix_text(d.col3), '[,;]+$', '')), '') AS author,
               nullif(trim(fix_text(d.col4)), '') AS citation,
               TRY_CAST(regexp_extract(d.col4, '.*\b(17[5-9][0-9]|18[0-5][0-9])\b', 1) AS INTEGER) AS year,
               regexp_extract(d.col4, ',\s*\[?(\d+)\]?\s*$', 1) AS page,
               nullif(trim(fix_text(d.leftovers)), '') AS leftovers,
               nullif(trim(regexp_replace(regexp_replace(fix_text(d.afterDash), '^[\[\s]+', ''), '[.,;\s]+$', '')), '') AS original_genus,
               trim(d.col2) = '' AND regexp_matches(trim(d.col1), '^[A-Z]') AS is_genus,
               d.publ, d."round", n.filename, n.pagenum, fix_text(n.rawData) AS raw
        FROM ia.data d LEFT JOIN ia.names n USING (uid)
        WHERE trim(d.col1) <> ''""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ia_bib AS
        SELECT clean, any_value(fix_text(orig)) AS orig FROM ia.bibliography
        WHERE trim(clean) <> '' GROUP BY 1 HAVING count(*) = 1""")

    arc.write(con, "NameUsage", r"""
        SELECT CAST(e.uid AS VARCHAR) AS ID, 'bare name' AS status,
               concat_ws(' ', nullif(e.genus, ''), '(' || e.subgenus || ')', e.epithet) AS scientificName,
               e.author AS authorship, CASE WHEN e.is_genus THEN 'genus' ELSE 'species' END AS rank,
               'zoological' AS code,
               CASE WHEN e.citation IS NOT NULL THEN CAST(e.uid AS VARCHAR) END AS nameReferenceID,
               e.year AS publishedInYear, nullif(e.page, '') AS publishedInPage,
               CASE WHEN e.leftovers ILIKE '%n. n.%' THEN 'nomen nudum' END AS nameStatus,
               concat_ws('; ',
                   CASE WHEN e.genus_raw LIKE '[%' THEN 'genus not stated explicitly in original' END,
                   'genus as cited: ' || CASE WHEN e.genus_raw LIKE '%(%' THEN e.genus_raw END,
                   CASE WHEN e.is_genus THEN 'group: ' || e.original_genus
                        ELSE 'original genus: ' || coalesce(g.genusLink, e.original_genus) END,
                   e.leftovers,
                   'Index Animalium ' || CASE WHEN lower(e.filename) LIKE 'sil34_01%' THEN 'sect. 1 (1758-1800)'
                                              ELSE 'sect. 2 (1801-1850)' END
                       || ', p. ' || e.pagenum || ' (scan ' || e.filename || ')',
                   CASE WHEN e."round" > 1 THEN 'verbatim: ' || e.raw END) AS remarks
        FROM ia_e e LEFT JOIN (SELECT uid, any_value(genusLink) AS genusLink FROM ia.genus_links GROUP BY 1) g USING (uid)
        ORDER BY e.uid""")
    arc.write(con, "Reference", """
        SELECT CAST(e.uid AS VARCHAR) AS ID,
               concat_ws(', ', e.author, e.citation) AS citation,
               e.author AS author, e.year AS issued, b.orig AS remarks
        FROM ia_e e LEFT JOIN ia_bib b ON b.clean = e.publ
        WHERE e.citation IS NOT NULL ORDER BY e.uid""")
    arc.write(con, "NameRelation", """
        SELECT DISTINCT CAST(l.uid AS VARCHAR) AS nameID, CAST(l.uidLink AS VARCHAR) AS relatedNameID,
               'basionym' AS type
        FROM ia.links l JOIN ia_e a ON a.uid = l.uid JOIN ia_e b ON b.uid = l.uidLink
        WHERE l.uid <> l.uidLink AND a.epithet = b.epithet
        ORDER BY 1, 2""")
    arc.metadata(load_metadata_template("index-animalium"))
    con.close()
    arc.finish(keep=keep)


if __name__ == "__main__":
    main(sys.argv[1:])
