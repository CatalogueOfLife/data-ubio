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

## Rebuild and upload

You need [uv](https://docs.astral.sh/uv/) and about 3 GB of free disk. The full rebuild from this repository takes
about two minutes; the original `ubio-db.tar` is not needed.

**1. Set up**

```sh
git clone https://github.com/CatalogueOfLife/data-ubio.git && cd data-ubio
uv sync
```

**2. Load the source data** from `source/` into the working database `work/ubio.duckdb`

```sh
uv run python -m ubio_coldp.stage --source
```

**3. Build all archives** into `output/` and check them

```sh
uv run python -m ubio_coldp.export_main             # output/ubio-namebank.zip
uv run python -m ubio_coldp.export_nz               # output/nomenclator-zoologicus.zip
uv run python -m ubio_coldp.export_index_animalium  # output/index-animalium.zip
uv run python -m ubio_coldp.export_hershkovitz      # output/hershkovitz-whales.zip
uv run python -m ubio_coldp.export_classification   # output/classifications/<cid>-<slug>.zip (config/classifications.yaml)
uv run python -m ubio_coldp.validate                # integrity checks on every zip; must report 0 with errors
```

Each exporter replaces its own archive and can be run on its own. Add `--keep` to also keep the unzipped files next to
the zip. `export_classification` takes ClassificationBank ids to build only some lists, e.g. `export_classification 8 35`.

**4. Upload to ChecklistBank**

```sh
./upload.sh --dry-run      # list the archives and the dataset each one goes to
./upload.sh                # upload all of them; asks for your ChecklistBank username and password
```

`upload.sh` uploads every archive found in `output/`, smallest first, and ChecklistBank imports them in the
background.
- **Options:** give archive names to upload only those (e.g. `./upload.sh ubio-namebank`), or use `--skip=a,b` to leave some out.
- **Dataset keys:** `config/checklistbank.yaml` maps every archive to its dataset key. An archive that isn't listed yet gets a new private dataset, which you need to publish in ChecklistBank.
- **Login:** set `CLB_TOKEN` to a JWT instead of logging in.
- **Other environments:** set `CLB_API`, e.g. to `https://api.dev.checklistbank.org`.

### Rebuilding from the original dump

Only needed to change which source rows are kept, i.e. the filters in `ubio_coldp/archive_source.py`. Place
`ubio-db.tar` in the repository root, then:

```sh
uv run python -m ubio_coldp.stage                   # tar -> work/ubio.duckdb (~3 min, streams without extracting)
uv run python -m ubio_coldp.archive_source          # work/ubio.duckdb -> source/ (commit the result)
uv run python -m ubio_coldp.profile                 # optional: profiling report behind the mapping decisions
```

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
- **Verbatim:** the uBio data are represented as they were, and nothing is inferred.
  - uBio name strings are passed unchanged as `scientificName` for ChecklistBank to parse.
  - In a classification, a node whose parent is not part of it becomes a root, with the source parent in `remarks`.
  - ColDP allows one parent per ID, so a name placed more than once keeps its first placement, and the other parents are listed in `remarks`.
- **Ranks:** a wrong rank is worse than none, so uncertain ranks are left out and ChecklistBank infers them from the name.
  - uBio rank labels are mapped with ChecklistBank's rank parser into `config/ranks.yaml`. Unparsable, uncertain (`?var`) and ambiguous (`division`) labels map to nothing.
  - A rank that contradicts the number of parts of uBio's canonical form (e.g. trinomials ranked species) is dropped.
  - Names with conflicting ranks in NameBank get none.
  - Nomenclator Zoologicus and Index Animalium state no ranks, so none are given.
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
