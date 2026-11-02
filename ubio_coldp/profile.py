"""Profiling reports over the staged data, used to settle mapping decisions.

    uv run python -m ubio_coldp.profile
"""
from __future__ import annotations

from .coldp import connect

QUERIES = {
    "names: visible / supercedure / clientClass": """
        SELECT visibleFlag, supercedureID <> 0 AS superseded, clientClassID, count(*) n
        FROM nb2.names GROUP BY ALL ORDER BY n DESC LIMIT 20""",
    "nameLanguages: top languages": """
        SELECT nl.languageID, l.languageName, l."639-3", count(*) n,
               count(*) FILTER (WHERE parentNamebankID <> 0) with_parent
        FROM nb2.nameLanguages nl LEFT JOIN nb2.languages l USING (languageID)
        GROUP BY ALL ORDER BY n DESC LIMIT 15""",
    "names without language row": """
        SELECT count(*) FROM nb2.names n ANTI JOIN nb2.nameLanguages nl USING (namebankID)""",
    "names with >1 language row": """
        SELECT count(*) FROM (SELECT namebankID FROM nb2.nameLanguages GROUP BY 1 HAVING count(*) > 1)""",
    "lexical qualifiers (member != group)": """
        SELECT q.lexicalQualifier, count(*) n, count(*) FILTER (WHERE g.namebankID <> g.lexicalGroupID) variants
        FROM nb2.lexicalGroups g LEFT JOIN nb2.lexicalQualifiers q USING (lexicalQualifierID)
        GROUP BY ALL ORDER BY n DESC""",
    "lexical variant examples": """
        SELECT q.lexicalQualifier, a.nameString variant, b.nameString preferred, g.lexicalGroupConfidence
        FROM nb2.lexicalGroups g JOIN nb2.lexicalQualifiers q USING (lexicalQualifierID)
        JOIN nb2.names a ON a.namebankID = g.namebankID JOIN nb2.names b ON b.namebankID = g.lexicalGroupID
        WHERE g.namebankID <> g.lexicalGroupID USING SAMPLE 12""",
    "basionym groups": """
        SELECT basionymGroupConfidence, basionymFlag, count(*) n,
               count(*) FILTER (WHERE namebankID <> basionymGroupID) rel
        FROM nb2.basionymGroups GROUP BY ALL ORDER BY n DESC""",
    "basionym examples": """
        SELECT a.nameString, b.nameString basionym FROM nb2.basionymGroups g
        JOIN nb2.names a ON a.namebankID = g.namebankID JOIN nb2.names b ON b.namebankID = g.basionymGroupID
        WHERE g.namebankID <> g.basionymGroupID USING SAMPLE 8""",
    "ranks: top nameRanks": """
        SELECT r.rankID, r.rankName, count(*) n FROM nb2.nameRanks nr LEFT JOIN nb2.ranks r USING (rankID)
        GROUP BY ALL ORDER BY n DESC LIMIT 25""",
    "uBiota 104: rank ids vs nb1.ranks": """
        SELECT h.rankID, r1.rankName nb1_rank, count(*) n FROM cb.Hierarchies h
        LEFT JOIN nb1.ranks r1 ON r1.rankID = h.rankID
        WHERE _classificationsID = 104 GROUP BY ALL ORDER BY n DESC LIMIT 25""",
    "uBiota 104: child uniqueness / parents present / names present": """
        SELECT count(*) nodes, count(DISTINCT childID) children,
               count(*) FILTER (WHERE parentID NOT IN (SELECT childID FROM cb.Hierarchies WHERE _classificationsID = 104)) orphan_parent,
               count(*) FILTER (WHERE childID NOT IN (SELECT namebankID FROM nb2.names)) missing_name
        FROM cb.Hierarchies WHERE _classificationsID = 104""",
    "uBiota 104: root nodes": """
        SELECT h.childID, h.parentID, n.nameString, h.rankID FROM cb.Hierarchies h
        LEFT JOIN nb2.names n ON n.namebankID = h.childID
        WHERE _classificationsID = 104 AND (parentID = 0 OR parentID NOT IN
            (SELECT childID FROM cb.Hierarchies WHERE _classificationsID = 104)) LIMIT 20""",
    "uBiota 104: sample with rank": """
        SELECT h.childID, n.nameString, h.rankID, nr.rankID nb2_rank, r.rankName, h.ancestry
        FROM cb.Hierarchies h LEFT JOIN nb2.names n ON n.namebankID = h.childID
        LEFT JOIN nb2.nameRanks nr ON nr.namebankID = h.childID LEFT JOIN nb2.ranks r ON r.rankID = nr.rankID
        WHERE _classificationsID = 104 USING SAMPLE 10""",
    "mappings per collection": """
        SELECT m.collectionsID, c.collectionsTitle, c.abbreviation, count(*) n,
               any_value(o.collectionsURL) url_template
        FROM nb2.mappings m LEFT JOIN nb2.collections c USING (collectionsID)
        LEFT JOIN nb1.collections o ON o.collectionsID = m.collectionsID
        GROUP BY ALL ORDER BY n DESC LIMIT 60""",
    "commentLayer values": """
        SELECT c.collectionsTitle, t.type, v.value, count(*) n FROM nb2.commentLayer l
        LEFT JOIN nb2.collections c USING (collectionsID) LEFT JOIN nb2.commentTypes t USING (typeID)
        LEFT JOIN nb2.commentValues v USING (valueID) GROUP BY ALL ORDER BY n DESC LIMIT 25""",
    "citations / chresonym": """
        SELECT (SELECT count(*) FROM nb2.citations) citations, (SELECT count(*) FROM nb2.chresonym) links,
               (SELECT count(DISTINCT namebankID) FROM nb2.chresonym) names_with_citation,
               (SELECT count(*) FROM nb2.citations WHERE pubMedID <> 0) with_pubmed""",
    "ubt vernaculars": """
        SELECT (SELECT count(*) FROM ubt.name_links) name_links,
               (SELECT count(DISTINCT nb_id) FROM ubt.name_links) nl_taxa,
               (SELECT count(*) FROM ubt.common_search) common_search,
               (SELECT count(DISTINCT nb_id) FROM ubt.common_search) cs_taxa""",
    "ubt lang ids vs nb2.languages": """
        SELECT c.lang, l.languageName, l."639-3", count(*) n FROM ubt.common_search c
        LEFT JOIN nb2.languages l ON l.languageID = c.lang GROUP BY ALL ORDER BY n DESC LIMIT 12""",
}


def main() -> None:
    con = connect()
    for title, sql in QUERIES.items():
        print(f"\n### {title}")
        try:
            con.sql(sql).show(max_width=220, max_rows=60)
        except Exception as e:  # keep going: this is a report
            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
