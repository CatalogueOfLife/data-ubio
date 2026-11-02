# uBio → ColDP

Converts the November 2015 uBio database dump (`ubio-db.tar`, 16 gzipped MySQL 5.1 dumps, about 25–30 GB uncompressed) into
[ColDP](https://github.com/CatalogueOfLife/coldp) archives for ChecklistBank.

With the full dump, the tar is never extracted. `ubio_coldp/dump.py` streams the needed tables straight out of `tar → gzip` and parses
the `INSERT` statements. They are staged in DuckDB (`work/ubio.duckdb`, about 0.9 GB), and SQL then writes the ColDP
files. The whole pipeline needs about 1.2 GB of disk. A guard (`ubio_coldp/disk.py`, `UBIO_DISK_BUDGET_GB`, default 45)
aborts if `work/` plus `output/` grows past the budget.

## Source data

The original dump (`ubio-db.tar`, 5.4 GB) is not in this repository. `source/` holds the subset the exporters need:
37 tables as zstd Parquet, 218 MB, each file under 45 MB. `ubio_coldp/archive_source.py` produced it from the full
dump, and its `filters()` function documents exactly which rows and columns were kept. In short:

- **NameBank:** all names, ranks and citations. Language rows only for names with a vernacular language.
  Lexical groups only for misspellings and lexical variants. Basionym links where they differ from the name itself.
  External-database mappings only for collections with a ChecklistBank identifier scope. Extinct flags from the old
  NameBank.
- **ClassificationBank:** only uBiota (104), Hershkovitz (57) and the lists in `config/classifications.yaml`.
- **Nomenclator Zoologicus:** spam comments (with links) are removed and email addresses redacted.
- **Index Animalium, Hershkovitz, uBiota vernacular names:** complete for the columns used.

The ColDP archives built from `source/` have exactly the same row counts as those built from the full dump.

## Run

```sh
uv sync
uv run python -m ubio_coldp.stage --source          # source/*.parquet -> work/ubio.duckdb (~10 s)
# or, with the full dump in ./ubio-db.tar:
# uv run python -m ubio_coldp.stage                 # tar -> work/ubio.duckdb (~3 min)
# uv run python -m ubio_coldp.archive_source        # work/ubio.duckdb -> source/
uv run python -m ubio_coldp.profile                 # optional: profiling report used for the mapping decisions
uv run python -m ubio_coldp.export_main             # output/ubio-namebank.zip
uv run python -m ubio_coldp.export_nz               # output/nomenclator-zoologicus.zip
uv run python -m ubio_coldp.export_index_animalium  # output/index-animalium.zip
uv run python -m ubio_coldp.export_hershkovitz      # output/hershkovitz-whales.zip
uv run python -m ubio_coldp.export_classification   # output/classifications/<cid>-<slug>.zip (config/classifications.yaml)
uv run python -m ubio_coldp.validate                # integrity checks on every zip
```

Every exporter takes `--keep` to keep the unzipped folder next to the zip.

## Archives

| Archive | Source | Content |
|---|---|---|
| `ubio-namebank` | namebank2, classificationbank 104, ubt_nb | uBiota tree (1.14M accepted taxa), 9.2M other NameBank names as bare names, 629k vernacular names, 423k references, 452k name relations, alternative IDs for external databases |
| `nomenclator-zoologicus` | NZ | 366k genus-group names with flags, comments and xrefs; replacement-name and later-homonym relations. IDs match ChecklistBank dataset 126539 |
| `index-animalium` | Index_Animalium | 423k entries (Sherborn 1758–1850), microcitations, basionym links, nomina nuda |
| `hershkovitz-whales` | classificationbank 57 + Hershkovitz | Catalog of Living Whales (1966): synonyms, chresonymy references, type material, distributions |
| `classifications/*` | classificationbank | 37 lists transcribed by uBio (birds, fish, Lepidoptera, Cnidaria, …). Copies of external databases are excluded |

## Mapping notes

- **IDs**
  - NameBank `namebankID` is used as the usage and name ID. Synonyms from ClassificationBank are `s<synonymsID>`.
  - Every name carries `ubio:<namebankID>` in `nameAlternativeID`.
- **Superseded names** (`supercedureID`, 352k empty records) are dropped and every reference to them is redirected to the current ID.
- **uBiota tree repair**
  - About 7.8k names placed twice keep their first placement.
  - Parents missing from the classification are added as implied nodes (4,959) where the name exists. Nodes are re-attached to their nearest ancestor present in the ancestry path.
- **Name split**: the NameBank canonical form gives `scientificName` and the rest of the string becomes `authorship`. Otherwise the full string is kept.
- **Ranks**: when NameBank gives several ranks for a name, the standard rank name is preferred.
- **References**: NameBank citations become references.
  - `nameReferenceID` is only set when the citation year equals the year in the name.
  - All other citations go into `referenceID`, for placed taxa only.
- **Vernacular names**: NameBank vernacular names (with their parent name), plus ubt_nb `name_links` and `common_search`.
  - Language comes from ISO 639-3 or 639-2, with overrides for common uBio languages that lack a code (e.g. Mandarin → `cmn`).
  - ColDP needs a taxon for each vernacular name, so the 810k whose scientific name is not in uBiota are dropped.
- **NameRelations**
  - `lexicalGroups` misspellings → `spelling correction`
  - lexical and illegitimate variants → `homotypic`
  - `basionymGroups` → `basionym`, only where the canonical forms differ
  - The roughly 4M other lexical-group links only connect authorship and formatting variants of the same name, so they are skipped.
- **alternativeID** only uses NameBank collections with a registered ChecklistBank identifier scope: `tsn`, `ncbi`, `if`, `tropicos`, `worms`, `grin`, `iucn`, `usda`, `fishbase`, `algaebase`, `fauna-europaea`, `biolib`. Links to PubMed, image sites and old CoL/GBIF record IDs are skipped.
- **NZ editorial comments**: anonymous web comments were heavily spammed (22.7k of 23.6k). Comments with links or spam keywords are dropped, and email addresses are redacted.
- **Text**: mojibake (double-encoded UTF-8) and HTML entities are repaired with `ftfy`.

## Not converted

- `infoButton`, `rss`, `termsapp`, `TerminologyExerciseDB`, `agi`, `bhl`, `ubio`
- the NameBank edit logs (`changes`)
- the `*_old` tables
- copies of CoL, ITIS, NCBI, Species 2000, IUCN, GBIF and ERMS in ClassificationBank
- PreUnion (103)
- NameBank `diptera`, `index_berg` and `ZooRecordTemp`
- AMNH
- the UBT ontology extras (EOL-derived distributions, AnAge longevity)

## Metadata

- **Templates:** `metadata/*.yaml` holds the templates for the main, NZ, Index Animalium and Hershkovitz archives, and `metadata/classification.yaml` the shared template for the lists. All archives are CC-BY-4.0, with a `conversion` section pointing to this repository.
- **Creators:**
  - The main archive credits the uBio team: David Remsen, Patrick Leary, Catherine Norton and Dmitry Mozzherin.
  - The other archives credit the author of their source (Neave, Sherborn, Hershkovitz, and the list authors in `config/classification-creators.yaml`), with David Remsen as contributor.
  - The lists without a stated author (32, 45, 5, 51) have no creator.
- **Classification metadata:** title, description and date come from ClassificationBank. The taxonomic scope is derived from the root taxa.
