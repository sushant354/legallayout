import os
import unittest
import tempfile
import shutil
import argparse
from pathlib import Path
import difflib
import logging
import csv

from legallayout.source.Main import Main


class TestPdfToHtmlDiff(unittest.TestCase):
    """
    Diff-based test that processes PDFs from a test folder and generates HTML output.
    Compares generated HTML against expected baseline files or detects changes.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test environment and locate test PDFs."""
        cls.test_dir = Path(__file__).parent
        cls.test_pdfs_dir = cls.test_dir / "test_pdfs"
        cls.expected_output_dir = cls.test_dir / "expected_html"
        cls.actual_output_dir = cls.test_dir / "actual_html"
        cls.diff_output_dir = cls.test_dir / "diff_results"

        # Create necessary directories
        cls.actual_output_dir.mkdir(exist_ok=True)
        cls.diff_output_dir.mkdir(exist_ok=True)

        # Set up logging for tests
        logging.basicConfig(level=logging.WARNING)  # Reduce noise during tests

        cls.test_cases = []
        cls.csv_file = cls.test_dir / "test_cases.csv"

        # Read test cases from CSV file
        cls._load_test_cases_from_csv()

    def setUp(self):
        """Set up for each test case."""
        # Clean up actual output directory before each test
        if self.actual_output_dir.exists():
            for html_file in self.actual_output_dir.glob("*.html"):
                html_file.unlink()

    def test_pdf_to_html_conversion(self):
        """Test PDF to HTML conversion for all PDFs defined in CSV."""
        if not self.test_cases:
            self.skipTest("No PDF test cases found in test_cases.csv")

        results = []

        for i, test_case in enumerate(self.test_cases):
            print ('TESTCASE: ',test_case)
            with self.subTest(pdf=test_case['pdf_name'], pdf_type=test_case.get('pdf_type', 'default')):
                # Process PDF with configured parameters
                success = self._process_pdf(
                    test_case,
                    pdf_type=test_case.get('pdf_type'),
                    # start_page=test_case.get('start_page'),
                    # end_page=test_case.get('end_page'),
                    has_sidenotes = test_case.get('has_sidenotes'),
                    char_margin = test_case.get('char_margin',None),
                    word_margin = test_case.get('word_margin', None),
                    line_margin = test_case.get('line_margin',None),
                    start_page  = test_case.get('start_page', None),
                    end_page = test_case.get('end_page', None),
                    # output_dir = test_case.get('output_dir',''),
                    is_amendment=test_case.get('is_amendment', False)
                )
                self.assertTrue(success, f"Failed to process PDF: {test_case['pdf_name']}")

                # Verify HTML was generated
                self.assertTrue(
                    test_case['actual_html'].exists(),
                    f"HTML output not generated for: {test_case['pdf_name']}"
                )

                # Compare with expected output or record baseline
                diff_result = self._compare_html_output(test_case)
                results.append({
                    'pdf_name': test_case['pdf_name'],
                    'pdf_type': test_case.get('pdf_type', 'default'),
                    'is_amendment': test_case.get('is_amendment', False),
                    'has_sidenotes' : test_case.get('has_sidenotes', False),
                    'status': 'PASS' if diff_result['is_match'] else 'DIFF',
                    'diff_file': diff_result.get('diff_file')
                })

        # Generate summary report
        self._generate_test_report(results)

    @classmethod
    def _load_test_cases_from_csv(cls):
        try:
            with open(cls.csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row['filename'].strip()
                    if not filename:
                        continue

                    pdf_path = cls.test_pdfs_dir / filename
                    if not pdf_path.exists():
                        print(f"Warning: PDF file not found: {pdf_path}")
                        continue

                    # Parse optional parameters
                    pdf_type = row.get('pdf_type', '').strip() or None
                    is_amendment = row.get('is_amendment', '').strip().lower() in ['true', 'yes', '1']
                    # start_page = row.get('start_page', '').strip()
                    # end_page = row.get('end_page', '').strip()

                    # start_page = int(start_page) if start_page.isdigit() else None
                    # end_page = int(end_page) if end_page.isdigit() else None
                    has_sidenotes = row.get('has_sidenotes', '').strip().lower() in ['true', 'yes', '1']
                    base_name = pdf_path.stem 
                    if pdf_type == 'acts':
                        expected_file = 'bluebell'
                    else:
                        expected_file = 'html'
                    cls.test_cases.append({
                        'pdf_path': str(pdf_path),
                        'pdf_name': base_name,
                        'filename': filename,
                        'pdf_type': pdf_type,
                        'is_amendment': is_amendment,
                        # 'start_page': start_page,
                        # 'end_page': end_page,
                        'has_sidenotes' : has_sidenotes,
                        'expected_html': cls.expected_output_dir / f"{base_name}.{expected_file}",
                        'actual_html': cls.actual_output_dir / f"{base_name}.{expected_file}"
                    })
        except Exception as e:
            print(f"Error reading CSV file {cls.csv_file}: {e}")

    @classmethod
    def _generate_config_suffix(cls, pdf_type, is_amendment, has_sidenotes):#start_page, end_page):
        """Generate a suffix based on configuration parameters."""
        suffix_parts = []
        if pdf_type:
            suffix_parts.append(f"type-{pdf_type}")
        if is_amendment:
            suffix_parts.append("amendment")
        # if start_page is not None:
        #     suffix_parts.append(f"start-{start_page}")
        # if end_page is not None:
        #     suffix_parts.append(f"end-{end_page}")
        if has_sidenotes:
            suffix_parts.append("has_sidenotes")

        return f"_{'_'.join(suffix_parts)}" if suffix_parts else ""

    def _process_pdf(self, test_case, pdf_type=None, is_amendment=False, has_sidenotes = False,
                     char_margin = None, word_margin = None, line_margin = None, 
                     start_page = None, end_page = None):
        """Process a single PDF file and generate HTML output."""
        try:
            # Create Main instance
            main = Main(
                pdfPath=test_case['pdf_path'],
                # start=start_page,
                # end=end_page,
                is_amendment_pdf=is_amendment,
                output_dir=str(self.actual_output_dir),
                pdf_type=pdf_type,
                has_side_notes = has_sidenotes
            )

            # Parse PDF
            parse_success = main.parsePDF(pdf_type, char_margin, word_margin, line_margin,\
                                          start_page, end_page)
            if not parse_success:
                return False

            # Build HTML
            main.buildHTML(start_page, end_page)

            # Clean up cache
            main.clear_cache_pdf()
            main.clear_cache()

            return True

        except Exception as e:
            logging.error(f"Error processing PDF {test_case['pdf_name']}: {e}")
            return False

    def _compare_html_output(self, test_case):
        """Compare actual HTML output with expected baseline."""
        actual_html = test_case['actual_html']
        expected_html = test_case['expected_html']

        # Read actual HTML content
        with open(actual_html, 'r', encoding='utf-8') as f:
            actual_content = f.read()

        # If expected file doesn't exist, create it as baseline
        if not expected_html.exists():
            self.expected_output_dir.mkdir(exist_ok=True)
            with open(expected_html, 'w', encoding='utf-8') as f:
                f.write(actual_content)
            return {'is_match': True, 'message': 'Created baseline file'}

        # Read expected HTML content
        with open(expected_html, 'r', encoding='utf-8') as f:
            expected_content = f.read()

        # Compare content
        if actual_content.strip() == expected_content.strip():
            return {'is_match': True}

        # Generate diff if content differs
        diff_file = self.diff_output_dir / f"{test_case['pdf_name']}_diff.html"
        diff_lines = list(difflib.unified_diff(
            expected_content.splitlines(keepends=True),
            actual_content.splitlines(keepends=True),
            fromfile=f"expected/{expected_html.name}",
            tofile=f"actual/{actual_html.name}",
            lineterm=''
        ))

        with open(diff_file, 'w', encoding='utf-8') as f:
            f.write(''.join(diff_lines))

        return {
            'is_match': False,
            'diff_file': str(diff_file),
            'message': f'Content differs - diff saved to {diff_file}'
        }

    def _generate_test_report(self, results):
        """Generate a summary test report."""
        report_file = self.diff_output_dir / "test_report.txt"

        with open(report_file, 'w') as f:
            f.write("PDF to HTML Conversion Test Report\n")
            f.write("=" * 40 + "\n\n")

            total_tests = len(results)
            passed_tests = sum(1 for r in results if r['status'] == 'PASS')

            f.write(f"Total PDFs tested: {total_tests}\n")
            f.write(f"Passed: {passed_tests}\n")
            f.write(f"With differences: {total_tests - passed_tests}\n\n")

            f.write("Detailed Results:\n")
            f.write("-" * 50 + "\n")

            for result in results:
                f.write(f"PDF: {result['pdf_name']}\n")
                f.write(f"Type: {result.get('pdf_type', 'default')}\n")
                f.write(f"Amendment: {result.get('is_amendment', False)}\n")
                f.write(f"Sidenotes: {result.get('has_sidenotes', False)}\n")
                f.write(f"Status: {result['status']}\n")
                if result.get('diff_file'):
                    f.write(f"Diff file: {result['diff_file']}\n")
                f.write("\n")

        print(f"\nTest report generated: {report_file}")

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        # Test with non-existent PDF
        with self.assertLogs(level='ERROR'):
            main = Main(
                pdfPath="non_existent.pdf",
                # start=None,
                # end=None,
                is_amendment_pdf=False,
                output_dir=str(self.actual_output_dir),
                pdf_type=None,
                has_side_notes = False
            )
            success = main.parsePDF(None, char_margin = None, word_margin = None, \
                                    line_margin = None, start_page = None, end_page = None)
            self.assertFalse(success)

    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests."""
        # Optionally clean up temporary files
        # Uncomment the following lines if you want to clean up after tests
        # if cls.actual_output_dir.exists():
        #     shutil.rmtree(cls.actual_output_dir)

def update_golden_files(actual_dir, expected_dir):
    if not actual_dir.exists():
        print(f"[ERROR] Actual output directory not found: {actual_dir}")
        return

    expected_dir.mkdir(exist_ok=True)

    copied_files = 0
    for actual_file in actual_dir.iterdir():
        if actual_file.is_file():
            target_file = expected_dir / actual_file.name
            shutil.copy2(actual_file, target_file)
            copied_files += 1
            print(f"[UPDATED] {target_file}")

    print(f"\n✅ Updated {copied_files} golden file(s) in {expected_dir}")


if __name__ == "__main__":
    # Create test directory structure if it doesn't exist
    test_dir = Path(__file__).parent
    test_pdfs_dir = test_dir / "test_pdfs"
    actual_html_dir = test_dir / "actual_html"
    expected_html_dir = test_dir / "expected_html"
    csv_file = test_dir / "test_cases.csv"

    if not test_pdfs_dir.exists():
        test_pdfs_dir.mkdir()
        print(f"Created test PDFs directory: {test_pdfs_dir}")
        print("Please add PDF files to this directory for testing.")

    if not actual_html_dir.exists():
        actual_html_dir.mkdir()
        print(f"Created actual HTML output directory: {actual_html_dir}")
    
    if not expected_html_dir.exists():
        expected_html_dir.mkdir()
        print(f"Created expected HTML directory: {expected_html_dir}")

    if not csv_file.exists():
        print(f"CSV file not found. A sample will be created at: {csv_file}")

    parser = argparse.ArgumentParser(description="Run HTML diff tests or update golden files.")
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="If set, overwrites expected_html files with actual_html outputs."
    )
    args, remaining = parser.parse_known_args()

    # If update flag is passed → update golden files directly
    if args.update_golden:
        update_golden_files(actual_html_dir, expected_html_dir)
    else:
        unittest.main()
