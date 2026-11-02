"""Main uBio archive: uBiota (ClassificationBank 104) as the accepted tree plus all other NameBank names as bare names.

    uv run python -m ubio_coldp.export_main [--keep]
"""
from __future__ import annotations

import sys

from .coldp import Archive, connect, load_metadata_template
from .disk import check_budget
from .export_classification import VERNACULAR_LANG, prepare_names, register_tree, write_references

UBIOTA = 104
SCIENTIFIC_LANGS = (501, 437)  # "Scientific Name", "Operational Taxonomic Unit"

# NameBank collections whose foreign keys are stable identifiers with a registered ColDP identifier scope
SCOPES = {
    1: "tsn", 5: "tsn", 3: "algaebase", 9: "fishbase", 10: "if", 11: "if", 109: "if", 15: "ncbi", 27: "grin",
    32: "iucn", 36: "worms", 39: "usda", 93: "tropicos", 108: "fauna-europaea", 99: "biolib",
}
# lexicalQualifierID -> NameRelation type; the other qualifiers only mark authorship/formatting variants
LEXICAL = {9: "spelling correction", 3: "spelling correction", 8: "homotypic", 7: "homotypic"}


def main(args: list[str]) -> None:
    keep = "--keep" in args
    check_budget("main start")
    con = connect(read_only=False)
    prepare_names(con)
    register_tree(con, UBIOTA)
    arc = Archive("ubio-namebank")

    langs = ", ".join(map(str, SCIENTIFIC_LANGS))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE usage_ids AS
        SELECT ID AS namebankID FROM tree
        UNION
        SELECT n.namebankID FROM nm_name n
        WHERE n.namebankID NOT IN (
            SELECT namebankID FROM nb2.nameLanguages GROUP BY 1
            HAVING bool_and(languageID NOT IN ({langs})))""")
    scopes = " ".join(f"WHEN {k} THEN '{v}'" for k, v in SCOPES.items())
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE alt_ids AS
        SELECT cur_id(m.namebankID) AS namebankID,
               string_agg(DISTINCT (CASE m.collectionsID {scopes} END) || ':'
                          || CASE WHEN m.collectionsID = 93 THEN ltrim(trim(m.foreignKey), '0') ELSE trim(m.foreignKey) END,
                          ',') AS ids
        FROM nb2.mappings m
        WHERE m.collectionsID IN ({", ".join(map(str, SCOPES))}) AND trim(m.foreignKey) NOT IN ('', '0')
        GROUP BY 1""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE comment_remarks AS
        SELECT cur_id(l.namebankID) AS namebankID,
               string_agg(DISTINCT coalesce(nullif(c.abbreviation, ''), c.collectionsTitle) || ' '
                          || CASE l.typeID WHEN 1 THEN 'taxonomic' WHEN 2 THEN 'nomenclatural' ELSE 'status' END
                          || ': ' || v.value, '; ') AS remarks
        FROM nb2.commentLayer l LEFT JOIN nb2.collections c USING (collectionsID)
        LEFT JOIN nb2.commentValues v USING (valueID)
        WHERE v.value IS NOT NULL GROUP BY 1""")
    print("  helper tables ready", flush=True)

    arc.write(con, "NameUsage", """
        SELECT CAST(u.namebankID AS VARCHAR) AS ID,
               concat_ws(',', 'ubio:' || u.namebankID, a.ids) AS nameAlternativeID,
               CAST(t.parentID AS VARCHAR) AS parentID,
               CASE WHEN t.ID IS NOT NULL THEN 'accepted' ELSE 'bare name' END AS status,
               sci_name(coalesce(map_rank(r.rankName), n.rank), n.canonical, n.nameString) AS scientificName,
               auth_name(coalesce(map_rank(r.rankName), n.rank), n.canonical, n.nameString) AS authorship,
               final_rank(coalesce(map_rank(r.rankName), n.rank), n.canonical, n.nameString, false) AS rank,
               CASE WHEN c.bestID IS NOT NULL THEN 'c' || c.bestID END AS nameReferenceID,
               CASE WHEN t.ID IS NOT NULL THEN c.otherRefs END AS referenceID,
               n.extinct,
               concat_ws('; ', CASE WHEN t.implied THEN 'parent implied by uBiota ancestry path' END,
                         cr.remarks) AS remarks
        FROM usage_ids u
        JOIN nm_name n USING (namebankID)
        LEFT JOIN tree t ON t.ID = u.namebankID
        LEFT JOIN nb2.ranks r ON r.rankID = t.rankID
        LEFT JOIN nm_cite c ON c.namebankID = u.namebankID
        LEFT JOIN alt_ids a ON a.namebankID = u.namebankID
        LEFT JOIN comment_remarks cr ON cr.namebankID = u.namebankID
        ORDER BY u.namebankID""")

    write_references(con, arc, """
        SELECT DISTINCT unnest(allIDs) FROM nm_cite WHERE namebankID IN (SELECT namebankID FROM usage_ids)""")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE vern AS
        WITH v AS (
            SELECT cur_id(nl.parentNamebankID) AS taxonID, n.nameString AS name, nl.languageID AS lang, 'NameBank' AS src
            FROM nb2.nameLanguages nl JOIN nb2.names n USING (namebankID)
            WHERE nl.languageID NOT IN ({langs}) AND nl.parentNamebankID <> 0
              AND n.supercedureID = 0 AND trim(n.nameString) <> ''
            UNION ALL
            SELECT cur_id(nb_id), common_name, lang, 'uBiota' FROM ubt.name_links WHERE trim(common_name) <> ''
            UNION ALL
            SELECT cur_id(nb_id), common_name, lang, 'uBiota' FROM ubt.common_search WHERE trim(common_name) <> ''
        )
        SELECT v.taxonID, trim(v.name) AS name, {VERNACULAR_LANG} AS language, v.taxonID IN (SELECT ID FROM tree) AS placed
        FROM v LEFT JOIN nb2.languages l ON l.languageID = v.lang""")
    total, placed = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE placed) FROM (
            SELECT DISTINCT taxonID, lower(name), language, placed FROM vern)""").fetchone()
    print(f"  vernacular names: {total:,} distinct, {placed:,} attached to uBiota taxa, "
          f"{total - placed:,} dropped (taxon not in uBiota)", flush=True)
    arc.write(con, "VernacularName", """
        SELECT DISTINCT ON (taxonID, lower(name), language)
               CAST(taxonID AS VARCHAR) AS taxonID, name, language
        FROM vern WHERE placed ORDER BY taxonID, lower(name), language, name""")

    lex_cases = " ".join(f"WHEN {k} THEN '{v}'" for k, v in LEXICAL.items())
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE rel AS
        WITH lex AS (
            SELECT coalesce(s1.cur, g.namebankID) AS variant, coalesce(s2.cur, g.lexicalGroupID) AS preferred,
                   CASE g.lexicalQualifierID {lex_cases} END AS type, q.lexicalQualifier AS qualifier
            FROM nb2.lexicalGroups g JOIN nb2.lexicalQualifiers q USING (lexicalQualifierID)
            LEFT JOIN nm_sup s1 ON s1.old = g.namebankID LEFT JOIN nm_sup s2 ON s2.old = g.lexicalGroupID
            WHERE g.lexicalQualifierID IN ({", ".join(map(str, LEXICAL))}) AND g.namebankID <> g.lexicalGroupID),
        bas AS (
            SELECT coalesce(s1.cur, b.namebankID) AS nameID, coalesce(s2.cur, b.basionymGroupID) AS basionymID,
                   b.basionymGroupConfidence AS conf
            FROM nb2.basionymGroups b
            LEFT JOIN nm_sup s1 ON s1.old = b.namebankID LEFT JOIN nm_sup s2 ON s2.old = b.basionymGroupID
            WHERE b.namebankID <> b.basionymGroupID)
        SELECT CASE WHEN type = 'spelling correction' THEN preferred ELSE variant END AS nameID,
               CASE WHEN type = 'spelling correction' THEN variant ELSE preferred END AS relatedNameID,
               type, 'uBio lexical group: ' || qualifier AS remarks
        FROM lex
        UNION ALL
        SELECT bas.nameID, bas.basionymID, 'basionym', 'uBio basionym group (confidence ' || bas.conf || ')'
        FROM bas JOIN nm_name n1 ON n1.namebankID = bas.nameID JOIN nm_name n2 ON n2.namebankID = bas.basionymID
        WHERE coalesce(n1.canonical, n1.nameString) <> coalesce(n2.canonical, n2.nameString)""")
    arc.write(con, "NameRelation", """
        SELECT DISTINCT ON (nameID, relatedNameID, type)
               CAST(nameID AS VARCHAR) AS nameID, CAST(relatedNameID AS VARCHAR) AS relatedNameID, type, remarks
        FROM rel
        SEMI JOIN usage_ids u1 ON u1.namebankID = rel.nameID
        SEMI JOIN usage_ids u2 ON u2.namebankID = rel.relatedNameID
        WHERE nameID <> relatedNameID
        ORDER BY nameID, relatedNameID, type""")

    arc.metadata(load_metadata_template("ubio-namebank"))
    con.close()
    arc.finish(keep=keep)


if __name__ == "__main__":
    main(sys.argv[1:])
