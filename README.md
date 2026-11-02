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
CLB_AUTH_HEADER=~/.clb-auth uv run python -m ubio_coldp.clb_upload [archive ...]   # upload to ChecklistBank
```

`config/checklistbank.yaml` maps every archive to its ChecklistBank dataset key. `clb_upload` creates a private
dataset for any archive that isn't listed yet. The header file holds a single line `Authorization: Bearer <jwt>`.

Every exporter takes `--keep` to keep the unzipped folder next to the zip.

## Archives

| Archive | Source | Content |
|---|---|---|
| `ubio-namebank` | namebank2, classificationbank 104, ubt_nb | uBiota tree (1.14M accepted taxa), 9.2M other NameBank names as bare names, 629k vernacular names, 423k references, 452k name relations, alternative IDs for external databases |
| `nomenclator-zoologicus` | NZ | 366k genus-group names with flags, comments and xrefs; replacement-name and later-homonym relations. IDs match ChecklistBank dataset 126539 |
| `index-animalium` | Index_Animalium | 423k entries (Sherborn 1758–1850), microcitations, basionym links, nomina nuda |
| `hershkovitz-whales` | classificationbank 57 + Hershkovitz | Catalog of Living Whales (1966): synonyms, chresonymy references, type material, distributions |
| `classifications/*` | classificationbank | 37 lists transcribed by uBio (birds, fish, Lepidoptera, Cnidaria, …). Copies of external databases are excluded |

## ChecklistBank datasets

Every archive is published as its own ChecklistBank dataset. `config/checklistbank.yaml` holds the same mapping.

| Dataset | Key | Archive |
|---|---|---|
| [uBio NameBank and uBiota](https://www.checklistbank.org/dataset/316856) | 316856 | `ubio-namebank` |
| [Nomenclator Zoologicus (uBio digital edition)](https://www.checklistbank.org/dataset/316817) | 316817 | `nomenclator-zoologicus` |
| [Index Animalium (uBio digital edition)](https://www.checklistbank.org/dataset/316855) | 316855 | `index-animalium` |
| [Catalog of Living Whales (Hershkovitz 1966)](https://www.checklistbank.org/dataset/316815) | 316815 | `hershkovitz-whales` |

Classifications transcribed by uBio (ClassificationBank id in the archive name):

| Dataset | Key | Archive |
|---|---|---|
| [Birds: AOU 83](https://www.checklistbank.org/dataset/316833) | 316833 | `1-birds-aou-83` |
| [Birds: AOU 98](https://www.checklistbank.org/dataset/316832) | 316832 | `2-birds-aou-98` |
| [Birds: James Lee Peters](https://www.checklistbank.org/dataset/316843) | 316843 | `3-birds-james-lee-peters` |
| [Birds: Morony, Bock, and Farrand](https://www.checklistbank.org/dataset/316844) | 316844 | `4-birds-morony-bock-and-farrand` |
| [Birds: Nomina Avium Mundi](https://www.checklistbank.org/dataset/316848) | 316848 | `5-birds-nomina-avium-mundi` |
| [Birds: Sibley and Monroe](https://www.checklistbank.org/dataset/316847) | 316847 | `6-birds-sibley-and-monroe` |
| [Birds: Zoonomen](https://www.checklistbank.org/dataset/316850) | 316850 | `7-birds-zoonomen` |
| [Fish: W.T. Eschmeyer](https://www.checklistbank.org/dataset/316854) | 316854 | `8-fish-w-t-eschmeyer` |
| [Reptilia - based on the EMBL classification](https://www.checklistbank.org/dataset/316851) | 316851 | `9-reptilia-based-on-the-embl-classification` |
| [Amphibia World Checklist](https://www.checklistbank.org/dataset/316840) | 316840 | `10-amphibia-world-checklist` |
| [Mammals: Wilson and Reeder, 1993](https://www.checklistbank.org/dataset/316841) | 316841 | `11-mammals-wilson-and-reeder-1993` |
| [Birds:Howard and Moore (edition 2)](https://www.checklistbank.org/dataset/316845) | 316845 | `23-birds-howard-and-moore-edition-2` |
| [Annelida: Rouse & Fauchald](https://www.checklistbank.org/dataset/316837) | 316837 | `24-annelida-rouse-fauchald` |
| [Margulis & Schwartz, Third Edition](https://www.checklistbank.org/dataset/316830) | 316830 | `25-margulis-schwartz-third-edition` |
| [Chaetognatha (based on Erik V. Thuesen )](https://www.checklistbank.org/dataset/316818) | 316818 | `28-chaetognatha-based-on-erik-v-thuesen` |
| [Bacteria - based on J.P. Euzeby.](https://www.checklistbank.org/dataset/316838) | 316838 | `29-bacteria-based-on-j-p-euzeby` |
| [Ctenophora](https://www.checklistbank.org/dataset/316826) | 316826 | `30-ctenophora` |
| [Bryozoa](https://www.checklistbank.org/dataset/316831) | 316831 | `31-bryozoa` |
| [Cnidaria](https://www.checklistbank.org/dataset/316839) | 316839 | `32-cnidaria` |
| [Carpoids](https://www.checklistbank.org/dataset/316825) | 316825 | `33-carpoids` |
| [Echiurans based on Murina](https://www.checklistbank.org/dataset/316823) | 316823 | `34-echiurans-based-on-murina` |
| [Chapin, Birds of the Belgian Congo](https://www.checklistbank.org/dataset/316849) | 316849 | `35-chapin-birds-of-the-belgian-congo` |
| [Hemichordata](https://www.checklistbank.org/dataset/316822) | 316822 | `41-hemichordata` |
| [Entoprocta](https://www.checklistbank.org/dataset/316821) | 316821 | `42-entoprocta` |
| [Sipunculids (based on Cutler)](https://www.checklistbank.org/dataset/316824) | 316824 | `43-sipunculids-based-on-cutler` |
| [Rotifera](https://www.checklistbank.org/dataset/316827) | 316827 | `44-rotifera` |
| [Nemertea](https://www.checklistbank.org/dataset/316820) | 316820 | `45-nemertea` |
| [Onychophorans (Centre International de Myriapodologie)](https://www.checklistbank.org/dataset/316819) | 316819 | `49-onychophorans-centre-international-de-myriapodologie` |
| [Trilobita](https://www.checklistbank.org/dataset/316836) | 316836 | `50-trilobita` |
| [Coleoptera classification to subfamily](https://www.checklistbank.org/dataset/316828) | 316828 | `51-coleoptera-classification-to-subfamily` |
| [Collembola (Janssens)](https://www.checklistbank.org/dataset/316842) | 316842 | `52-collembola-janssens` |
| [Lepidoptera NHM](https://www.checklistbank.org/dataset/316853) | 316853 | `53-lepidoptera-nhm` |
| [Apoidea to genera (Michener)](https://www.checklistbank.org/dataset/316829) | 316829 | `54-apoidea-to-genera-michener` |
| [Tineidae (Lepidoptera) (Robinson)](https://www.checklistbank.org/dataset/316834) | 316834 | `55-tineidae-lepidoptera-robinson` |
| [Howard and Moore, 3rd Edition](https://www.checklistbank.org/dataset/316852) | 316852 | `66-howard-and-moore-3rd-edition` |
| [Mallophaga (suborders Amblycera, Ischnocera, and Rhynchophthirina)](https://www.checklistbank.org/dataset/316835) | 316835 | `76-mallophaga-suborders-amblycera-ischnocera-and-rhynchophthiri` |
| [Cnidaria from Dr. Jacob van der Land](https://www.checklistbank.org/dataset/316846) | 316846 | `86-cnidaria-from-dr-jacob-van-der-land` |

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
