import os
import sys
import unittest
import tempfile
import shutil
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import difflib
import logging
import csv

from source.Main import Main


def compute_output_filename(base_stem, start_page, end_page, total_pgs, suffix):
    if start_page or end_page:
        if start_page is None:
            start_page = 1
        elif end_page is None:
            end_page = total_pgs - 1 + int(start_page)
        return f"{base_stem}pg:{start_page}_pg:{end_page}{suffix}"
    return f"{base_stem}{suffix}"


def compare_html_output(test_case, expected_output_dir, diff_output_dir):
    actual_html = test_case['actual_html']
    expected_html = test_case['expected_html']

    with open(actual_html, 'r', encoding='utf-8') as f:
        actual_content = f.read()

    if not expected_html.exists():
        expected_output_dir.mkdir(exist_ok=True)
        with open(expected_html, 'w', encoding='utf-8') as f:
            f.write(actual_content)
        return {'is_match': True, 'message': 'Created baseline file'}

    with open(expected_html, 'r', encoding='utf-8') as f:
        expected_content = f.read()

    if actual_content.strip() == expected_content.strip():
        return {'is_match': True}

    diff_file = diff_output_dir / f"{test_case['pdf_name']}_diff.html"
    diff_lines = list(difflib.unified_diff(
        expected_content.splitlines(keepends=True),
        actual_content.splitlines(keepends=True),
        fromfile=f"expected/{expected_html.name}",
        tofile=f"actual/{actual_html.name}"
    ))

    with open(diff_file, 'w', encoding='utf-8') as f:
        f.write(''.join(diff_lines))

    return {
        'is_match': False,
        'diff_file': str(diff_file),
        'message': f'Content differs - diff saved to {diff_file}'
    }


def process_pdf(test_case, actual_output_dir):
    test_case = dict(test_case)
    source_path = Path(test_case['pdf_path'])
    pdf_path_for_main = test_case['pdf_path']
    renamed_copy = None
    if test_case['pdf_name'] != source_path.stem:
        renamed_copy = actual_output_dir / f"{test_case['pdf_name']}{source_path.suffix}"
        shutil.copy2(source_path, renamed_copy)
        pdf_path_for_main = str(renamed_copy)

    start_page = test_case.get('start_page')
    end_page = test_case.get('end_page')

    main = None
    try:
        main = Main(
            pdfPath=pdf_path_for_main,
            is_amendment_pdf=test_case.get('is_amendment', False),
            output_dir=str(actual_output_dir),
            pdf_type=test_case.get('pdf_type'),
            has_side_notes=test_case.get('has_sidenotes', False),
            has_doc_end=test_case.get('has_doc_end', False),
            is_footnote_continuation=test_case.get('is_footnote_continuation', False),
            min_img_pixels=test_case.get('min_img_pixels', 0),
            ocr_language=test_case.get('ocr_language', 'eng'),
            ocr_engine=test_case.get('ocr_engine', 'tesseract'),
            is_scanned_copy=test_case.get('scanned_copy', False),
            table_extract=test_case.get('table_extract', False),
            figure_text=test_case.get('figure_text', False),
            public_base_url=test_case.get('public_base_url'),
            server_root=test_case.get('server_root'),
            rights=test_case.get('rights'),
            provider_id=test_case.get('provider_id'),
            provider_name=test_case.get('provider_name'),
            attribution=test_case.get('attribution')
        )

        parse_success = main.parsePDF(
            test_case.get('pdf_type'),
            test_case.get('char_margin'),
            test_case.get('word_margin'),
            test_case.get('line_margin'),
            start_page,
            end_page
        )
        if not parse_success:
            return False, test_case

        main.buildHTML(start_page, end_page)

        suffix = test_case['actual_html'].suffix
        filename = compute_output_filename(
            test_case['pdf_name'], start_page, end_page, main.total_pgs, suffix
        )
        test_case['actual_html'] = actual_output_dir / filename
        test_case['expected_html'] = test_case['expected_html'].parent / filename

        return True, test_case

    except Exception as e:
        logging.error(f"Error processing PDF {test_case['pdf_name']}: {e}")
        return False, test_case

    finally:
        if main is not None:
            main.clear_cache_pdf()
            main.clear_xml_cache()
            main.clear_ocr_engines()
        if renamed_copy and renamed_copy.exists():
            renamed_copy.unlink()


def run_test_case(test_case, actual_output_dir, expected_output_dir, diff_output_dir):
    result = {
        'pdf_name': test_case['pdf_name'],
        'pdf_type': test_case.get('pdf_type', 'default'),
        'is_amendment': test_case.get('is_amendment', False),
        'has_sidenotes': test_case.get('has_sidenotes', False),
        'scanned_copy': test_case.get('scanned_copy', False),
        'table_extract': test_case.get('table_extract', False),
        'figure_text': test_case.get('figure_text', False),
    }

    success, test_case = process_pdf(test_case, actual_output_dir)
    if not success:
        result['status'] = 'ERROR'
        result['message'] = f"Failed to process PDF: {test_case['pdf_name']}"
        return result

    if not test_case['actual_html'].exists():
        result['status'] = 'ERROR'
        result['message'] = f"HTML output not generated for: {test_case['pdf_name']}"
        return result

    diff_result = compare_html_output(test_case, expected_output_dir, diff_output_dir)
    result['status'] = 'PASS' if diff_result['is_match'] else 'DIFF'
    result['diff_file'] = diff_result.get('diff_file')
    return result


def generate_test_report(results, diff_output_dir):
    report_file = diff_output_dir / "test_report.txt"

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
            if result.get('message') and result['status'] == 'ERROR':
                f.write(f"Message: {result['message']}\n")
            f.write("\n")

    print(f"\nTest report generated: {report_file}")


class TestPdfToHtmlDiff(unittest.TestCase):
    """
    Diff-based test that processes PDFs from a test folder and generates HTML output.
    Compares generated HTML against expected baseline files or detects changes.
    """

    # Set by __main__ (before setUpClass runs) to restrict which CSV(s) get loaded,
    # e.g. ["test_judgment_cases.csv"]. None/empty means "load every registered CSV".
    selected_csvs = None

    @classmethod
    def setUpClass(cls):
        """Set up test environment and locate test PDFs."""
        cls.test_dir = Path(__file__).parent
        cls.test_pdfs_dir = cls.test_dir / "test_pdfs"
        cls.test_judgment_pdfs_dir = cls.test_dir / "test_judgment_pdfs"
        cls.expected_output_dir = cls.test_dir / "expected_html"
        cls.actual_output_dir = cls.test_dir / "actual_html"
        cls.diff_output_dir = cls.test_dir / "diff_results"

        # Create necessary directories
        cls.actual_output_dir.mkdir(exist_ok=True)
        cls.diff_output_dir.mkdir(exist_ok=True)

        # Set up logging for tests
        logging.basicConfig(level=logging.WARNING)  # Reduce noise during tests

        cls.csv_file = cls.test_dir / "test_cases.csv"
        cls.judgment_csv_file = cls.test_dir / "test_judgment_cases.csv"
        cls.csv_registry = {
            cls.csv_file.name: (cls.csv_file, cls.test_pdfs_dir),
            cls.judgment_csv_file.name: (cls.judgment_csv_file, cls.test_judgment_pdfs_dir),
        }

        cls.test_cases = []
        selected = cls.selected_csvs or list(cls.csv_registry.keys())
        for name in selected:
            name = Path(name).name
            if name not in cls.csv_registry:
                print(f"Warning: unknown --csv selection '{name}' (known: "
                      f"{', '.join(cls.csv_registry)}) - skipping")
                continue
            csv_path, pdfs_dir = cls.csv_registry[name]
            # Read test cases from this CSV file; output always goes to the shared
            # actual_html/expected_html/diff_results dirs regardless of which CSV(s) ran.
            cls._load_test_cases_from_csv(csv_path, pdfs_dir)

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

        for test_case in self.test_cases:
            with self.subTest(pdf=test_case['pdf_name'], pdf_type=test_case.get('pdf_type', 'default')):
                result = run_test_case(
                    test_case, self.actual_output_dir, self.expected_output_dir, self.diff_output_dir
                )
                results.append(result)
                print(f"[{result['status']}] {result['pdf_name']} - TESTCASE: {test_case}")
                self.assertNotEqual(result['status'], 'ERROR', result.get('message'))

        generate_test_report(results, self.diff_output_dir)

    @staticmethod
    def _parse_bool(value):
        return value.strip().lower() in ['true', 'yes', '1']

    @classmethod
    def _load_test_cases_from_csv(cls, csv_file, pdfs_dir):
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row['filename'].strip()
                    if not filename:
                        continue

                    pdf_path = pdfs_dir / filename
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
                    ocr_language = row.get('ocr_language', '').strip() or 'eng'
                    ocr_engine = row.get('ocr_engine', '').strip() or 'tesseract'
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
                        'ocr_engine': ocr_engine,
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
            print(f"Error reading CSV file {csv_file}: {e}")

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        # Test with non-existent PDF
        with self.assertLogs(level='ERROR'):
            main = Main(
                pdfPath="non_existent.pdf",
                is_amendment_pdf=False,
                output_dir=str(self.actual_output_dir),
                pdf_type=None,
                has_side_notes = False,
                has_doc_end = False,
                is_footnote_continuation = False,
                min_img_pixels = 0,
                ocr_language = 'eng',
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


def run_parallel(test_cases, actual_output_dir, expected_output_dir, diff_output_dir, workers):
    results = []
    print(f"Running {len(test_cases)} test case(s) across {workers} worker process(es)...")

    spawn_context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=spawn_context) as executor:
        futures = {
            executor.submit(run_test_case, test_case, actual_output_dir, expected_output_dir, diff_output_dir): test_case
            for test_case in test_cases
        }

        for future in as_completed(futures):
            test_case = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    'pdf_name': test_case['pdf_name'],
                    'pdf_type': test_case.get('pdf_type', 'default'),
                    'status': 'ERROR',
                    'message': str(e)
                }
            results.append(result)
            print(f"[{result['status']}] {result['pdf_name']} - TESTCASE: {test_case}")

    generate_test_report(results, diff_output_dir)

    failed = [r for r in results if r['status'] != 'PASS']
    if failed:
        print(f"\n{len(failed)} of {len(results)} test case(s) did not pass.")
    else:
        print(f"\nAll {len(results)} test case(s) passed.")

    return len(failed) == 0


if __name__ == "__main__":
    # Create test directory structure if it doesn't exist
    test_dir = Path(__file__).parent
    test_pdfs_dir = test_dir / "test_pdfs"
    actual_html_dir = test_dir / "actual_html"
    expected_html_dir = test_dir / "expected_html"
    diff_results_dir = test_dir / "diff_results"
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

    diff_results_dir.mkdir(exist_ok=True)

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
        default=1,
        help="Number of worker processes to run test cases in parallel (default: 1, sequential)."
    )
    parser.add_argument(
        "--csv",
        dest="csv_files",
        action="append",
        default=None,
        help="Which test-cases CSV to run (by filename, e.g. test_cases.csv or "
             "test_judgment_cases.csv). Repeatable. Default: run every registered CSV. "
             "Output always goes to actual_html/ (and expected_html/, diff_results/) "
             "regardless of which CSV(s) are selected."
    )
    args, remaining = parser.parse_known_args()

    if args.csv_files:
        TestPdfToHtmlDiff.selected_csvs = args.csv_files

    # If update flag is passed → update golden files directly
    if args.update_golden:
        update_golden_files(actual_html_dir, expected_html_dir)
    elif args.workers > 1:
        TestPdfToHtmlDiff.setUpClass()
        ok = run_parallel(
            TestPdfToHtmlDiff.test_cases, actual_html_dir, expected_html_dir,
            diff_results_dir, args.workers
        )
        raise SystemExit(0 if ok else 1)
    else:
        unittest.main(argv=[sys.argv[0]] + remaining)
