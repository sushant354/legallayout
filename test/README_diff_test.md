# PDF to HTML Diff-Based Test

This directory contains a comprehensive diff-based test suite for the PDF parser and converter.

## Directory Structure

```
test/
├── TestPdfToHtmlDiff.py      # Main test script
├── test_cases.csv            # Test configuration file
├── test_pdfs/                # Place your test PDF files here
├── expected_html/            # Baseline HTML outputs (auto-generated)
├── actual_html/              # Generated HTML outputs during tests
├── diff_results/             # Diff files when outputs don't match
└── README_diff_test.md       # This file
```

## How to Use

### 1. Add Test PDFs
Place your PDF test files in the `test_pdfs/` directory:
```bash
cp your_test_file.pdf test/test_pdfs/
```

### 2. Configure Test Cases
Edit the `test_cases.csv` file to define your test cases with specific parameters:

#### CSV Format
```csv
filename,pdf_type,is_amendment,start_page,end_page
sample1.pdf,acts,false,,
sample2.pdf,sebi,true,5,10
sample3.pdf,,false,,
legal_doc.pdf,acts,true,1,20
regulations.pdf,sebi,false,3,
```

#### CSV Columns:
- **filename**: Name of the PDF file in `test_pdfs/` directory (required)
- **pdf_type**: Processing type - `acts`, `sebi`, or empty for default (optional)
- **is_amendment**: Set to `true` if PDF contains amendments, `false` otherwise (optional)
- **start_page**: Starting page number for processing (optional)
- **end_page**: Ending page number for processing (optional)
- **server_root**: Web server document root for IIIF manifest URLs, i.e. `-sr`
  (optional). **Keep it relative**: it is resolved against the repository, not
  the current directory, so `.` means the repo is the server root and the
  manifest URLs carry `test/actual_html/...` on every machine. An absolute path
  is taken as given and only works where the repo really sits under it, which
  makes the case fail everywhere else (`output_dir is not located within
  server_root`)

### 3. Run the Diff Test
```bash
# Run from project root directory
python -m unittest test.TestPdfToHtmlDiff

# Or run the test file directly
python test/TestPdfToHtmlDiff.py
```

### 4. Run Only Some of the Cases

The full CSV takes a long time, so a single case can be picked out with
`--cases` (or the `DIFF_TEST_CASES` environment variable, which is the way to
do it when running through `python -m unittest`):

```bash
# just one case
python test/TestPdfToHtmlDiff.py --cases sebi1
DIFF_TEST_CASES=sebi1 python -m unittest test.TestPdfToHtmlDiff

# several of them
python test/TestPdfToHtmlDiff.py --cases sebi1 tnact2 act1
DIFF_TEST_CASES=sebi1,tnact2,act1 python -m unittest test.TestPdfToHtmlDiff

# everything whose name matches a glob
python test/TestPdfToHtmlDiff.py --cases 'sebicirculars*'
DIFF_TEST_CASES='sebicirculars*' python -m unittest test.TestPdfToHtmlDiff
```

A case can be named in any of these ways, and the match is case insensitive:

| Form | Example | Picks |
|------|---------|-------|
| File name | `tnact.pdf` | that csv row |
| File stem | `sebi1` | `sebi1` **and** `sebi1_scanned` |
| Reported name | `changeofnames_scanned` | only the scanned row |
| Glob | `sebi*`, `*table*` | every row that matches |

A stem picks up every csv row using that PDF, so the two rows a single PDF can
have (a normal one and a `scanned_copy` one) are both selected by `sebi1`. Use
the reported name, the one the test report and the output file use, to single
one of them out.

Nothing selected means the whole CSV runs, so the default is unchanged. If a
name matches nothing, the run prints the list of available case names instead
of quietly doing nothing.

### 5. Control How Many PDFs Convert at Once

PDFs are converted in parallel worker processes, 4 at a time by default. Use
`--workers` (or the `DIFF_TEST_WORKERS` environment variable) to change that:

```bash
python test/TestPdfToHtmlDiff.py --workers 8
DIFF_TEST_WORKERS=8 python -m unittest test.TestPdfToHtmlDiff

# converting one at a time, e.g. to read the logs or to debug a crash
python test/TestPdfToHtmlDiff.py --workers 1
```

The default is capped at 4 rather than at the core count because the OCR paths
load a model of their own in every worker process, which costs memory. Raise it
if the machine has the RAM for it, and lower it to 1 when the interleaved log
output of several PDFs at once gets in the way. The output is identical either
way; only the wall clock time changes.

`--cases` and `--workers` combine, which is the quickest way to iterate on one
document:

```bash
python test/TestPdfToHtmlDiff.py --cases sebi1 --workers 1
```

## What the Test Does

1. **Reads CSV Configuration**: Loads test cases from `test_cases.csv` with specific parameters for each PDF

2. **Processes PDFs**: Uses `Main.parsePDF()` and `Main.buildHTML()` to convert each PDF to HTML with the configured parameters. Every PDF is converted first, several at a time in worker processes of their own, and only then are the outputs compared

3. **Creates Baselines**: On first run, generates baseline HTML files in `expected_html/`

4. **Compares Output**: On subsequent runs, compares new output against baselines

5. **Generates Diffs**: Creates diff files in `diff_results/` when output changes

6. **Reports Results**: Generates a test report showing which PDFs passed/failed

## Test Features

- **CSV Configuration**: Configure test parameters for each PDF individually
- **Parameter Support**: Test with different PDF types, amendment flags, and page ranges
- **Case Selection**: Run a single case, a few of them, or a glob, instead of the whole CSV (`--cases`)
- **Parallel Conversion**: Convert several PDFs at once, or one at a time while debugging (`--workers`)
- **Baseline Management**: Auto-creates baseline files on first run
- **Diff Generation**: Creates unified diff files for changed outputs
- **Flexible Naming**: Output files include configuration parameters for easy identification
- **Error Handling**: Tests edge cases like missing files
- **Comprehensive Reporting**: Detailed test reports with pass/fail status and configuration details

## Interpreting Results

### PASS
- HTML output matches the expected baseline exactly
- No changes in the conversion logic

### DIFF
- HTML output differs from baseline
- Check the diff file in `diff_results/` to see what changed
- May indicate:
  - Bug fixes that improved output
  - Regressions that broke functionality
  - Intentional changes that require updating baselines

## Updating Baselines

When you make intentional changes to the conversion logic:

1. Review the diff files to ensure changes are correct
2. Delete the corresponding file(s) in `expected_html/`
3. Re-run the test to generate new baselines

```bash
# Update baseline for specific PDF, re-running just that case
rm test/expected_html/your_file.html
python test/TestPdfToHtmlDiff.py --cases your_file

# Update all baselines
rm test/expected_html/*.html
python -m unittest test.TestPdfToHtmlDiff
```

## Example Workflow

```bash
# 1. Add test PDFs
cp sample1.pdf sample2.pdf test/test_pdfs/

# 2. Configure test cases in CSV
echo "filename,pdf_type,is_amendment,start_page,end_page" > test/test_cases.csv
echo "sample1.pdf,acts,false,," >> test/test_cases.csv
echo "sample2.pdf,sebi,true,5,10" >> test/test_cases.csv

# 3. Run test (creates baselines on first run)
python -m unittest test.TestPdfToHtmlDiff

# 4. Make changes to conversion code
# Edit source/Main.py, source/Page.py, etc.

# 5. Run test again to detect changes
python -m unittest test.TestPdfToHtmlDiff

# 6. Review diff results
cat test/diff_results/sample1_type-acts_diff.html

# 7. Update baselines if changes are correct
rm test/expected_html/sample1_type-acts.html
python -m unittest test.TestPdfToHtmlDiff
```

## CI/CD Integration

This test is designed for continuous integration:

```bash
# In your CI pipeline
python -m unittest test.TestPdfToHtmlDiff
```

The test will fail if any PDF output changes, helping catch regressions early.