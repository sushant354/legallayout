import os
import sys
import unittest
import tempfile
import shutil
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import difflib
import fnmatch
import logging
import csv

# so that this file works when run as a script too, and not just through
# 'python -m unittest' from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from source.Main import Main


def process_case(job):
    """Convert a single PDF, in a worker process of its own.

    Nothing is shared with the parent process here, so the output filename that
    Main settles on (it depends on the page count, which is only known once the
    PDF has been parsed) is returned rather than written back into the test case.
    """
    source_path = Path(job['pdf_path'])
    output_dir = Path(job['output_dir'])
    pdf_path_for_main = job['pdf_path']

    renamed_copy = None
    if job['pdf_name'] != source_path.stem:
        renamed_copy = output_dir / f"{job['pdf_name']}{source_path.suffix}"
        shutil.copy2(source_path, renamed_copy)
        pdf_path_for_main = str(renamed_copy)

    result = {'pdf_name': job['pdf_name'], 'success': False,
              'filename': None, 'error': None}

    try:
        main = Main(
            pdfPath=pdf_path_for_main,
            is_amendment_pdf=job['is_amendment'],
            output_dir=str(output_dir),
            pdf_type=job['pdf_type'],
            has_side_notes=job['has_sidenotes'],
            has_doc_end=job['has_doc_end'],
            is_footnote_continuation=job['is_footnote_continuation'],
            min_img_pixels=job['min_img_pixels'],
            ocr_language=job['ocr_language'],
            is_scanned_copy=job['scanned_copy'],
            table_extract=job['table_extract'],
            figure_text=job['figure_text'],
            public_base_url=job['public_base_url'],
            server_root=job['server_root'],
            rights=job['rights'],
            provider_id=job['provider_id'],
            provider_name=job['provider_name'],
            attribution=job['attribution']
        )

        # Parse PDF
        parse_success = main.parsePDF(job['pdf_type'], job['char_margin'],
                                      job['word_margin'], job['line_margin'],
                                      job['start_page'], job['end_page'])
        if not parse_success:
            result['error'] = 'parsePDF() reported a failure'
            return result

        # Build HTML
        main.buildHTML(job['start_page'], job['end_page'])

        result['filename'] = TestPdfToHtmlDiff._compute_output_filename(
            job['pdf_name'], job['start_page'], job['end_page'],
            main.total_pgs, job['suffix']
        )

        # Clean up cache
        main.clear_cache_pdf()
        main.clear_xml_cache()

        result['success'] = True
        return result

    except Exception as e:
        logging.error(f"Error processing PDF {job['pdf_name']}: {e}")
        result['error'] = str(e)
        return result

    finally:
        if renamed_copy and renamed_copy.exists():
            renamed_copy.unlink()


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
            selected = self.get_selected_cases()

            if selected:
                self.skipTest(
                    f"No test case in test_cases.csv matched: {', '.join(selected)}"
                )

            self.skipTest("No PDF test cases found in test_cases.csv")

        results = []

        # every PDF is converted first, several at a time, and only then compared
        case_results = self._process_all_pdfs()

        for test_case, case_result in zip(self.test_cases, case_results):
            print ('TESTCASE: ',test_case)
            with self.subTest(pdf=test_case['pdf_name'], pdf_type=test_case.get('pdf_type', 'default')):
                success = self._apply_case_result(test_case, case_result)
                self.assertTrue(
                    success,
                    f"Failed to process PDF: {test_case['pdf_name']} "
                    f"({case_result.get('error')})"
                )

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
                    'scanned_copy': test_case.get('scanned_copy', False),
                    'table_extract': test_case.get('table_extract', False),
                    'figure_text': test_case.get('figure_text', False),
                    'status': 'PASS' if diff_result['is_match'] else 'DIFF',
                    'diff_file': diff_result.get('diff_file')
                })

        # Generate summary report
        self._generate_test_report(results)

    @staticmethod
    def _parse_bool(value):
        return value.strip().lower() in ['true', 'yes', '1']

    @staticmethod
    def get_selected_cases():
        """The cases asked for on DIFF_TEST_CASES, empty when the whole csv runs."""
        configured = os.environ.get('DIFF_TEST_CASES', '').strip()

        return [name.strip() for name in configured.split(',') if name.strip()]

    @classmethod
    def is_case_selected(cls, selected, filename, pdf_name):
        """Whether a csv row was asked for, by file name, stem or case name.

        'sebi1.pdf', 'sebi1' and 'sebi*' all pick the sebi1 case, and the name a
        case is reported under ('changeofnames_scanned') works too, which is the
        only way to pick one of the two cases a single pdf can produce.
        """
        if not selected:
            return True

        names = {filename.lower(), Path(filename).stem.lower(), pdf_name.lower()}

        for wanted in selected:
            wanted = wanted.lower()

            if any(fnmatch.fnmatchcase(name, wanted) for name in names):
                return True

        return False

    @staticmethod
    def get_worker_count(no_of_jobs):
        """Number of PDFs to convert at the same time."""
        configured = os.environ.get('DIFF_TEST_WORKERS', '').strip()

        if configured.isdigit() and int(configured) > 0:
            workers = int(configured)
        else:
            # the OCR paths load a model of their own in every process, so this
            # stays well below the core count to keep the memory use sane
            workers = min(4, os.cpu_count() or 1)

        return max(1, min(workers, no_of_jobs))

    def _build_job(self, test_case, params):
        """Everything a worker process needs to convert one PDF, as plain data."""
        job = {
            'pdf_path': test_case['pdf_path'],
            'pdf_name': test_case['pdf_name'],
            'output_dir': str(self.actual_output_dir),
            'suffix': test_case['actual_html'].suffix,
            'ocr_language': params.get('ocr_language') or 'en',
            'min_img_pixels': params.get('min_img_pixels') or 0
        }

        for key in ('pdf_type', 'char_margin', 'word_margin', 'line_margin',
                    'start_page', 'end_page', 'server_root', 'public_base_url',
                    'rights', 'provider_id', 'provider_name', 'attribution'):
            job[key] = params.get(key)

        for key in ('is_amendment', 'has_sidenotes', 'scanned_copy', 'table_extract',
                    'figure_text', 'has_doc_end', 'is_footnote_continuation'):
            job[key] = bool(params.get(key))

        return job

    def _apply_case_result(self, test_case, case_result):
        """Point the test case at the file the worker actually wrote."""
        if case_result.get('filename'):
            test_case['actual_html'] = self.actual_output_dir / case_result['filename']
            test_case['expected_html'] = self.expected_output_dir / case_result['filename']

        return case_result['success']

    def _process_all_pdfs(self):
        """Convert every PDF, several at a time, keeping the results in case order."""
        jobs = [self._build_job(test_case, test_case) for test_case in self.test_cases]

        workers = self.get_worker_count(len(jobs))

        if workers == 1:
            return [process_case(job) for job in jobs]

        print(f"Converting {len(jobs)} PDF(s) using {workers} worker processes...")

        results = [None] * len(jobs)
        done = 0

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_case, job): idx
                for idx, job in enumerate(jobs)
            }

            for future in as_completed(futures):
                idx = futures[future]

                try:
                    results[idx] = future.result()
                except Exception as e:
                    # the worker died outright, e.g. it was killed for using too
                    # much memory - report it as a failure of that one PDF
                    results[idx] = {
                        'pdf_name': jobs[idx]['pdf_name'], 'success': False,
                        'filename': None, 'error': f"worker process failed: {e}"
                    }

                done += 1
                print(f"  [{done}/{len(jobs)}] {results[idx]['pdf_name']}: "
                      f"{'OK' if results[idx]['success'] else 'FAILED'}")

        return results

    @staticmethod
    def _compute_output_filename(base_stem, start_page, end_page, total_pgs, suffix):
        if start_page or end_page:
            if start_page is None:
                start_page = 1
            elif end_page is None:
                end_page = total_pgs - 1 + int(start_page)
            return f"{base_stem}pg:{start_page}_pg:{end_page}{suffix}"
        return f"{base_stem}{suffix}"

    @classmethod
    def _load_test_cases_from_csv(cls):
        selected = cls.get_selected_cases()
        skipped = []

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
                    is_amendment = cls._parse_bool(row.get('is_amendment', ''))
                    start_page = row.get('start_page', '').strip()
                    end_page = row.get('end_page', '').strip()

                    start_page = int(start_page) if start_page.isdigit() else None
                    end_page = int(end_page) if end_page.isdigit() else None
                    has_sidenotes = cls._parse_bool(row.get('has_sidenotes', ''))
                    scanned_copy = cls._parse_bool(row.get('scanned_copy', ''))
                    table_extract = cls._parse_bool(row.get('table_extract', ''))
                    figure_text = cls._parse_bool(row.get('figure_text', ''))
                    has_doc_end = cls._parse_bool(row.get('has_doc_end', ''))
                    is_footnote_continuation = cls._parse_bool(row.get('is_footnote_continuation', ''))
                    ocr_language = row.get('ocr_language', '').strip() or 'en'
                    min_img_pixels_raw = row.get('min_img_pixels', '').strip()
                    min_img_pixels = int(min_img_pixels_raw) if min_img_pixels_raw.isdigit() else 0
                    server_root_raw = row.get('server_root', '').strip()
                    server_root = str(Path(server_root_raw).expanduser()) if server_root_raw else None
                    public_base_url = row.get('public_base_url', '').strip() or None
                    rights = row.get('rights', '').strip() or None
                    provider_id = row.get('provider_id', '').strip() or None
                    provider_name = row.get('provider_name', '').strip() or None
                    attribution = row.get('attribution', '').strip() or None

                    base_name = pdf_path.stem
                    if scanned_copy:
                        base_name += '_scanned'

                    # base_name is only known here, so the row is filtered now
                    # rather than as soon as its filename was read
                    if not cls.is_case_selected(selected, filename, base_name):
                        skipped.append(base_name)
                        continue

                    if pdf_type in {'acts', 'sebi_circulars'}:
                        expected_file = 'bluebell'
                    else:
                        expected_file = 'html'
                    cls.test_cases.append({
                        'pdf_path': str(pdf_path),
                        'pdf_name': base_name,
                        'filename': filename,
                        'pdf_type': pdf_type,
                        'is_amendment': is_amendment,
                        'start_page': start_page,
                        'end_page': end_page,
                        'has_sidenotes' : has_sidenotes,
                        'scanned_copy': scanned_copy,
                        'table_extract': table_extract,
                        'figure_text': figure_text,
                        'has_doc_end': has_doc_end,
                        'is_footnote_continuation': is_footnote_continuation,
                        'ocr_language': ocr_language,
                        'min_img_pixels': min_img_pixels,
                        'server_root': server_root,
                        'public_base_url': public_base_url,
                        'rights': rights,
                        'provider_id': provider_id,
                        'provider_name': provider_name,
                        'attribution': attribution,
                        'expected_html': cls.expected_output_dir / f"{base_name}.{expected_file}",
                        'actual_html': cls.actual_output_dir / f"{base_name}.{expected_file}"
                    })
        except Exception as e:
            print(f"Error reading CSV file {cls.csv_file}: {e}")

        if selected:
            print(f"Selected {len(cls.test_cases)} of {len(cls.test_cases) + len(skipped)} "
                  f"case(s) in {cls.csv_file.name}: "
                  f"{', '.join(tc['pdf_name'] for tc in cls.test_cases) or 'none'}")

            if not cls.test_cases and skipped:
                print(f"Available cases are: {', '.join(skipped)}")

    def _process_pdf(self, test_case, pdf_type=None, is_amendment=False, has_sidenotes = False,
                     char_margin = None, word_margin = None, line_margin = None,
                     start_page = None, end_page = None, scanned_copy = False, table_extract = False,
                     figure_text = False,
                     has_doc_end = False, is_footnote_continuation = False, ocr_language = 'en',
                     min_img_pixels = 0, server_root = None, public_base_url = None,
                     rights = None, provider_id = None, provider_name = None, attribution = None):
        """Process a single PDF file and generate HTML output, in this process."""
        job = self._build_job(test_case, {
            'pdf_type': pdf_type, 'is_amendment': is_amendment,
            'has_sidenotes': has_sidenotes, 'char_margin': char_margin,
            'word_margin': word_margin, 'line_margin': line_margin,
            'start_page': start_page, 'end_page': end_page,
            'scanned_copy': scanned_copy, 'table_extract': table_extract,
            'figure_text': figure_text, 'has_doc_end': has_doc_end,
            'is_footnote_continuation': is_footnote_continuation,
            'ocr_language': ocr_language, 'min_img_pixels': min_img_pixels,
            'server_root': server_root, 'public_base_url': public_base_url,
            'rights': rights, 'provider_id': provider_id,
            'provider_name': provider_name, 'attribution': attribution
        })

        return self._apply_case_result(test_case, process_case(job))

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
                f.write(f"Scanned copy: {result.get('scanned_copy', False)}\n")
                f.write(f"Table extract: {result.get('table_extract', False)}\n")
                f.write(f"Figure text: {result.get('figure_text', False)}\n")
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
                has_side_notes = False,
                has_doc_end = False,
                is_footnote_continuation = False,
                min_img_pixels = 0,
                ocr_language = 'en',
                is_scanned_copy = False,
                table_extract = False
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
        target_file = expected_dir / actual_file.name
        if actual_file.is_file():
            shutil.copy2(actual_file, target_file)
            copied_files += 1
            print(f"[UPDATED] {target_file}")
        elif actual_file.is_dir():
            shutil.copytree(actual_file, target_file, dirs_exist_ok=True)
            copied_files += 1
            print(f"[UPDATED] {target_file}/")

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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="How many PDFs to convert at the same time (1 disables parallelism). "
             "Defaults to the DIFF_TEST_WORKERS env var, then to 4."
    )
    parser.add_argument(
        "--cases",
        nargs='+',
        default=None,
        metavar="CASE",
        help="Run only these cases from test_cases.csv instead of all of them. "
             "A case can be given as a file name (sebi1.pdf), a stem (sebi1), the "
             "name it is reported under (changeofnames_scanned) or a glob (sebi*). "
             "Defaults to the DIFF_TEST_CASES env var."
    )
    args, remaining = parser.parse_known_args()

    if args.workers:
        # picked up by TestPdfToHtmlDiff.get_worker_count()
        os.environ['DIFF_TEST_WORKERS'] = str(args.workers)

    if args.cases:
        # picked up by TestPdfToHtmlDiff.get_selected_cases()
        os.environ['DIFF_TEST_CASES'] = ','.join(args.cases)

    # If update flag is passed → update golden files directly
    if args.update_golden:
        update_golden_files(actual_html_dir, expected_html_dir)
    else:
        # unittest reads sys.argv itself, so keep only what it understands
        sys.argv = [sys.argv[0]] + remaining
        unittest.main()
