"""Nomenclator Zoologicus (uBio digital edition) as ColDP.

IDs follow ChecklistBank dataset 126539 (NZ uid), so both versions can be compared or merged.

    uv run python -m ubio_coldp.export_nz [--keep]
"""
from __future__ import annotations

import re
import sys

import pyarrow as pa

from .coldp import Archive, connect, load_metadata_template
from .disk import check_budget

# anonymous web comments were heavily spammed; keep registered editors and plausible anonymous notes only
SPAM = (r"headphone|replica|viagra|cialis|louboutin|cheap|handbag|\bugg|jordan|nike|casino|loan|outlet|\bdre\b|"
        r"wholesale|watches|\bsale\b|discount|shoes|boots|jersey|\bbags?\b|dress|wedding|payday|porn|\bsex|moncler|"
        r"\bcoats?\b|\bhair\b|xmas|trendy|fashion|\bhehe\b")
EMAIL = r"[\w.+-]+@[\w-]+\.[\w.-]+"

# "(n.n. pro Aphobus Cabanis 1851)", "n.n. pro Leiurus Swainson 1839", "nom. nov. pro ..."
NN_PRO = re.compile(r"\b(?:n\.\s?n\.|nom\.\s?nov\.)\s+pro\s+([A-Z][a-zë-]+)\s+([^();]*?)\s*(1[5-9]\d\d|20[0-2]\d)", re.U)


def replacement_relations(con) -> pa.Table:
    entries = con.execute("SELECT uid, name, authority, year FROM nz.entries").fetchall()
    by_name: dict[str, list[tuple]] = {}
    for uid, name, auth, year in entries:
        by_name.setdefault(name, []).append((uid, auth or "", year))
    src, dst, remark = [], [], []
    for uid, comments in con.execute("SELECT uid, comments FROM nz.entries WHERE comments ILIKE '%pro %'").fetchall():
        for m in NN_PRO.finditer(comments or ""):
            target, author, year = m.group(1), m.group(2).strip(" ,"), int(m.group(3))
            author_key = author.split()[0].lower() if author else ""
            cands = [c for c in by_name.get(target, []) if c[2] == year and c[0] != uid]
            if len(cands) > 1 and author_key:
                cands = [c for c in cands if author_key in c[1].lower()] or cands
            if len(cands) == 1:
                src.append(str(uid))
                dst.append(str(cands[0][0]))
                remark.append(m.group(0))
    print(f"  replacement names resolved: {len(src):,}", flush=True)
    return pa.table({"nameID": src, "relatedNameID": dst, "remarks": remark})


def main(args: list[str]) -> None:
    keep = "--keep" in args
    check_budget("nz start")
    con = connect(read_only=False)
    arc = Archive("nomenclator-zoologicus")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE nz_ed AS
        SELECT TRY_CAST(c.recordID AS BIGINT) AS uid,
               string_agg(regexp_replace(fix_text(c.comment), '{EMAIL}', '[email removed]', 'g') || coalesce(' [' || nullif(trim(concat_ws(' ', e.first, e.last)), '') || ', '
                          || left(c.datestamp, 10) || ']', ''), ' | ' ORDER BY c.datestamp) AS ed
        FROM nz.edComments c LEFT JOIN nz.editors e USING (editorID)
        WHERE length(trim(c.comment)) >= 10
          AND NOT regexp_matches(c.comment, '(?i)http|www\\.|<a ')
          AND (c.editorID > 0 OR NOT regexp_matches(lower(c.comment), '{SPAM}'))
        GROUP BY 1""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nz_see AS
        SELECT x.uid1 AS uid, string_agg(DISTINCT e.name || ' ' || e.authority, '; ') AS see
        FROM nz.xref x JOIN nz.entries e ON e.uid = x.uid2 GROUP BY 1""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nz_alt AS
        SELECT uid, string_agg(DISTINCT 'ubio:' || namebankID, ',') AS ids
        FROM nz.mapping WHERE namebankID > 0 GROUP BY 1""")

    arc.write(con, "NameUsage", """
        SELECT CAST(e.uid AS VARCHAR) AS ID, a.ids AS nameAlternativeID, 'bare name' AS status,
               e.name AS scientificName, e.authority AS authorship, 'zoological' AS code,
               CASE WHEN trim(e.publication) <> '' THEN CAST(e.uid AS VARCHAR) END AS nameReferenceID,
               CASE WHEN e.year > 0 THEN e.year END AS publishedInYear,
               CASE WHEN e.extinctFlag = 1 THEN 'true' END AS extinct,
               concat_ws('; ',
                   nullif(trim(e.comments), ''),
                   'group: ' || nullif(trim(e.category), ''),
                   CASE WHEN e.homonymFlag = 1 THEN 'homonym' END,
                   CASE WHEN e.corrigendaFlag = 1 THEN 'corrigenda' END,
                   CASE WHEN e.addendumFlag = 1 THEN 'addendum' END,
                   'see also: ' || s.see,
                   'NZ vol. ' || e.volume || ', p. ' || e.page,
                   'editorial comments: ' || d.ed) AS remarks
        FROM nz.entries e
        LEFT JOIN nz_alt a USING (uid) LEFT JOIN nz_see s USING (uid) LEFT JOIN nz_ed d USING (uid)
        UNION ALL
        SELECT 'a' || additionID, NULL, 'bare name', nameString, author, 'zoological',
               CASE WHEN trim(publication) <> '' THEN 'a' || additionID END,
               TRY_CAST(regexp_extract(author, '(1[5-9][0-9]{2}|20[0-2][0-9])', 1) AS INTEGER),
               CASE WHEN extinct = 1 THEN 'true' END,
               concat_ws('; ', 'group: ' || nullif(trim(category), ''),
                         'online addition to uBio NZ, entered ' || date_entered)
        FROM nz.additions WHERE trim(nameString) <> ''
        ORDER BY 1""")
    arc.write(con, "Reference", """
        SELECT CAST(uid AS VARCHAR) AS ID, fix_text(publication) AS citation,
               CASE WHEN year > 0 THEN year END AS issued
        FROM nz.entries WHERE trim(publication) <> ''
        UNION ALL
        SELECT 'a' || additionID, fix_text(publication),
               TRY_CAST(regexp_extract(author, '(1[5-9][0-9]{2}|20[0-2][0-9])', 1) AS INTEGER)
        FROM nz.additions WHERE trim(publication) <> '' AND trim(nameString) <> ''
        ORDER BY 1""")

    con.register("_nn", replacement_relations(con))
    arc.write(con, "NameRelation", """
        SELECT nameID, relatedNameID, 'replacement name' AS type, remarks FROM _nn ORDER BY 1, 2""")

    arc.metadata(load_metadata_template("nomenclator-zoologicus"))
    con.close()
    arc.finish(keep=keep)


if __name__ == "__main__":
    main(sys.argv[1:])
