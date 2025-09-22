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

### 3. Run the Diff Test
```bash
# Run from project root directory
python -m unittest test.TestPdfToHtmlDiff

# Or run the test file directly
cd test
python TestPdfToHtmlDiff.py
```

## What the Test Does

1. **Reads CSV Configuration**: Loads test cases from `test_cases.csv` with specific parameters for each PDF

2. **Processes PDFs**: Uses `Main.parsePDF()` and `Main.buildHTML()` to convert each PDF to HTML with the configured parameters

3. **Creates Baselines**: On first run, generates baseline HTML files in `expected_html/`

4. **Compares Output**: On subsequent runs, compares new output against baselines

5. **Generates Diffs**: Creates diff files in `diff_results/` when output changes

6. **Reports Results**: Generates a test report showing which PDFs passed/failed

## Test Features

- **CSV Configuration**: Configure test parameters for each PDF individually
- **Parameter Support**: Test with different PDF types, amendment flags, and page ranges
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
# Update baseline for specific PDF
rm test/expected_html/your_file.html
python -m unittest test.TestPdfToHtmlDiff

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