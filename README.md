# legallayout

A Python tool that parses legal PDF documents (acts, SEBI circulars, eGazette notifications, amendments) and converts them into structured, semantic HTML — along with IIIF Presentation API 3.0 manifests for image-based eGazette/SEBI documents.

It extracts text and layout information from PDFs, classifies content into headers, footers, sections, paragraphs, side notes, tables, and figures, and applies document-type-specific rules to reconstruct the structure of the original legal document.

## Features

- PDF → structured HTML conversion using coordinate-based layout analysis
- Cross-page header/footer detection via sequence matching
- Multi-column page layout detection and left-column-then-right-column reading-order reconstruction (works across all `pdf_type`s, and across both the pdfminer and OCR/ChromeLens extraction paths)
- Document-type-specific processing for `acts`, `sebi`/`sebi_circulars`, and `egazette`
- Amendment detection and structuring for legal amendment documents
- Table extraction, including borderless-table detection with cross-page continuation (`camelot-py` for bordered tables, plus lightweight logistic-regression classifiers trained online via reinforcement learning for header-row, region-merge, and continuation decisions on borderless tables — see [Borderless-table detection](#borderless-table-detection))
- OCR-based extraction: Chrome-Lens for the scanned-copy (`-sc`) page-text path; Tesseract for optional per-image figure-text extraction (`-ftx`), supporting English plus 15 Indian regional languages via `-ol/--ocr-language`
- IIIF Presentation API 3.0 manifest generation for `egazette`/`sebi` image documents — configurable public URL and server-root-relative URLs, an IIIF Image API Level 0 service per image, OCR text surfaced as searchable annotations, and optional rights/attribution/provider metadata (see [IIIF manifest generation](#iiif-manifest-generation))
- XML caching of intermediate pdfminer output for faster iteration

## Requirements

- Python 3
- [Git LFS](https://git-lfs.com/) (the fastText language model `model/lid.176.bin` is tracked via LFS)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) system binary, with language data for whichever `-ol/--ocr-language` codes you plan to use (e.g. on Debian/Ubuntu: `apt install tesseract-ocr tesseract-ocr-all`). Without it, `-ftx/--figure-text` OCR silently produces no text rather than failing loudly.

## Installation

```bash
git clone <repo-url>
cd legallayout
git lfs pull
pip install -r requirements.txt
```

## Usage

```bash
python -m source.Main -i <input_pdf> -o <output_directory> [options]
```

### Example

```bash
python -m source.Main -i test/test_pdfs/act1.pdf -o output/ -t acts
```

### Options

| Flag | Description |
|---|---|
| `-i, --input-filePath` | Input PDF file path (required) |
| `-o, --output-directory` | Output directory for the generated HTML (required) |
| `-fp, --start-page` | Start page number |
| `-lp, --end-page` | End page number |
| `-t, --type` | Document type: `acts`, `sebi`, `sebi_circulars`, or `egazette` |
| `-s, --sidenotes` | PDF has sidenotes or inline titles for acts, amendments|
| `-a, --amendments` | PDF contains amendments |
| `-de, --doc-end` | PDF has a document-end symbol (`---`) |
| `-fnc, --footnote-continuation` | Footnotes continue across pages |
| `-sc, --scanned-copy` | PDF is a scanned copy (routes through OCR) |
| `-ol, --ocr-language` | Tesseract language code for figure-text OCR (default: `eng`); one of `eng`, `asm`, `ben`, `guj`, `hin`, `kan`, `mal`, `mar`, `nep`, `ori`, `pan`, `san`, `snd`, `tam`, `tel`, `urd` |
| `-te, --table-extract` | Enable borderless-table extraction |
| `-mip, --min-img-pixels` | Minimum pixel area threshold for image filtering |
| `-pu, --public-base-url` | Public URL the output directory will be served from (e.g. `https://gazettes.servantsofknowledge.in/gzdl/html/andhra_extraordinary/2025-01-01`); used as the base for every URI in the IIIF manifest (`egazette`/`sebi` types only). Falls back to the `PUBLIC_BASE_URL` env var, then `http://localhost:8000` |
| `-sr, --server-root` | Local filesystem directory acting as the web server's document root (e.g. `/var/www`); the path from here to `--output-directory` becomes an extra URL segment inserted between `-pu` and `manifest/<pdfname>/...` (`egazette`/`sebi` types only). Never affects where files are written. Omit to have `--output-directory` itself act as the server root. `--output-directory` must be located inside it, or the tool fails fast with a clear error |
| `-rt, --rights` | IIIF manifest `rights` URI — a Creative Commons or RightsStatements.org license URI (`egazette`/`sebi` types only). Only added if supplied; never guessed |
| `-pi, --provider-id` / `-pn, --provider-name` | URI + display name of the organization presenting the manifest, used for the IIIF `provider` field (`egazette`/`sebi` types only). Both are required together — a partial pair is dropped with a warning, not emitted broken |
| `-at, --attribution` | Attribution text for the IIIF manifest's `requiredStatement` (`egazette`/`sebi` types only) |
| `-lm, --line-margin` | pdfminer line margin threshold |
| `-cm, --char-margin` | pdfminer char margin threshold |
| `-wm, --word-margin` | pdfminer word margin threshold |
| `-l, --loglevel` | Log level: `error`\|`warning`\|`info`\|`debug` (default: `info`) |
| `-g, --logfile` | Log file path |
| `-x, --keep-xml` | Keep intermediate XML in `cache_xml/` instead of deleting it |

### Borderless-table detection

`-te/--table-extract` enables detection of tables that have no ruling lines (bordered tables are already found by `camelot-py`'s lattice mode). Several decisions in that pipeline — whether a candidate region's first row is a header, whether two adjacent regions should be merged into one table, and whether a page continues a table from the previous page — are made by small logistic-regression classifiers (`source/TableExtraction.py`) that start from fixed default weights and adapt online via reinforcement learning as pages are processed:

- **Reward**: once a candidate region's fate is known (accepted as a table or rejected), the classifier gets a reward derived from that outcome — the region's `fill_ratio` if it was accepted, or how far short of the acceptance threshold it fell if rejected — and every decision that helped build that region is updated towards or away from the action it took, in proportion to that reward (a REINFORCE-style policy-gradient update, not naive self-confirmation).
- **Scope**: the classifiers are shared across all pages of one PDF (so they adapt to that document's own table formatting as they go), but start fresh from their defaults on every run — nothing is persisted to disk between runs or PDFs, so behavior stays reproducible and one document can't bias another's results.

**Cross-page continuation**: a borderless table that runs off the bottom of one page and resumes at the top of the next (common for multi-page amendment schedules) is stitched into a single continuous table rather than being treated as several unrelated ones:

- A page whose trailing table is large, bottom-cut, or carries a repeating column-ruler row (e.g. `(1)(2)(3)(4)(5)`) hands a column template to the next page. The next page's top-of-page content is matched against that template purely by coordinate alignment — no ruler or specific structure required, so this works for ordinary tables too, not just ruler-seeded schedules.
- Column positions adapt via a running average as more rows are seen (`ColumnTracker`), rather than trusting a single frozen snapshot, and new columns can be promoted mid-chain if recurring content doesn't match anything already tracked. Template propagation is a *union* of what each page detects, never a wholesale replace, so one noisy page's detection can't permanently erase an already-established column.
- A page that's a pure single-column overflow of the inherited table (e.g. a whole page of free-text continuation) emits no table of its own but still passes the template forward, so the chain survives runs of such pages and reconnects once the full column structure resumes.
- Detection is tuned to avoid both false negatives (content-loss checks ensure every candidate table-region textbox actually lands in the resulting table) and false positives (adaptive header/footer/side-note detection can misclassify table cells as page boilerplate; a page with an incoming continuation template reclaims any such misclassified box that geometrically belongs to the table).

## IIIF manifest generation

For `egazette`/`sebi` documents with extractable images, an [IIIF Presentation API 3.0](https://iiif.io/api/presentation/3.0/) manifest (`manifest/<pdfname>/manifest.json`) is written alongside the HTML output, and a `click here for iiif manifest` link to it is added to the HTML itself. No manifest is written for a document with no extractable images.

**Canvases are the embedded images actually collected from the PDF** (the same deduplicated pool used for inline images in the HTML), not one canvas per PDF page — most pages of a text-layer eGazette have no embedded image at all, so "one canvas per page" wouldn't be meaningful. Each canvas is labeled with its real source page number(s) (`"Page 12"`, or `"Pages 3, 7"` if the image recurred across pages before deduplication), not a meaningless sequential counter.

**OCR text, not discarded.** The pipeline already runs OCR on every candidate image just to decide whether to keep it (an image with no detectable text is dropped) — the extracted text and detected language are now attached to each canvas as an inline IIIF "supplementing" annotation instead of being thrown away, giving every canvas an accessible, searchable text layer with no extra file or HTTP request.

**A real IIIF Image API Level 0 service is generated per image** (`info.json` + the canonical `full/max/0/default.<format>` request), not just declared — since these are static files with no dynamic image server behind them, Level 0 (the compliance level designed exactly for pre-rendered static images) is what can genuinely work, and both required resources are written as real files so an Image API client gets a working response rather than a broken reference.

**URL construction** is strict about not leaking local filesystem details into what's a publicly-served file:
- `-o/--output-directory` only ever controls where files are *written*; the manifest's URLs come *only* from `-pu/--public-base-url` (required for correct URLs outside local development — a loud warning is logged if it's missing) and, optionally, `-sr/--server-root`.
- `output_dir`/`server_root` are validated up front to make sure neither was accidentally given a URL (e.g. `-o`/`-pu`/`-sr` swapped) — caught with a clear error before any file is written, rather than silently writing to a mangled path.
- If `-sr` is given, `--output-directory` must be located inside it, or the tool fails fast with a clear error instead of generating broken URLs.
- The manifest's `"Generated from"` metadata records only the input PDF's filename, never its full local path (which would otherwise leak server username/directory layout into a public file).

**`rights`/`provider`/`requiredStatement` (attribution) are optional and never fabricated** — this repository has no license/organization/attribution data configured anywhere, so these fields are emitted only when real values are supplied via `-rt`, `-pi`+`-pn`, and `-at` respectively.

## Project Structure

```
source/
├── Main.py               # Orchestrator: PDF → XML → classification → HTML
├── ParserTool.py         # pdfminer-based and ChromeLens/OCR-based XML extraction
├── Page.py               # Per-page layout analysis, content classification, multi-column detection/reordering
├── HTMLBuilder.py         # HTML generation and styling (HTMLBuilderChromeLens: OCR/scanned-copy path)
├── Acts.py                # "acts" document type processing
├── SebiCirculars.py       # "sebi"/"sebi_circulars" document type processing
├── Amendment.py           # Amendment detection and structuring
├── TableExtraction.py     # Table / borderless-table detection with RL-trained header/merge/continuation classifiers
├── Table.py                # Table building mixin
├── Figure.py               # Image/figure extraction
├── Manifest.py             # IIIF Presentation API 3.0 manifest generation (eGazette/SEBI), Image API Level 0 service
├── CompareLevel.py         # Section/heading level comparison
├── FontMapper.py           # Dynamic font mapping
├── NormalizeText.py        # Text normalization
├── SentenceEndDetector.py  # Legal sentence boundary detection
├── TextBox.py               # Textbox data model
└── Utils.py                 # Shared helpers

model/
└── lid.176.bin             # fastText language ID model (Git LFS)

test/
├── TestPageLayout.py        # Layout unit tests
├── TestPdfToHtmlDiff.py     # Diff-based end-to-end tests
├── test_cases.csv           # Test case configuration
├── test_pdfs/                # Sample input PDFs
└── expected_html/            # Baseline HTML outputs

cache_xml/   # Cached intermediate XML (gitignored)
cache_pdf/   # Temporary PDF storage (gitignored)
```

## Testing

```bash
# Layout unit tests
python -m unittest test.TestPageLayout

# Diff-based end-to-end tests against baseline HTML
python -m unittest test.TestPdfToHtmlDiff
```

See [`test/README_diff_test.md`](test/README_diff_test.md) for details on configuring test cases.

## License

No license file is currently present in this repository.
