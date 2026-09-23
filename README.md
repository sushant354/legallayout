# legallayout

A Python tool that parses legal PDF documents (acts, SEBI circulars, eGazette notifications, amendments) and converts them into structured, semantic HTML — along with IIIF Presentation API 3.0 manifests for image-based eGazette/SEBI documents.

It extracts text and layout information from PDFs, classifies content into headers, footers, sections, paragraphs, side notes, tables, and figures, and applies document-type-specific rules to reconstruct the structure of the original legal document.

## Features

- PDF → structured HTML conversion using coordinate-based layout analysis
- Cross-page header/footer detection
- Multi-column page layout detection and reading-order reconstruction
- Document-type-specific processing for `acts`, `sebi`/`sebi_circulars`, and `egazette`
- Amendment detection and structuring
- Table extraction — bordered tables via `camelot-py`, plus borderless-table detection with cross-page continuation (`-te/--table-extract`)
- OCR-based extraction: Chrome-Lens or Tesseract for scanned copies, Tesseract or PaddleOCR for figure-text, covering English plus 15 Indian regional languages
- IIIF Presentation API 3.0 manifest generation for `egazette`/`sebi` image documents
- XML caching of intermediate pdfminer output for faster iteration

## Requirements

- Python 3
- [Git LFS](https://git-lfs.com/) (`model/lid.176.bin` and `model/eng_hin_fonts.pkl` are tracked via LFS)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) system binary, with language data for whichever `-ol/--ocr-language` codes you plan to use (e.g. `apt install tesseract-ocr tesseract-ocr-all`)
- `paddlepaddle`/`paddleocr` (in `requirements.txt`) only if using `-oe paddleocr`

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
| `-s, --sidenotes` | PDF has sidenotes or inline titles for acts, amendments |
| `-a, --amendments` | PDF contains amendments |
| `-de, --doc-end` | PDF has a document-end symbol (`---`) |
| `-fnc, --footnote-continuation` | Footnotes continue across pages |
| `-sc, --scanned-copy` | PDF is a scanned copy (routes through OCR page-text parsing — see `-op`) |
| `-ftx, --figure-text` | Enable OCR-based per-image figure-text extraction; images without confident text are dropped. Always on for `acts`/`sebi_circulars` |
| `-oe, --ocr-engine-image-text` | OCR engine for figure-text extraction (`-ftx`): `tesseract` (default) or `paddleocr` |
| `-op, --ocr-engine-pdf-parser` | OCR engine for the scanned-copy page-text path: `chromelens` or `tesseract`. Defaults to `chromelens` for `egazette`/`acts`/`sebi_circulars`, `tesseract` otherwise |
| `-ol, --ocr-language` | OCR language code (default `eng`): `eng`, `asm`, `ben`, `guj`, `hin`, `kan`, `mal`, `mar`, `nep`, `ori`, `pan`, `san`, `snd`, `tam`, `tel`, `urd`, or `+`-joined for multi-language tesseract OCR (e.g. `hin+eng`). PaddleOCR supports only a 7-language subset and degrades gracefully otherwise |
| `-te, --table-extract` | Enable borderless-table extraction |
| `-mip, --min-img-pixels` | Minimum pixel area threshold for image filtering |
| `-pu, --public-base-url` | Public URL the output directory is served from; base for every IIIF manifest URI (`egazette`/`sebi` only). Falls back to `PUBLIC_BASE_URL` env var, then `http://localhost:8000` |
| `-sr, --server-root` | Local filesystem directory acting as the web server's document root (`egazette`/`sebi` only); `--output-directory` must be inside it |
| `-rt, --rights` | IIIF manifest `rights` URI (`egazette`/`sebi` only) |
| `-pi, --provider-id` / `-pn, --provider-name` | URI + name of the presenting organization for the IIIF `provider` field (`egazette`/`sebi` only); both required together |
| `-at, --attribution` | Attribution text for the IIIF manifest's `requiredStatement` (`egazette`/`sebi` only) |
| `-fc, --font-conv` | `FONT=CONVERTER` mapping for a legacy indic font whose name doesn't identify its encoding (e.g. `-fc TT572t00=chanakya`). Repeatable, comma-separable |
| `-fm, --font-model` | Path to the font classifier model for fonts that neither their name nor `-fc` places (default `model/eng_hin_fonts.pkl`) |
| `-fl, --font-lang` | Which shipped model (`-fm`) to use, by script: `hin` (default) or `kan` |
| `-nfd, --no-font-detect` | Disable the font classifier; place fonts by name and `-fc` only |
| `-fn, --font-names` | Wrap each run of text in `<span data-font="FONT">`. HTML output only — ignored for `acts`/`sebi_circulars` |
| `-lm, --line-margin` | pdfminer line margin threshold |
| `-cm, --char-margin` | pdfminer char margin threshold |
| `-wm, --word-margin` | pdfminer word margin threshold |
| `-l, --loglevel` | Log level: `error`\|`warning`\|`info`\|`debug` (default: `info`) |
| `-g, --logfile` | Log file path |
| `-x, --keep-xml` | Keep intermediate XML in `cache_xml/` instead of deleting it |

## IIIF manifest generation

For `egazette`/`sebi` documents with extractable images, an IIIF Presentation API 3.0 manifest (`manifest/<pdfname>/manifest.json`) is written alongside the HTML, with a link to it added in the HTML. Use `-pu/--public-base-url` to get correct URLs outside local development.

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
├── TableExtraction.py     # Table / borderless-table detection
├── Table.py                # Table building mixin
├── Figure.py               # Image/figure extraction
├── Manifest.py             # IIIF Presentation API 3.0 manifest generation (eGazette/SEBI)
├── CompareLevel.py         # Section/heading level comparison
├── FontMapper.py           # Dynamic font mapping
├── NormalizeText.py        # Text normalization
├── SentenceEndDetector.py  # Legal sentence boundary detection
├── TextBox.py               # Textbox data model
└── Utils.py                 # Shared helpers

model/
├── lid.176.bin             # fastText language ID model (Git LFS)
└── eng_hin_fonts.pkl       # font classifier used by -fm/--font-model (Git LFS)

test/
├── TestPdfToHtmlDiff.py     # Diff-based end-to-end tests
├── TestCleanup.py           # Cache/interrupt cleanup mechanism tests (no PDF parsing)
├── test_cases.csv           # Test case configuration
├── test_pdfs/                # Sample input PDFs
└── expected_html/            # Baseline HTML outputs

cache_xml/   # Cached intermediate XML (gitignored)
cache_pdf/   # Temporary PDF storage (gitignored)
```

## Testing

```bash
# Diff-based end-to-end tests against baseline HTML
python -m unittest test.TestPdfToHtmlDiff

# Cache/interrupt cleanup mechanism (fast, no PDF parsing)
python -m unittest test.TestCleanup
```

See [`test/README_diff_test.md`](test/README_diff_test.md) for details on configuring test cases.

## Acknowledgments

This work is sponsored by [Public Resource](https://public.resource.org/), a non-profit dedicated to making government records publicly available.

The HTML tool here uses font converters from the indic2unicode project. Following people have helped us to validate the conversion of various fonts embedded in PDFs to HTML:

| Contributor | Email | Language |
|---|---|---|
| Sushant Sinha | sushant@indiankanoon.com | Hindi |
| Barath Kumar | barath@indiankanoon.com | Tamil |
| Nanditha Harimohan | nanditha@indiankanoon.com | Malayalam |
| Yashwanth A | yashwanth@indiankanoon.com | Kannada |

## License
 
GNU GPL v3
