"""Export ClassificationBank classifications as ColDP archives.

    uv run python -m ubio_coldp.export_classification            # all cids in config/classifications.yaml
    uv run python -m ubio_coldp.export_classification 8 53 66

Shared building blocks (name tables, tree repair) are also used by export_main and export_hershkovitz.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import yaml

from .coldp import Archive, connect, fix_text, load_metadata_template
from .disk import check_budget

CONFIG = Path(__file__).resolve().parent.parent / "config" / "classifications.yaml"
REPO_URL = "https://github.com/CatalogueOfLife/data-ubio"
# all exported classifications cover animals, except Margulis & Schwartz (Five Kingdoms) and bacteria
NOT_ZOOLOGICAL = {25, 29}
GEO_SCOPE = {35: "Belgian Congo (Democratic Republic of the Congo)", 1: "North America", 2: "North America"}
CREATORS = Path(__file__).resolve().parent.parent / "config" / "classification-creators.yaml"

RANKS = Path(__file__).resolve().parent.parent / "config" / "ranks.yaml"

# cb.SynonymRelations.relationID -> (ColDP status or None to skip, remark or None, vernacular?)
SYN_REL = {
    1: ("synonym", None, False),
    2: (None, None, True),
    3: (None, None, False),  # acronym
    4: ("synonym", "anamorph", False),
    5: (None, None, False),  # blast name
    6: ("synonym", "equivalent name", False),
    7: (None, None, False),  # genbank acronym
    8: ("synonym", "genbank anamorph", False),
    9: (None, None, True),
    10: ("synonym", "genbank synonym", False),
    11: ("synonym", "in-part", False),
    12: ("synonym", "includes", False),
    13: ("synonym", "misnomer", False),
    14: ("synonym", "misspelling", False),
    15: ("synonym", "teleomorph", False),
    16: ("ambiguous synonym", None, False),
    17: ("misapplied", None, False),
    18: ("provisionally accepted", None, False),
    19: ("synonym", "listed as accepted name", False),
}


def prepare_names(con: duckdb.DuckDBPyConnection) -> None:
    """Create temp tables shared by all exporters.

    nm_sup:   superseded namebankID -> current namebankID (chains resolved)
    nm_name:  namebankID -> scientificName, authorship, rank, extinct, year (non-empty, not superseded)
    nm_cite:  namebankID -> best publishedIn citation id, plus all citation ids
    """
    if con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'nm_name' AND temporary").fetchone()[0]:
        return
    print("  preparing name tables", flush=True)
    sup = dict(con.execute("SELECT namebankID, supercedureID FROM nb2.names WHERE supercedureID <> 0").fetchall())
    resolved = {}
    for k in sup:
        cur, seen = sup[k], {k}
        while cur in sup and cur not in seen:
            seen.add(cur)
            cur = sup[cur]
        resolved[k] = cur
    con.register("_sup", pa.table({"old": list(resolved), "cur": list(resolved.values())}))
    con.execute("CREATE OR REPLACE TEMP TABLE nm_sup AS SELECT old, cur FROM _sup")
    con.unregister("_sup")
    con.execute("CREATE OR REPLACE TEMP MACRO cur_id(x) AS coalesce((SELECT cur FROM nm_sup WHERE old = x), x)")

    # uBio rank labels mapped with the ChecklistBank rank parser (config/ranks.yaml); unparsable labels -> NULL
    with open(RANKS, encoding="utf-8") as f:
        rcfg = yaml.safe_load(f)
    labels = [(k, v) for k, v in rcfg["ranks"].items()]
    con.register("_rm", pa.table({"label": [k for k, _ in labels], "rank": [v for _, v in labels]}))
    con.execute("CREATE OR REPLACE TEMP TABLE rank_map AS SELECT * FROM _rm")
    con.unregister("_rm")
    con.register("_rl", pa.table({"rank": list(rcfg["levels"]), "level": list(rcfg["levels"].values())}))
    con.execute("CREATE OR REPLACE TEMP TABLE rank_lvl AS SELECT * FROM _rl")
    con.unregister("_rl")
    con.execute("CREATE OR REPLACE TEMP MACRO map_rank(x) AS (SELECT rank FROM rank_map WHERE label = x)")
    # 1 = genus and above, 2 = species, 3 = infraspecific; NULL = unknown
    con.execute("CREATE OR REPLACE TEMP MACRO rank_level(r) AS (SELECT level FROM rank_lvl WHERE rank = r)")
    # a name with several, conflicting ranks in NameBank gets none
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nm_rank AS
        WITH m AS (
            SELECT DISTINCT nr.namebankID, rm.rank
            FROM nb2.nameRanks nr JOIN nb2.ranks r USING (rankID) JOIN rank_map rm ON rm.label = r.rankName
            WHERE rm.rank IS NOT NULL)
        SELECT namebankID,
               CASE WHEN count(*) FILTER (WHERE rank <> 'infraspecific name') = 1
                        THEN any_value(rank) FILTER (WHERE rank <> 'infraspecific name')
                    WHEN count(*) FILTER (WHERE rank <> 'infraspecific name') = 0 THEN 'infraspecific name' END AS rank
        FROM m GROUP BY 1""")
    con.execute("""CREATE OR REPLACE TEMP MACRO name_words(c) AS
        CASE WHEN c IS NULL OR trim(c) = '' THEN NULL ELSE len(string_split(trim(c), ' ')) END""")
    # uBio's rank, left out when uncertain: when it contradicts the number of parts of uBio's canonical form,
    # or when the canonical form is truncated and a single word follows ("Balæna Mysticetus": epithet or author?)
    con.execute("""CREATE OR REPLACE TEMP MACRO final_rank(r, c, ns) AS CASE
        WHEN r IS NULL THEN NULL
        WHEN name_words(c) < rank_level(r) AND starts_with(ns, c || ' ')
             AND name_words(substr(ns, length(c) + 2)) = 1 THEN NULL
        WHEN name_words(c) IS NULL OR rank_level(r) IS NULL OR rank_level(r) = 3
             OR name_words(c) <= rank_level(r) THEN r
        END""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nm_name AS
        SELECT n.namebankID,
               n.nameString,
               CASE WHEN cf.canonicalForm <> '' AND starts_with(n.nameString, cf.canonicalForm || ' ')
                    THEN cf.canonicalForm ELSE n.nameString END AS scientificName,
               CASE WHEN cf.canonicalForm <> '' AND starts_with(n.nameString, cf.canonicalForm || ' ')
                    THEN trim(substr(n.nameString, length(cf.canonicalForm) + 2)) END AS authorship,
               cf.canonicalForm AS canonical,
               r.rank,
               CASE WHEN o.extinctFlag = 1 THEN 'true' END AS extinct,
               TRY_CAST(regexp_extract(n.nameString, '\\b(1[5-9][0-9]{2}|20[0-2][0-9])\\b', 1) AS INTEGER) AS year,
               n.visibleFlag, n.clientClassID
        FROM nb2.names n
        LEFT JOIN nb2.canonicalForms cf USING (canonicalFormID)
        LEFT JOIN nm_rank r USING (namebankID)
        LEFT JOIN nb1.names o USING (namebankID)
        WHERE n.supercedureID = 0 AND trim(n.nameString) <> ''""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nm_cite AS
        WITH c AS (
            SELECT ch.namebankID, ch.citationsID, TRY_CAST(ci.year AS INTEGER) AS cyear
            FROM nb2.chresonym ch JOIN nb2.citations ci USING (citationsID))
        , b AS (
            SELECT c.namebankID, min(c.citationsID) FILTER (WHERE c.cyear = n.year) AS bestID
            FROM c JOIN nm_name n USING (namebankID) GROUP BY 1)
        SELECT c.namebankID, any_value(b.bestID) AS bestID,
               string_agg(DISTINCT 'c' || c.citationsID, ',' ORDER BY 'c' || c.citationsID)
                   FILTER (WHERE c.citationsID IS DISTINCT FROM b.bestID) AS otherRefs,
               list(c.citationsID) AS allIDs
        FROM c LEFT JOIN b USING (namebankID)
        GROUP BY 1""")


def build_tree(con: duckdb.DuckDBPyConnection, cid: int) -> pa.Table:
    """Return the tree of one classification as (ID, parentID, rankID, remarks), as close to the source as ColDP allows.

    - superseded namebankIDs are replaced by their current ID (superseded NameBank records are empty)
    - a name placed more than once keeps its first placement (lowest hierarchiesID); the other parents go to remarks
    - a parent that is not part of the classification is not invented: the node becomes a root and the source parent
      is given in remarks
    """
    rows = con.execute("""
        WITH h AS (
            SELECT hierarchiesID, cur_id(childID) AS child, cur_id(parentID) AS parent, rankID
            FROM cb.Hierarchies WHERE _classificationsID = ?)
        SELECT h.hierarchiesID, h.child, h.parent, h.rankID, p.nameString AS parent_name
        FROM h JOIN nm_name c ON c.namebankID = h.child LEFT JOIN nm_name p ON p.namebankID = h.parent
        ORDER BY h.hierarchiesID""", [cid]).fetchall()
    nodes: dict[int, list] = {}
    extra: dict[int, list[str]] = {}
    for hid, child, parent, rank, parent_name in rows:
        label = f"{parent_name} ({parent})" if parent_name else str(parent)
        if child in nodes:
            if parent and parent != nodes[child][0]:
                extra.setdefault(child, []).append(label)
            continue
        nodes[child] = [parent, rank, label]
    out_id, out_parent, out_rank, out_remarks = [], [], [], []
    orphans = 0
    for child, (parent, rank, label) in nodes.items():
        notes = []
        p = parent if parent in nodes and parent != child else None
        if p is None and parent:
            orphans += 1
            notes.append(f"source parent not in this classification: {label}")
        if child in extra:
            notes.append("also placed in source under: " + "; ".join(extra[child]))
        out_id.append(child)
        out_parent.append(p)
        out_rank.append(rank)
        out_remarks.append(notes)
    # a ColDP tree cannot have cycles
    parent_of = dict(zip(out_id, out_parent))
    for i, node in enumerate(out_id):
        seen, cur = {node}, parent_of[node]
        while cur is not None:
            if cur in seen:
                out_remarks[i].append(f"source parent {out_parent[i]} removed: cyclic classification")
                parent_of[node] = None
                out_parent[i] = None
                break
            seen.add(cur)
            cur = parent_of.get(cur)
    print(f"  tree {cid}: {len(rows):,} rows -> {len(out_id):,} nodes ({orphans:,} with a parent outside the "
          f"classification, {len(extra):,} placed more than once)", flush=True)
    return pa.table({"ID": pa.array(out_id, pa.int64()), "parentID": pa.array(out_parent, pa.int64()),
                     "rankID": pa.array(out_rank, pa.int64()),
                     "remarks": pa.array(["; ".join(r) or None for r in out_remarks], pa.string())})


def register_tree(con: duckdb.DuckDBPyConnection, cid: int, table: str = "tree") -> None:
    t = build_tree(con, cid)
    con.register("_tree", t)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT * FROM _tree")
    con.unregister("_tree")


# ISO 639-3 codes for uBio languages that lack one in nb2.languages
LANG_OVERRIDES = {
    "Mandarin Chinese": "cmn", "Greek, Modern (1453)": "ell", "Bikol": "bik", "Cebuano": "ceb",
    "Waray-waray": "war", "Farsi": "fas", "Ilokano": "ilo", "Carolinian": "cal", "Rumanian": "ron",
    "Hiligaynon": "hil", "Hawaiian": "haw", "Marshall": "mah", "Chavacano": "cbk", "Davawenyo": "daw",
    "Surigaonon": "sgd", "Gela": "nlg", "Cantonese": "yue",
}
_NO_LANG = ("Common name", "unspecified", "Scientific Name", "Operational Taxonomic Unit", "Other",
            "informal latinized name (vernacular concept only)")
VERNACULAR_LANG = (
    "CASE "
    + " ".join(f"WHEN l.languageName = '{k}' THEN '{v}'" for k, v in LANG_OVERRIDES.items())
    + " WHEN l.languageName IN (" + ", ".join(f"'{x}'" for x in _NO_LANG) + ") THEN NULL"
    + """ ELSE coalesce(nullif(l."639-3", ''), nullif(l."639-2", ''), l.languageName) END"""
)


def classification_meta(con: duckdb.DuckDBPyConnection, cid: int) -> dict:
    name, desc, url, date, group = con.execute("""
        SELECT c.classificationName, c.description, c.classURL, c.indexDate, g.groupName
        FROM cb.Classifications c LEFT JOIN cb.ClassificationStructure s USING (classificationsID)
        LEFT JOIN cb.ClassificationGroups g USING (groupID) WHERE c.classificationsID = ?""", [cid]).fetchone()
    return {"cid": cid, "name": fix_text(name), "description": fix_text(desc),
            "url": url if url and url.startswith("http") else None,
            "date": date if date and not date.startswith("0000") else None, "group": group}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def export_classification(cid: int, keep: bool = False, archive_name: str | None = None,
                          subdir: str | None = "classifications", before=None, after=None,
                          metadata: dict | None = None) -> Path:
    """before(con) may create temp table extra_remarks(ID, remarks) joined to accepted usages;
    after(con, arc) may write extra entities; metadata replaces the generated metadata."""
    con = connect(read_only=False)
    prepare_names(con)
    meta = classification_meta(con, cid)
    print(f"[classification {cid}] {meta['name']}", flush=True)
    register_tree(con, cid)
    arc = Archive(archive_name or f"{cid}-{slug(meta['name'])}", subdir=subdir)
    con.execute("CREATE OR REPLACE TEMP TABLE extra_remarks (ID BIGINT, remarks VARCHAR)")
    if before:
        before(con)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE syn AS
        SELECT s.synonymsID, cur_id(s.synonymID) AS nameID, cur_id(h.childID) AS acceptedID, s.relationID
        FROM cb.Synonyms s JOIN cb.Hierarchies h USING (hierarchiesID)
        WHERE s._classificationsID = ?""", [cid])
    statuses = " ".join(f"WHEN {k} THEN '{v[0]}'" for k, v in SYN_REL.items() if v[0])
    remarks = " ".join(f"WHEN {k} THEN 'uBio relation: {v[1]}'" for k, v in SYN_REL.items() if v[1])
    vern = ", ".join(str(k) for k, v in SYN_REL.items() if v[2])
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE syn_all AS
        SELECT syn.*, CASE relationID {statuses} END AS status, CASE relationID {remarks} END AS remarks
        FROM syn JOIN tree ON tree.ID = syn.acceptedID JOIN nm_name n ON n.namebankID = syn.nameID
        WHERE relationID NOT IN ({vern}) AND (CASE relationID {statuses} END) IS NOT NULL""")
    # one synonym per (name, accepted); self-synonyms (chresonyms of the accepted name) only contribute references
    con.execute("""
        CREATE OR REPLACE TEMP TABLE syn_ok AS
        SELECT min(synonymsID) AS synonymsID, nameID, acceptedID, arg_min(status, synonymsID) AS status,
               string_agg(DISTINCT remarks, '; ') AS remarks
        FROM syn_all WHERE nameID <> acceptedID GROUP BY nameID, acceptedID""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE syn_refs AS
        SELECT g.synonymsID, string_agg(DISTINCT 'c' || ch.citationsID, ',') AS refs
        FROM syn_all a JOIN syn_ok g USING (nameID, acceptedID) JOIN cb.Chresonym ch ON ch.synonymsID = a.synonymsID
        GROUP BY 1""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE self_refs AS
        SELECT a.acceptedID AS ID, string_agg(DISTINCT 'c' || ch.citationsID, ',') AS refs
        FROM syn_all a JOIN cb.Chresonym ch USING (synonymsID) WHERE a.nameID = a.acceptedID GROUP BY 1""")
    con.execute("""CREATE OR REPLACE TEMP MACRO ref_list(a, b) AS
        nullif(array_to_string(list_sort(list_distinct(list_filter(string_split(concat_ws(',', a, b), ','), lambda x: x <> ''))), ','), '')""")
    zoological = cid not in NOT_ZOOLOGICAL
    code = "'zoological'" if zoological else "NULL"
    arc.write(con, "NameUsage", f"""
        SELECT CAST(t.ID AS VARCHAR) AS ID, 'ubio:' || t.ID AS nameAlternativeID,
               CAST(t.parentID AS VARCHAR) AS parentID, 'accepted' AS status,
               n.nameString AS scientificName,
               final_rank(coalesce(map_rank(r.rankName), n.rank), n.canonical, n.nameString) AS rank,
               {code} AS code,
               CASE WHEN c.bestID IS NOT NULL THEN 'c' || c.bestID END AS nameReferenceID,
               ref_list(c.otherRefs, sr.refs) AS referenceID,
               n.extinct,
               concat_ws('; ', t.remarks, er.remarks) AS remarks
        FROM tree t JOIN nm_name n ON n.namebankID = t.ID
        LEFT JOIN extra_remarks er ON er.ID = t.ID
        LEFT JOIN nb2.ranks r ON r.rankID = t.rankID
        LEFT JOIN nm_cite c ON c.namebankID = t.ID
        LEFT JOIN self_refs sr ON sr.ID = t.ID
        UNION ALL
        SELECT 's' || s.synonymsID, 'ubio:' || s.nameID, CAST(s.acceptedID AS VARCHAR), s.status,
               n.nameString,
               final_rank(n.rank, n.canonical, n.nameString), {code},
               CASE WHEN c.bestID IS NOT NULL THEN 'c' || c.bestID END,
               ref_list(c.otherRefs, r.refs),
               n.extinct, s.remarks
        FROM syn_ok s JOIN nm_name n ON n.namebankID = s.nameID
        LEFT JOIN nm_cite c ON c.namebankID = s.nameID
        LEFT JOIN syn_refs r USING (synonymsID)
        ORDER BY 1""")
    # references: publishedIn citations plus classification chresonyms
    con.execute("""
        CREATE OR REPLACE TEMP TABLE used_refs AS
        SELECT unnest(allIDs) AS citationsID FROM nm_cite WHERE namebankID IN (SELECT ID FROM tree UNION SELECT nameID FROM syn_ok)
        UNION SELECT citationsID FROM cb.Chresonym WHERE synonymsID IN (SELECT synonymsID FROM syn_all)""")
    write_references(con, arc, "SELECT citationsID FROM used_refs WHERE citationsID IS NOT NULL")
    arc.write(con, "VernacularName", f"""
        SELECT DISTINCT CAST(h.ID AS VARCHAR) AS taxonID, n.nameString AS name, {VERNACULAR_LANG} AS language
        FROM syn JOIN tree h ON h.ID = syn.acceptedID JOIN nb2.names n ON n.namebankID = syn.nameID
        LEFT JOIN nb2.nameLanguages nl ON nl.namebankID = syn.nameID LEFT JOIN nb2.languages l USING (languageID)
        WHERE syn.relationID IN ({vern}) AND trim(n.nameString) <> ''
        ORDER BY 1, 2""")
    if after:
        after(con, arc)
    tmpl = load_metadata_template("classification")
    md = {
        "title": meta["name"],
        "alias": f"uBio CB {cid}",
        "description": "\n\n".join(x for x in [
            meta["description"],
            tmpl.get("description", "").format(cid=cid, group=meta["group"] or "-"),
        ] if x),
        **{k: v for k, v in tmpl.items() if k not in ("description",)},
    }
    roots = [r for r in con.execute("""
        SELECT n.scientificName, (SELECT count(*) FROM tree c WHERE c.parentID = t.ID) AS kids
        FROM tree t JOIN nm_name n ON n.namebankID = t.ID
        WHERE t.parentID IS NULL AND EXISTS (SELECT 1 FROM tree c WHERE c.parentID = t.ID) AND n.scientificName NOT LIKE '% %'
        ORDER BY kids DESC LIMIT 5""").fetchall()]
    roots = [name for name, kids in roots if kids * 5 >= roots[0][1]] if roots else []
    md["taxonomicScope"] = ", ".join(roots)
    md["geographicScope"] = GEO_SCOPE.get(cid, "global")
    md.setdefault("url", REPO_URL)
    with open(CREATORS, encoding="utf-8") as f:
        creators = yaml.safe_load(f) or {}
    if cid in creators:
        md = {k: md[k] for k in ("title", "alias", "description")} | {"creator": creators[cid]} | \
             {k: v for k, v in md.items() if k not in ("title", "alias", "description")}
    if meta["url"]:
        md["url"] = meta["url"]
    if meta["date"]:
        md["issued"] = meta["date"]
    arc.metadata(metadata or md)
    con.close()
    return arc.finish(keep=keep)


def write_references(con: duckdb.DuckDBPyConnection, arc: Archive, id_sql: str) -> int:
    return arc.write(con, "Reference", f"""
        SELECT 'c' || ci.citationsID AS ID,
               ci.citation AS citation,
               CASE WHEN ci.authority <> '' THEN ci.authority END AS author,
               CASE WHEN ci.year NOT IN ('', '0') THEN ci.year END AS issued,
               CASE WHEN ci.title <> '' THEN ci.title END AS title,
               CASE WHEN ci.title <> '' AND ci.source <> '' THEN ci.source END AS containerTitle,
               CASE WHEN ci.pubMedID > 0 THEN 'https://pubmed.ncbi.nlm.nih.gov/' || ci.pubMedID END AS link
        FROM nb2.citations ci WHERE ci.citationsID IN ({id_sql})
        ORDER BY ci.citationsID""")


def configured_cids() -> list[int]:
    with open(CONFIG) as f:
        cfg = yaml.safe_load(f)
    return [int(c) for c in cfg["export"]]


def main(args: list[str]) -> None:
    keep = "--keep" in args
    cids = [int(a) for a in args if a.isdigit()] or configured_cids()
    check_budget("classifications start")
    for cid in cids:
        export_classification(cid, keep=keep)


if __name__ == "__main__":
    main(sys.argv[1:])
