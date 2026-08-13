import os
import argparse
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict
import re
import codecs
import html
import logging
import shutil
from .ParserTool import ParserTool, ChromeLensParserTool, TesseractParserTool
from .Page import Page, SectionState
from .Judgment import JudgmentBuilder
from .HTMLBuilder import HTMLBuilder, HTMLBuilderChromeLens
from .Acts import Acts
from .SebiCirculars import SebiCirculars
from .Amendment import Amendment
from .Utils import *
from .FontMapper import DynamicFontMapper
from .Manifest import IIIFManifest
from .TableExtraction import HeaderRowClassifier, RegionMergeClassifier, ContinuationClassifier

class Main:
    def __init__(self,pdfPath,is_amendment_pdf,output_dir, pdf_type, has_side_notes, has_doc_end,
                 is_footnote_continuation, min_img_pixels, ocr_language, is_scanned_copy,
                 table_extract, public_base_url=None, server_root=None,
                 rights=None, provider_id=None, provider_name=None, attribution=None,
                 figure_text=False, ocr_engine="tesseract"): #start,end,is_amendment_pdf,output_dir, pdf_type):
        self.logger = logging.getLogger('source.Main')
        if self.is_url_like(output_dir):
            raise ValueError(
                f"output_dir ('{output_dir}') looks like a URL, not a local filesystem "
                f"path where output files get written. Did you mean to pass that as "
                f"public_base_url/-pu instead? The public URL used for IIIF manifest "
                f"links is always supplied separately and never derived from output_dir."
            )
        if server_root:
            if self.is_url_like(server_root):
                raise ValueError(
                    f"server_root ('{server_root}') looks like a URL, not a local "
                    f"filesystem path. It must be the local directory that corresponds "
                    f"to the web server's document root; the public URL is supplied "
                    f"separately via public_base_url/-pu."
                )
            try:
                Path(output_dir).resolve().relative_to(Path(server_root).resolve())
            except ValueError:
                raise ValueError(
                    f"output_dir ('{output_dir}') is not located within "
                    f"server_root ('{server_root}') - IIIF manifest URLs can't be "
                    f"expressed relative to a server root that doesn't contain the "
                    f"output directory."
                )

        if rights and not self.is_url_like(rights):
            self.logger.warning(
                f"[!] rights ('{rights}') doesn't look like a URI (IIIF requires a "
                f"Creative Commons or RightsStatements.org URI) - ignoring it."
            )
            rights = None
        if provider_id and not self.is_url_like(provider_id):
            self.logger.warning(
                f"[!] provider_id ('{provider_id}') doesn't look like a URI - ignoring it."
            )
            provider_id = None
        if ocr_engine == "paddleocr" and ocr_language not in TESSERACT_TO_PADDLE_LANG:
            self.logger.warning(
                f"[!] ocr_language ('{ocr_language}') has no paddleocr equivalent. "
                f"Languages supported for ocr_engine='paddleocr': "
                f"{', '.join(sorted(TESSERACT_TO_PADDLE_LANG))}. Falling back to 'eng'."
            )
            ocr_language = "eng"

        self.pdf_path = pdfPath
        self.output_dir = output_dir
        self.server_root = server_root
        self.rights = rights
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.attribution = attribution
        self.parserTool = ParserTool()
        self.total_pgs = 0
        self.all_pgs = {}
        self.pdf_type = pdf_type  # Store pdf_type for later use
        self.has_doc_end = has_doc_end
        self.is_amendment_pdf = is_amendment_pdf
        self.has_side_notes = has_side_notes
        self.amendment = Amendment()
        self.section_state = SectionState()
        self.article_state = SectionState()
        self.title_state = SectionState()
        self.is_preamble_reached = False
        self.section_shorttitle_notend_status = False
        self.is_footnote_continuation = is_footnote_continuation
        self.fontmapper = DynamicFontMapper(self.pdf_path, out_dir=self.output_dir)
        self.unique_images = {}
        self.all_footnote_text = {}
        self.html_builder = None
        self.min_img_pixels = min_img_pixels
        self.ocr_language = ocr_language
        self.ocr_engine = ocr_engine
        self.is_scanned_copy = is_scanned_copy
        self.table_extract = table_extract
        self.figure_text = figure_text
        self.public_base_url = public_base_url
        # Public URL of the IIIF manifest (egazette only), set by write_manifest() once
        self.manifest_url = None
        self.header_classifier = HeaderRowClassifier.default()
        self.region_merge_classifier = RegionMergeClassifier.default()
        self.continuation_classifier = ContinuationClassifier.default()
        # Column template carried between pages so a borderless table that runs off the
        # bottom of one page can be picked up at the top of the next (set per page in the
        # processing loops, reset here so it never leaks across documents/runs).
        self.pending_continuation = None
        # self.fontmapper.extract_fonts()

    def get_all_footnote_text(self):

        FOOTNOTE_START_RE = re.compile(
            r'^\{\{\^\{\{FOOTNOTE\s*(.+?)\}\}\}\}'
        )

        active_footnote_num = None
        active_footnote_page = None

        for pg_num in sorted(self.all_pgs.keys()):

            page = self.all_pgs[pg_num]

            page_footnote_text = self.all_footnote_text.setdefault(pg_num, {})

            for tb in page.all_tbs.keys():

                if page.all_tbs[tb] != 'footnote':
                    continue

                for textline in tb.tbox.findall(".//textline"):

                    line_parts = []

                    pending_superscript = []

                    for text in textline.findall(".//text"):

                        raw = text.text or ""

                        if not raw:
                            continue

                        is_super = False

                        if "bbox" in text.attrib:

                            try:

                                bbox = tuple(
                                    map(
                                        float,
                                        text.attrib["bbox"].split(",")
                                    )
                                )

                                if bbox in tb.footnotes_superscript:

                                    pending_superscript.append(
                                        tb.footnotes_superscript[bbox]
                                    )

                                    is_super = True

                            except Exception:
                                pass

                        if not is_super:

                            if pending_superscript:

                                marker = "".join(
                                    pending_superscript
                                )

                                line_parts.append(
                                    "{{^{{FOOTNOTE "
                                    + marker +
                                    "}}}}"
                                )

                                pending_superscript = []

                            line_parts.append(raw)

                    if pending_superscript:

                        marker = "".join(
                            pending_superscript
                        )

                        line_parts.append(
                            "{{^{{FOOTNOTE "
                            + marker +
                            "}}}}"
                        )

                    text = "".join(line_parts)

                    text = re.sub(
                        r'\s+',
                        ' ',
                        text
                    ).strip()

                    if not text:
                        continue

                    start_match = FOOTNOTE_START_RE.match(text)

                    if start_match:

                        footnote_num = (
                            start_match.group(1).strip()
                        )

                        active_footnote_num = footnote_num
                        active_footnote_page = pg_num

                        cleaned_text = FOOTNOTE_START_RE.sub(
                            '',
                            text,
                            count=1
                        ).strip()

                        cleaned_text = re.sub(
                            r'^[.\):\]]\s*',
                            '',
                            cleaned_text
                        )

                        if (
                            footnote_num
                            not in page_footnote_text
                        ):

                            page_footnote_text[
                                footnote_num
                            ] = cleaned_text

                        else:

                            page_footnote_text[
                                footnote_num
                            ] += "\n" + cleaned_text

                    else:

                        if not active_footnote_num or active_footnote_page is None:
                            continue

                        self.all_footnote_text[
                            active_footnote_page
                        ][
                            active_footnote_num
                        ] += "\n" + text

            if not self.is_footnote_continuation:

                active_footnote_num = None
                active_footnote_page = None

    def finalize_unique_images(self):

        remove_hashes = []

        for img_hash, meta in self.unique_images.items():

            if meta.get("count", 0) > 1:

                img_path = meta.get("path")


                if img_path and os.path.exists(img_path):

                    try:
                        os.remove(img_path)

                        self.logger.info(
                            f"Deleted duplicate image: {img_path}"
                        )

                        self.remove_empty_parent_dir(img_path)
                    
                    except Exception as e:

                        self.logger.warning(
                            f"Failed deleting image "
                            f"{img_path}: {e}"
                        )


                for pg_num in meta.get("pages", set()):
                    pg_num = int(pg_num)
                    page_obj = self.all_pgs.get(pg_num)
                    if page_obj and hasattr(page_obj, "figures"):
                        try:
                            page_obj.figures.remove_hash(img_hash)

                        except Exception as e:

                            self.logger.warning(
                                f"Failed removing image hash "
                                f"{img_hash} from page {pg_num}: {e}"
                            )


                remove_hashes.append(img_hash)


        for img_hash in remove_hashes:

            del self.unique_images[img_hash]

        self.logger.info(
            f"Remaining unique images: "
            f"{len(self.unique_images)}"
        )
    
    def remove_empty_parent_dir(self, file_path):
        try:
            current = os.path.dirname(file_path)
            while current and os.path.isdir(current) and os.path.basename(current) != "images":
                os.rmdir(current)

                self.logger.debug(
                    f"Removed empty image directory: {current}"
                )

                current = os.path.dirname(current)

        except OSError:
            pass

        except Exception:
            self.logger.exception(
                f"Failed removing directory for: {file_path}"
            )
    
    def get_htmlBuilder(self, pdf_type, docend_symbol = False):
        if pdf_type == 'sebi':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";') #( ".", ":", "?",  ".'", '."', ";", ";'", ';"')
            return HTMLBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
            # return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        elif pdf_type in set(['acts']):
            sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-')
            return Acts(self.all_footnote_text, sentence_completion_punctutation, pdf_type, docend_symbol)
        elif pdf_type in set(['sebi_circulars']):
            sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-', '.]',
                                                ',-', ':-', ';-', '--')
            return SebiCirculars(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type, docend_symbol)

        elif pdf_type == 'judgments':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";')
            return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        else:
            sentence_completion_punctutation = ('.', ':')
            return HTMLBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
            # return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        
    # --- func to build HTML after text classification ---
    def buildHTML(self, start_page, end_page): #, section_page_end):
        if not self.html_builder:
            return
        if not self.all_pgs:
            self.html_builder.build(start_page, end_page)
            html_content = self.html_builder.get_html()
            self.write_html(html_content, start_page, end_page)
            return
        
        for page in self.all_pgs.values():
            self.logger.info(f"HTML build starts for page num-{page.pg_num}")
            self.html_builder.build(page, self.has_side_notes) #, section_page_end)
        
        self.logger.debug("Fetching Full HTML content")
        if self.pdf_type not in set(['acts', 'sebi_circulars']):
            html_content = self.html_builder.get_html()
            self.write_html(html_content, start_page, end_page)
        else:
            content = self.html_builder.get_content()
            self.write_bluebell(content, start_page, end_page)

    # --- classify the page texboxes sidenotes, section, para, titles(headings) ---
    def process_pages_acts(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            # page.print_tbs()
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            page.find_sidenote_leftend_rightstart_coords()
            page.get_side_notes() #self.section_start_page,self.section_end_page)
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            if self.is_amendment_pdf:
                self.amendment.check_for_amendment_acts(page)#,self.section_start_page,self.section_end_page)
            page.get_article(self.article_state, self)
            page.get_section_para(self.section_state, self)#, self.section_start_page,self.section_end_page)
            page.get_titles(pdf_type)
            page.sort_all_boxes()
            page.print_all()
            # page.print_headers()
            # page.print_footers()

    def process_pages_sebi_circulars(self, pdf_type):
        prev_sent_end_status = True
        sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-', '.]',
                                                ',-', ':-', ';-', '--')

        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_bulletins_sebi_circulars(self.section_state)
            page.get_titles(pdf_type)
            prev_sent_end_status = page.get_title_hierarchy(self.title_state, prev_sent_end_status, sentence_completion_punctutation)   
            page.sort_all_boxes()
            # page.print_blockquote()
            # page.print_headers()
            # page.print_footers()
            # page.print_levels()
            page.print_all()
            # page.print_tbs()

    def process_pages_sebi(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            # page.get_italic_blockquotes(pdf_type)
            # self.amendment.check_for_blockquotes(page)
            self.amendment.check_for_blockquotes_judgments(page)
            page.detect_sparse_pre()
            # page.get_titles(pdf_type)
            page.get_bulletins(self.section_state)
            page.get_titles(pdf_type)
            page.sort_all_boxes()
            # page.print_blockquote()
            # page.print_headers()
            # page.print_footers()
            # page.print_levels()
            page.print_all()
            # page.print_tbs()

    def process_pages_judgments(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            # page.get_italic_blockquotes(pdf_type)
            self.amendment.check_for_blockquotes_judgments(page)
            page.detect_sparse_pre()
            # page.detect_pre()
           
            # page.get_titles(pdf_type)
            # page.get_bulletins(self.section_state)
            page.sort_all_boxes()
            # page.print_headers()
            # page.print_footers()
            page.print_all()
    
    def process_pages(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_titles(pdf_type)
            # page.get_bulletins(self.section_state)
            page.sort_all_boxes()
            # page.print_headers()
            # page.print_footers()
            page.print_all()

    def print_labels(self, pdf_type):
        #for page in self.all_pgs.values():
            # page.print_table_content()
            # page.print_headers()
            # page.print_footers()
            # page.print_sidenotes()
            # page.print_titles()
            # page.print_section_para()
            # page.print_all()
            # page.print_amendment()
            # # page.print_tbs()
            # self.bq_layout.print_sections()
        pass

    # --- NEW ADAPTIVE HEADER/FOOTER DETECTION ---
    def get_page_header_footer(self, pages, base_name_of_file, output_dir):
        # Initialize page objects first
        for pg in pages:
            pdf_dir = self.get_path_cache_pdf()
            if not self.pdf_path.lower().endswith(".pdf"):
                base_name = os.path.basename(self.pdf_path) + ".pdf"
                new_pdf_path = os.path.join(pdf_dir, base_name)
                shutil.copy(self.pdf_path, new_pdf_path)
                self.logger.debug(f"Copied input file to cache dir as: {new_pdf_path}")
                self.pdf_path = new_pdf_path

            page = Page(pg, self.pdf_path, base_name_of_file, output_dir,
                        self.pdf_type, self.has_side_notes, self.is_amendment_pdf,
                        self.fontmapper, self.unique_images, self.min_img_pixels,
                        self.ocr_language,
                        self.is_scanned_copy, self.figure_text, self.ocr_engine)
            self.total_pgs += 1
            self.all_pgs[self.total_pgs] = page
            page.process_textboxes()#pg)
            page.get_figures()#pg)
            page.label_table_tbs()

            # page.line_based_header_footer_detection()
        # Run adaptive header/footer detection
        self.logger.info("Starting adaptive header/footer detection...")
        if not self.is_scanned_copy:
            self.adaptive_header_footer_detection(pages, self.pdf_type)

        if self.table_extract and self.pdf_type != 'judgments':
            self.logger.info("Detecting borderless tables...")
            self.pending_continuation = None
            for page in self.all_pgs.values():
                page.reclaim_header_footer_for_continuation(self.pending_continuation)
                self.pending_continuation = page.get_borderless_table(
                    self.pdf_type, self.header_classifier, self.region_merge_classifier,
                    continuation_template=self.pending_continuation,
                    continuation_classifier=self.continuation_classifier,
                )
                page.label_borderless_table_tbs()

        self.logger.info("Detecting multicolumn page layouts...")
        for page in self.all_pgs.values():
            page.detect_multicolumn_layout()
            page.apply_column_reading_order()

        if self.pdf_type in {'judgments'}:
            self.detect_header_pre(pages)
        # elif self.pdf_type in {'sebi'}:
        #     self.detect_sebi_header_pre(pages)

        self.detect_toc(pages)

        previous_page_footnote_font_size  = None
        seen_footnote = set()
        for page in self.all_pgs.values():
            page.mark_standalone_footnote_markers()
            if self.is_footnote_continuation:
                previous_page_footnote_font_size, seen_footnote = (
                    page.get_footnotes(
                        seen_footnote,
                        previous_page_footnote_font_size
                    )
                )
            else:
                page.get_footnotes()
            page.detect_footnote_blocks_by_style()

        self.finalize_unique_images()
        if not self.unique_images:
            self.remove_empty_manifest_dir(base_name_of_file, output_dir)
        self.get_all_footnote_text()
        self.logger.info(self.all_footnote_text)

    def remove_empty_manifest_dir(self, base_name_of_file, output_dir, image_base_dir="manifest"):
        manifest_pdf_dir = os.path.join(output_dir, image_base_dir, base_name_of_file)
        images_dir = os.path.join(manifest_pdf_dir, "images")
        for directory in (images_dir, manifest_pdf_dir):
            try:
                if os.path.isdir(directory) and not os.listdir(directory):
                    os.rmdir(directory)
                    self.logger.debug(f"Removed empty manifest directory: {directory}")
            except OSError as e:
                self.logger.debug(f"Could not remove manifest directory {directory}: {e}")

    def adaptive_header_footer_detection(self, pages, pdf_type=None):
        self.adaptive_headers = []
        self.adaptive_footers = []
        page_elements = []
        
        # Simple working configuration
        if pdf_type not in set(['sebi_circulars']):
            HEADER_ZONE_THRESHOLD = 0.12#0.15    # Top 15% of page height
            FOOTER_ZONE_THRESHOLD = 0.12#0.15    # Bottom 15% of page height
            SIMILARITY_THRESHOLD =  0.8       # 80% similarity
            MIN_OCCURRENCE_RATE =   0.4     # Must appear on at least 40% of pages
            LINE_TOLERANCE = 0.02           # 2% of page height tolerance for same line detection
        
        else:
            HEADER_ZONE_THRESHOLD = 0.12#0.15    # Top 15% of page height
            FOOTER_ZONE_THRESHOLD = 0.12#0.15    # Bottom 15% of page height
            SIMILARITY_THRESHOLD =  0.9       # 80% similarity
            MIN_OCCURRENCE_RATE =   0.6     # Must appear on at least 40% of pages
            LINE_TOLERANCE = 0.02 

        try:
            total_pages = len(pages)
            self.logger.info("Starting adaptive header/footer detection on %d pages", total_pages)
            
            # Special handling for single-page PDFs
            if total_pages == 1:
                self.logger.info("Single-page PDF detected - using strict header/footer detection")
                self._handle_single_page_header_footer_detection(pages, pdf_type, HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
                return
            
            # Step 1: Extract all textboxes with normalized coordinates
            for pg_idx, pg in enumerate(pages):
                page_num = pg_idx + 1
                if page_num not in self.all_pgs:
                    continue
                    
                page_obj = self.all_pgs[page_num]
                
                for tb, label in page_obj.all_tbs.items():
                    try:
                        if label is not None:
                            continue
                        text = tb.extract_text_from_tb().strip()
                        if not text or text.isspace():
                            continue
                            
                        # Normalize coordinates as percentages of page dimensions
                        x0_pct = tb.coords[0] / page_obj.pg_width
                        y0_pct = tb.coords[1] / page_obj.pg_height
                        x1_pct = tb.coords[2] / page_obj.pg_width
                        y1_pct = tb.coords[3] / page_obj.pg_height
                        
                        width_pct = x1_pct - x0_pct
                        height_pct = y1_pct - y0_pct
                        
                        # Calculate relative position zones
                        is_header_zone = y0_pct >= (1 - HEADER_ZONE_THRESHOLD)
                        is_footer_zone = y0_pct <= FOOTER_ZONE_THRESHOLD
                        
                        page_elements.append({
                            'page_num': page_num,
                            'text': text,
                            'textbox': tb,
                            'x0_pct': x0_pct,
                            'y0_pct': y0_pct,
                            'x1_pct': x1_pct,
                            'y1_pct': y1_pct,
                            'width_pct': width_pct,
                            'height_pct': height_pct,
                            'is_header_zone': is_header_zone,
                            'is_footer_zone': is_footer_zone,
                            'is_centered': abs(x0_pct + width_pct/2 - 0.5) < 0.1,
                            'is_left_aligned': x0_pct < 0.1,
                            'is_right_aligned': x1_pct > 0.9
                        })
                        
                    except Exception as e:
                        self.logger.warning("Error processing textbox on page %d: %s", page_num, e)
                        continue
            
            if not page_elements:
                self.logger.warning("No valid page elements found for header/footer detection")
                return
                
            # Count elements in zones
            header_zone_count = sum(1 for elem in page_elements if elem['is_header_zone'])
            footer_zone_count = sum(1 for elem in page_elements if elem['is_footer_zone'])
            self.logger.info("Found %d elements in header zones, %d in footer zones", 
                           header_zone_count, footer_zone_count)
            
            # Debug: Show coordinate distribution to understand the issue
            if page_elements:
                y_coords = [elem['y0_pct'] for elem in page_elements]
                min_y = min(y_coords)
                max_y = max(y_coords)
                self.logger.info("Y-coordinate range: %.3f to %.3f", min_y, max_y)
                self.logger.info("Header zone threshold (y >= %.3f), Footer zone threshold (y <= %.3f)", 
                               1 - HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
                
                # Show some sample elements with their coordinates
                self.logger.info("Sample elements by Y position:")
                sorted_elements = sorted(page_elements, key=lambda e: e['y0_pct'])
                for i in [0, len(sorted_elements)//2, -1]:
                    if 0 <= i < len(sorted_elements):
                        elem = sorted_elements[i]
                        self.logger.info("  Y=%.3f: '%s' (header_zone=%s, footer_zone=%s)", 
                                       elem['y0_pct'], elem['text'][:40], 
                                       elem['is_header_zone'], elem['is_footer_zone'])
            
            # Step 2: Simple similarity calculation
            def calculate_similarity(elem1, elem2):
                if re.fullmatch(r'\d+', elem1['text'].strip()) and re.fullmatch(r'\d+', elem2['text'].strip()):
                    return 1.0
                text_sim = SequenceMatcher(None, elem1['text'], elem2['text']).ratio()
                x_sim = 1 - abs(elem1['x0_pct'] - elem2['x0_pct'])
                y_sim = 1 - abs(elem1['y0_pct'] - elem2['y0_pct'])
                width_sim = 1 - abs(elem1['width_pct'] - elem2['width_pct'])
                
                alignment_sim = 1.0 if (elem1['is_centered'] == elem2['is_centered'] and 
                                      elem1['is_left_aligned'] == elem2['is_left_aligned'] and 
                                      elem1['is_right_aligned'] == elem2['is_right_aligned']) else 0.8
                
                overall_sim = (text_sim * 0.4 + x_sim * 0.2 + y_sim * 0.2 + 
                             width_sim * 0.1 + alignment_sim * 0.1)
                
                return overall_sim
            
            # Step 3: Find header candidates (including those marked by uploaded_by detection)
            header_candidates = [elem for elem in page_elements if elem['is_header_zone']]
            
            # Add any headers marked by uploaded_by detection
            uploaded_by_headers = [elem for elem in page_elements if elem.get('marked_by_uploaded_by') and elem.get('is_header_zone')]
            for header_elem in uploaded_by_headers:
                if header_elem not in header_candidates:
                    header_candidates.append(header_elem)
            
            header_groups = self._group_similar_elements(header_candidates, calculate_similarity, 
                                                       SIMILARITY_THRESHOLD, total_pages, MIN_OCCURRENCE_RATE)
            
            # Step 4: Find footer candidates with adaptive detection
            footer_candidates = [elem for elem in page_elements if elem['is_footer_zone']]
            
            # Add special regex-based detection for "uploaded by" patterns in footer area (only for 'acts' pdf type)
            uploaded_by_candidates = []
            if pdf_type == 'acts':
                self.logger.info("Processing 'uploaded by' patterns for PDF type 'acts'")
                
                # Group elements by page for easier processing
                pages_dict = {}
                for elem in page_elements:
                    page_num = elem['page_num']
                    if page_num not in pages_dict:
                        pages_dict[page_num] = []
                    pages_dict[page_num].append(elem)
                
                for elem in page_elements:
                    text_lower = elem['text'].lower().strip()
                    # Check if text matches "uploaded by" pattern and is in footer area (bottom 50% of page)
                    if re.search(r'^uploaded\s*by\s*\S*\s*', text_lower) and elem['y0_pct'] <= 0.5:
                        elem['is_footer_zone'] = True  # Mark as footer zone
                        uploaded_by_candidates.append(elem)
                        self.logger.info("Found 'uploaded by' pattern in footer area: page=%d, y=%.3f, text='%s'", 
                                       elem['page_num'], elem['y0_pct'], elem['text'][:40])
                        
                        # Find and mark related textboxes on the same page within threshold areas
                        page_num = elem['page_num']
                        if page_num in pages_dict:
                            self._mark_related_header_footer_textboxes(elem, pages_dict[page_num], uploaded_by_candidates, 
                                                                     HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
            else:
                self.logger.debug("Skipping 'uploaded by' pattern detection for PDF type '%s' (only works for 'acts')", pdf_type)
            
            # Add uploaded by candidates to footer candidates
            footer_candidates.extend(uploaded_by_candidates)
            
            # If no footers found with current logic, try finding elements at actual bottom of pages
            if not footer_candidates:
                self.logger.info("No footers found with standard detection, trying adaptive approach...")
                
                # Group elements by page and find the ones at the bottom of each page
                pages_dict = {}
                for elem in page_elements:
                    page_num = elem['page_num']
                    if page_num not in pages_dict:
                        pages_dict[page_num] = []
                    pages_dict[page_num].append(elem)
                
                # For each page, find elements that are actually at the bottom
                adaptive_footer_candidates = []
                for page_num, page_elems in pages_dict.items():
                    if len(page_elems) < 2:
                        continue
                    
                    # Sort by Y coordinate to find bottom elements
                    sorted_elems = sorted(page_elems, key=lambda e: e['y0_pct'])
                    
                    # Take elements from the bottom portion of the page
                    bottom_threshold = 0.25  # Bottom 25% of elements
                    num_bottom_elements = max(1, int(len(sorted_elems) * bottom_threshold))
                    bottom_elements = sorted_elems[:num_bottom_elements]
                    
                    # Add these as footer candidates
                    for elem in bottom_elements:
                        elem['is_footer_zone'] = True  # Mark as footer zone
                        adaptive_footer_candidates.append(elem)
                        self.logger.debug("Adaptive footer candidate: page=%d, y=%.3f, text='%s'", 
                                        page_num, elem['y0_pct'], elem['text'][:40])
                
                footer_candidates.extend(adaptive_footer_candidates)
                self.logger.info("Found %d adaptive footer candidates", len(adaptive_footer_candidates))
            
            # Group footer candidates, but handle "uploaded by" patterns separately
            regular_footer_candidates = [elem for elem in footer_candidates 
                                       if not re.search(r'^uploaded\s*by\s*\S*\s*', elem['text'].lower().strip())]
            footer_groups = self._group_similar_elements(regular_footer_candidates, calculate_similarity,
                                                       SIMILARITY_THRESHOLD, total_pages, MIN_OCCURRENCE_RATE)
            
            # Add special groups for "uploaded by" patterns with relaxed criteria
            uploaded_by_groups = self._group_uploaded_by_patterns(uploaded_by_candidates, total_pages)
            footer_groups.extend(uploaded_by_groups)
            
            self.logger.info("Grouped into %d header groups and %d footer groups", 
                           len(header_groups), len(footer_groups))
            
            # Step 5: Simple validation - just use the groups as they are
            self.adaptive_headers = header_groups
            self.adaptive_footers = footer_groups
            
            self.logger.info("Adaptive detection complete: %d header groups, %d footer groups", 
                           len(self.adaptive_headers), len(self.adaptive_footers))
            
            # Step 6: Extend headers/footers to include textboxes on same lines
            self._extend_headers_footers_by_line(page_elements, LINE_TOLERANCE)
            
            # Step 7: Apply the detected headers and footers to pages
            self._apply_adaptive_headers_footers()
            
        except Exception as e:
            self.logger.exception("Error during adaptive header/footer detection: %s", e)
    
    
    def _analyze_header_footer_content(self, text):
        import re
        
        text_lower = text.lower().strip()
        
        # Common header/footer patterns
        header_footer_patterns = [
            r'page\s*\d+',           # Page numbers
            r'\d+\s*page',           # Page numbers (reverse)
            r'^\d+$',                # Just numbers
            r'chapter\s*\d+',        # Chapter references
            r'section\s*\d+',        # Section references
            r'\d{4}',                # Years
            r'copyright',            # Copyright notices
            r'©',                    # Copyright symbol
            r'confidential',         # Confidentiality notices
            r'draft',                # Draft notices
            r'www\.',                # Web addresses
            r'\.com|\.org|\.gov',    # Domain extensions
            r'^\d+[-./]\d+',         # Date patterns
            r'rev\.|revision',       # Revision markers
            r'version\s*\d+',        # Version numbers
            r'^uploaded\s*by\s*\S*\s*',  # Uploaded by patterns
        ]
        
        # Content that's unlikely to be header/footer
        unlikely_patterns = [
            r'\w{50,}',              # Very long words (likely body text)
            r'[.!?]\s+[A-Z]',        # Sentences (multiple sentences)
            r'\w+\s+\w+\s+\w+\s+\w+\s+\w+',  # 5+ words (likely paragraph)
        ]
        
        # Check for header/footer indicators
        for pattern in header_footer_patterns:
            if re.search(pattern, text_lower):
                return True
        
        # Check for unlikely content
        for pattern in unlikely_patterns:
            if re.search(pattern, text):
                return False
        
        # Additional heuristics
        if len(text) < 5:  # Very short text might be page numbers
            return True
        
        if len(text) > 100:  # Long text unlikely to be header/footer
            return False
        
        # Check if it's mostly numbers or special characters
        alphanumeric_ratio = sum(c.isalnum() for c in text) / len(text)
        if alphanumeric_ratio < 0.5:  # Less than 50% alphanumeric
            return True
        
        # Default to False for body content
        return False
    
    def _group_uploaded_by_patterns(self, uploaded_by_candidates, total_pages):
        groups = []
        
        if not uploaded_by_candidates:
            return groups
        
        # Since "uploaded by" patterns can vary (different usernames), group them more loosely
        # Just check if they have the basic "uploaded by" pattern
        used_elements = set()
        
        for candidate in uploaded_by_candidates:
            if id(candidate) in used_elements:
                continue
                
            # Find all elements with "uploaded by" pattern
            similar_elements = [candidate]
            used_elements.add(id(candidate))
            
            for other in uploaded_by_candidates:
                if id(other) in used_elements:
                    continue
                    
                # For "uploaded by" patterns, just check if both match the pattern
                # (don't require high text similarity since usernames will differ)
                other_text_lower = other['text'].lower().strip()
                if re.search(r'^uploaded\s*by\s*\S*\s*', other_text_lower):
                    similar_elements.append(other)
                    used_elements.add(id(other))
            
            # For "uploaded by" patterns, accept even single occurrences
            # since they are explicitly identified by regex pattern
            if len(similar_elements) >= 1:  # Accept even single occurrence
                representative_text = "uploaded by [user]"  # Generic representative text
                
                avg_x0_pct = sum(elem['x0_pct'] for elem in similar_elements) / len(similar_elements)
                avg_y0_pct = sum(elem['y0_pct'] for elem in similar_elements) / len(similar_elements)
                
                groups.append({
                    'elements': similar_elements,
                    'representative_text': representative_text,
                    'avg_x0_pct': avg_x0_pct,
                    'avg_y0_pct': avg_y0_pct,
                    'occurrence_rate': len(similar_elements) / total_pages,
                    'pages': [elem['page_num'] for elem in similar_elements],
                    'quality_score': 0.9,  # High quality score for regex-matched patterns
                    'pattern_type': 'uploaded_by'
                })
                
                self.logger.info("Created 'uploaded by' footer group with %d elements across pages: %s", 
                               len(similar_elements), [elem['page_num'] for elem in similar_elements])
        
        return groups
    
    def _handle_single_page_header_footer_detection(self, pages, pdf_type, header_zone_threshold, footer_zone_threshold):
        try:
            page_elements = []
            page = pages[0]
            page_num = 1
            
            if page_num not in self.all_pgs:
                self.logger.warning("Page 1 not found in all_pgs for single-page detection")
                return
                
            page_obj = self.all_pgs[page_num]
            
            # Extract all textboxes with normalized coordinates
            for tb in page_obj.all_tbs.keys():
                try:
                    text = tb.extract_text_from_tb().strip()
                    if not text or text.isspace():
                        continue
                        
                    # Normalize coordinates as percentages of page dimensions
                    x0_pct = tb.coords[0] / page_obj.pg_width
                    y0_pct = tb.coords[1] / page_obj.pg_height
                    x1_pct = tb.coords[2] / page_obj.pg_width
                    y1_pct = tb.coords[3] / page_obj.pg_height
                    
                    width_pct = x1_pct - x0_pct
                    height_pct = y1_pct - y0_pct
                    
                    # Calculate relative position zones
                    is_header_zone = y0_pct >= (1 - header_zone_threshold)
                    is_footer_zone = y0_pct <= footer_zone_threshold
                    
                    page_elements.append({
                        'page_num': page_num,
                        'text': text,
                        'textbox': tb,
                        'x0_pct': x0_pct,
                        'y0_pct': y0_pct,
                        'x1_pct': x1_pct,
                        'y1_pct': y1_pct,
                        'width_pct': width_pct,
                        'height_pct': height_pct,
                        'is_header_zone': is_header_zone,
                        'is_footer_zone': is_footer_zone,
                        'is_centered': abs(x0_pct + width_pct/2 - 0.5) < 0.1,
                        'is_left_aligned': x0_pct < 0.1,
                        'is_right_aligned': x1_pct > 0.9
                    })
                    
                except Exception as e:
                    self.logger.warning("Error processing textbox on single page: %s", e)
                    continue
            
            self.logger.info("Found %d elements on single page", len(page_elements))
            
            # For single-page PDFs, be very strict about what constitutes headers/footers
            header_candidates = []
            footer_candidates = []
            
            # Only consider elements that are in the zones AND match header/footer patterns
            for elem in page_elements:
                text_lower = elem['text'].lower().strip()
                
                # Check if text has header/footer characteristics
                is_likely_header_footer = self._analyze_header_footer_content(elem['text'])
                
                # Be stricter for single-page: must be in zone AND match pattern
                if elem['is_header_zone'] and is_likely_header_footer:
                    header_candidates.append(elem)
                    self.logger.info("Single-page header candidate: y=%.3f, text='%s'", 
                                   elem['y0_pct'], elem['text'][:40])
                elif elem['is_footer_zone'] and is_likely_header_footer:
                    footer_candidates.append(elem)
                    self.logger.info("Single-page footer candidate: y=%.3f, text='%s'", 
                                   elem['y0_pct'], elem['text'][:40])
            
            # Handle special "uploaded by" patterns for acts PDFs (only if in appropriate zones)
            if pdf_type == 'acts':
                for elem in page_elements:
                    text_lower = elem['text'].lower().strip()
                    if re.search(r'^uploaded\s*by\s*\S*\s*', text_lower):
                        if elem['is_footer_zone'] or elem['y0_pct'] <= 0.5:  # Footer zone or bottom half
                            footer_candidates.append(elem)
                            self.logger.info("Single-page 'uploaded by' footer: y=%.3f, text='%s'", 
                                           elem['y0_pct'], elem['text'][:40])
                            # For single page, don't mark related textboxes to avoid false positives
            
            # Create simple groups for single-page elements
            if header_candidates:
                self.adaptive_headers = [{
                    'elements': header_candidates,
                    'representative_text': f"Single-page headers ({len(header_candidates)} items)",
                    'quality_score': 0.8,
                    'pattern_type': 'single_page_header'
                }]
            
            if footer_candidates:
                self.adaptive_footers = [{
                    'elements': footer_candidates,
                    'representative_text': f"Single-page footers ({len(footer_candidates)} items)",
                    'quality_score': 0.8,
                    'pattern_type': 'single_page_footer'
                }]
            
            self.logger.info("Single-page detection complete: %d header candidates, %d footer candidates", 
                           len(header_candidates), len(footer_candidates))
            
            # Apply the detected headers and footers
            self._apply_adaptive_headers_footers()
            
        except Exception as e:
            self.logger.exception("Error during single-page header/footer detection: %s", e)
    
    def _mark_related_header_footer_textboxes(self, uploaded_by_elem, page_elements, uploaded_by_candidates, 
                                            header_zone_threshold, footer_zone_threshold):
        try:
            uploaded_by_y = uploaded_by_elem['y0_pct']
            page_num = uploaded_by_elem['page_num']
            
            # Calculate threshold boundaries
            header_zone_min_y = 1 - header_zone_threshold  # Top threshold% of page
            footer_zone_max_y = footer_zone_threshold       # Bottom threshold% of page
            
            self.logger.debug("Marking related textboxes for 'uploaded by' pattern on page %d (y=%.3f)", 
                            page_num, uploaded_by_y)
            self.logger.debug("Header zone: y >= %.3f, Footer zone: y <= %.3f", 
                            header_zone_min_y, footer_zone_max_y)
            
            for elem in page_elements:
                # Skip if it's the same element or different page
                if elem['textbox'] == uploaded_by_elem['textbox'] or elem['page_num'] != page_num:
                    continue
                
                # Skip if already marked by uploaded_by detection (to avoid double processing)
                if elem.get('marked_by_uploaded_by'):
                    continue
                
                elem_y = elem['y0_pct']
                
                # Mark textboxes above the 'uploaded by' pattern as headers 
                # BUT only if they are in the header zone area
                if elem_y > uploaded_by_y and elem_y >= header_zone_min_y:
                    elem['is_header_zone'] = True
                    elem['marked_by_uploaded_by'] = True
                    self.logger.debug("Marked textbox above 'uploaded by' as header (in header zone): page=%d, y=%.3f, text='%s'", 
                                    page_num, elem_y, elem['text'][:30])
                    
                # Mark textboxes below the 'uploaded by' pattern as footers
                # BUT only if they are in the footer zone area  
                elif elem_y < uploaded_by_y and elem_y <= footer_zone_max_y:
                    elem['is_footer_zone'] = True
                    elem['marked_by_uploaded_by'] = True
                    uploaded_by_candidates.append(elem)  # Add to uploaded_by_candidates for grouping
                    self.logger.debug("Marked textbox below 'uploaded by' as footer (in footer zone): page=%d, y=%.3f, text='%s'", 
                                    page_num, elem_y, elem['text'][:30])
                
                # Log textboxes that are above/below but outside threshold areas
                elif elem_y > uploaded_by_y and elem_y < header_zone_min_y:
                    self.logger.debug("Textbox above 'uploaded by' but outside header zone (y=%.3f < %.3f): '%s'", 
                                    elem_y, header_zone_min_y, elem['text'][:30])
                elif elem_y < uploaded_by_y and elem_y > footer_zone_max_y:
                    self.logger.debug("Textbox below 'uploaded by' but outside footer zone (y=%.3f > %.3f): '%s'", 
                                    elem_y, footer_zone_max_y, elem['text'][:30])
                    
        except Exception as e:
            self.logger.exception("Error marking related textboxes for 'uploaded by' pattern: %s", e)
    
    def _group_similar_elements(self, candidates, similarity_func, threshold, total_pages, min_occurrence_rate):
        groups = []
        used_elements = set()

        for candidate in candidates:
            if id(candidate) in used_elements:
                continue
                
            # Find all similar elements
            similar_elements = [candidate]
            used_elements.add(id(candidate))
            
            for other in candidates:
                if id(other) in used_elements:
                    continue
                    
                if similarity_func(candidate, other) >= threshold:
                    similar_elements.append(other)
                    used_elements.add(id(other))
            
            # Check if this group meets minimum occurrence criteria
            occurrence_rate = len(similar_elements) / total_pages
            self.logger.debug("Group with %d elements has occurrence rate %.3f (min required: %.3f)", 
                            len(similar_elements), occurrence_rate, min_occurrence_rate)
            if occurrence_rate >= min_occurrence_rate:
                # Calculate representative text and position
                texts = [elem['text'] for elem in similar_elements]
                representative_text = max(set(texts), key=texts.count)
                
                avg_x0_pct = sum(elem['x0_pct'] for elem in similar_elements) / len(similar_elements)
                avg_y0_pct = sum(elem['y0_pct'] for elem in similar_elements) / len(similar_elements)
                
                groups.append({
                    'elements': similar_elements,
                    'representative_text': representative_text,
                    'avg_x0_pct': avg_x0_pct,
                    'avg_y0_pct': avg_y0_pct,
                    'occurrence_rate': occurrence_rate,
                    'pages': [elem['page_num'] for elem in similar_elements]
                })
        
        return groups
    
    def _extend_headers_footers_by_line(self, page_elements, line_tolerance=0.02):
        try:
            
            # Group page elements by page for easier processing
            pages_dict = {}
            for elem in page_elements:
                page_num = elem['page_num']
                if page_num not in pages_dict:
                    pages_dict[page_num] = []
                pages_dict[page_num].append(elem)
            
            # Process headers
            for header_group in self.adaptive_headers:
                self._extend_group_by_line(header_group, pages_dict, 'header', line_tolerance)
            
            # Process footers  
            for footer_group in self.adaptive_footers:
                self._extend_group_by_line(footer_group, pages_dict, 'footer', line_tolerance)
                
            self.logger.info("Extended headers/footers to include same-line textboxes")
            
        except Exception as e:
            self.logger.exception("Error extending headers/footers by line: %s", e)
    
    def _extend_group_by_line(self, group, pages_dict, group_type, line_tolerance):
        extended_elements = []
        
        # For each element in the group, find other textboxes on the same line
        for element in group['elements']:
            page_num = element['page_num']
            element_y = element['y0_pct']
            
            if page_num not in pages_dict:
                continue
                
            # Find textboxes on the same line (within tolerance)
            same_line_elements = []
            for other_elem in pages_dict[page_num]:
                # Skip if it's the same element
                if other_elem['textbox'] == element['textbox']:
                    continue
                    
                # Check if it's on the same line (within tolerance)
                y_diff = abs(other_elem['y0_pct'] - element_y)
                if y_diff <= line_tolerance:
                    # Check if this element is not already marked as header/footer
                    if not self._is_already_marked_as_header_footer(other_elem):
                        same_line_elements.append(other_elem)
                        
            # Add same-line elements to the group
            for same_line_elem in same_line_elements:
                extended_elements.append(same_line_elem)
                self.logger.debug("Extended %s on page %d: added '%s' (y=%.3f) to line with '%s' (y=%.3f)", 
                                group_type, page_num, same_line_elem['text'][:30], 
                                same_line_elem['y0_pct'], element['text'][:30], element_y)
        
        # Add extended elements to the group
        if extended_elements:
            group['elements'].extend(extended_elements)
            self.logger.info("Extended %s group '%s' with %d additional same-line elements", 
                           group_type, group.get('representative_text', '')[:40], len(extended_elements))
    
    def _is_already_marked_as_header_footer(self, element):
        # Check if element is in any header group
        for header_group in self.adaptive_headers:
            for header_elem in header_group['elements']:
                if header_elem['textbox'] == element['textbox'] and header_elem['page_num'] == element['page_num']:
                    return True
        
        # Check if element is in any footer group  
        for footer_group in self.adaptive_footers:
            for footer_elem in footer_group['elements']:
                if footer_elem['textbox'] == element['textbox'] and footer_elem['page_num'] == element['page_num']:
                    return True
                    
        return False

    def _apply_adaptive_headers_footers(self):
        try:
            for header_group in self.adaptive_headers:
                for element in header_group['elements']:
                    page_num = element['page_num']
                    textbox = element['textbox']

                    if page_num in self.all_pgs and textbox in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[textbox] = "header"
                        self.logger.debug("Applied adaptive header on page %d: '%s'",
                                        page_num, element['text'][:50])

            for footer_group in self.adaptive_footers:
                for element in footer_group['elements']:
                    page_num = element['page_num']
                    textbox = element['textbox']

                    if page_num in self.all_pgs and textbox in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[textbox] = "footer"
                        self.logger.debug("Applied adaptive footer on page %d: '%s'",
                                        page_num, element['text'][:50])

            self.logger.info("Successfully applied adaptive headers and footers to pages")

        except Exception as e:
            self.logger.exception("Error applying adaptive headers and footers: %s", e)
    
    def get_path_cache_xml(self):
        current_file = Path(__file__).resolve()       
        source_dir = current_file.parent.parent              
        cache_xml_dir = source_dir / "cache_xml"      
        cache_xml_dir.mkdir(parents=True, exist_ok=True)  
        return cache_xml_dir
    
    def is_pdf_file(self, path):
        try:
            with open(path, "rb") as f:
                header = f.read(1024)  # read first 1KB, enough for header
                return b"%PDF-" in header
        except Exception:
            return False

    @staticmethod
    def is_url_like(value):
        return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', str(value)))
    
    def set_htmlbuilder(self):
        if self.pdf_type in set(['acts']):
            self.html_builder = self.get_htmlBuilder(self.pdf_type, self.has_doc_end)
        elif self.pdf_type in set(['sebi_circulars']):
            self.html_builder = self.get_htmlBuilder(self.pdf_type)
        else:
            self.html_builder = self.get_htmlBuilder(self.pdf_type)

    def process_scanned_copy(self, pdf_type, base_name_of_file, start_page,
                             end_page):
        if pdf_type == 'egazette':
            pages = ChromeLensParserTool(self.pdf_path)\
                                .build_xml(start_page, end_page)
        else:
            pages = TesseractParserTool(self.pdf_path, self.ocr_language)\
                                            .build_xml(start_page, end_page)
        self.print_page_xml(pages)
        self.set_htmlbuilder()
        self.logger.debug("Extracting header and footer info...")
        self.get_page_header_footer(pages, base_name_of_file, self.output_dir)
        self.logger.debug("Processing content from pages...")
        if pdf_type == 'acts':
            self.process_pages_acts(pdf_type)
        elif pdf_type == 'sebi_circulars':
            self.process_pages_sebi_circulars(pdf_type)
        elif pdf_type == 'sebi':
            self.process_pages_sebi(pdf_type)
        elif pdf_type == 'judgments':
            self.process_pages_judgments(pdf_type)
        else:
            self.process_pages(pdf_type)
        self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
    
    # --- parse pdf using pdfminer to convert to XML ---       
    def parsePDF(self, pdf_type, char_margin, word_margin, line_margin, \
                start_page, end_page):
        try:
            if not os.path.exists(self.pdf_path):
                self.logger.error(f"[✖] Input file not found: {self.pdf_path}")
                return False
        
            if not self.is_pdf_file(self.pdf_path):
                self.logger.error(f"[✖] Input is not a valid PDF file: {self.pdf_path}")
                return False

            if self.is_url_like(self.output_dir):
                self.logger.error(
                    f"[✖] -o/--output-directory ('{self.output_dir}') looks like a URL, "
                    f"not a local filesystem path where output files get written. Did you "
                    f"mean to pass that as -pu/--public-base-url instead? The public URL "
                    f"used for IIIF manifest links is always supplied separately via "
                    f"-pu/--public-base-url (or the PUBLIC_BASE_URL env var) and never "
                    f"derived from output_dir."
                )
                return False

            base_name_of_file = os.path.splitext(os.path.basename(self.pdf_path))[0]
            self.logger.info("Starting PDF parsing for: %s", self.pdf_path)
            if self.is_scanned_copy:
                self.process_scanned_copy(pdf_type, base_name_of_file, start_page, 
                                          end_page)
                return True
            
            cache_xml_path = self.get_path_cache_xml()
            self.xml_path =  cache_xml_path / f"{base_name_of_file}.xml"
            self.logger.debug("Converting PDF to XML...")
            self.parserTool.convert_to_xml(self.pdf_path,self.xml_path, self.pdf_type, \
                                           char_margin, word_margin, line_margin)

            
            if not os.path.exists(self.xml_path):
                self.logger.error("XML file was not created: %s", self.xml_path)
                return False

            self.logger.debug("Parsing pages from XML: %s", self.xml_path)
            pages = self.parserTool.get_pages_from_xml(self.xml_path, start_page, end_page)
            if pages:
                self.set_htmlbuilder()
                self.logger.debug("Extracting header and footer info...")
                self.get_page_header_footer(pages, base_name_of_file, self.output_dir)
                self.logger.debug("Processing content from pages...")
                if pdf_type == 'acts':
                    self.process_pages_acts(pdf_type)
                elif pdf_type == 'sebi_circulars':
                    self.process_pages_sebi_circulars(pdf_type)
                elif pdf_type == 'sebi':
                    self.process_pages_sebi(pdf_type)
                elif pdf_type == 'judgments':
                    self.process_pages_judgments(pdf_type)
                else:
                    self.process_pages(pdf_type)
                self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
            else:
                # if pdf_type in {'egazette'}:
                #     self.logger.info('using chrome lens for the scanned copy')
                #     self.html_builder = HTMLBuilderChromeLens(self.pdf_path)
                # else:
                    self.is_scanned_copy = True
                    self.process_scanned_copy(pdf_type, base_name_of_file,
                                              start_page, end_page)

            if pdf_type in {'egazette', 'sebi'}:
                self.write_manifest()

            return True
        except Exception as e:
            self.logger.exception("Exception occurred while parsing PDF: %s", e)
            return False

    def print_page_xml(self, pages):
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        for page_el in pages:
            raw = ET.tostring(page_el, encoding="unicode")
            pretty = minidom.parseString(raw).toprettyxml(indent="  ")
            pretty = "\n".join(
                line for line in pretty.split("\n") if line.strip()
            )
            self.logger.info(pretty)
            
    
    def escape_inline_markup(self, content):
        if not content:
            return content

        pattern = r'(?<!\\)([*_])'

        return re.sub(
            pattern,
            r'\\\1',
            content
        )
    
    # --- func for writing the html content to the desired output file ---
    def write_manifest(self):
        if not self.unique_images:
            self.logger.info("No images to build an IIIF manifest from; skipping manifest generation.")
            return None
        if self.is_url_like(self.output_dir):
            self.logger.error(
                f"[✖] output_dir ('{self.output_dir}') looks like a URL, not a local "
                f"filesystem path - skipping manifest generation. Use -pu/--public-base-url "
                f"(or PUBLIC_BASE_URL) to supply the public URL instead."
            )
            return None
        if not self.public_base_url and not os.environ.get("PUBLIC_BASE_URL"):
            self.logger.warning(
                "[!] No -pu/--public-base-url (or PUBLIC_BASE_URL env var) supplied - "
                "IIIF manifest and HTML manifest-link URLs will fall back to "
                "http://localhost:8000, which is almost certainly wrong outside local "
                "development."
            )
        try:
            output_dir = Path(self.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            # Build manifest from finalized images collected in self.unique_images
            manifest_builder = IIIFManifest(
                    Path(output_dir),
                    label=Path(self.pdf_path).stem,
                    base_prez_uri=self.public_base_url,
                    server_root=self.server_root,
                    rights=self.rights,
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    attribution=self.attribution
                )

            # Page ids come from the source XML as strings (e.g. "1", "12"), so a plain
            # lexicographic sort would order page 12 before page 2 - sort numerically
            # where possible, falling back to the raw value for anything non-numeric.
            def page_sort_key(p):
                return (0, int(p)) if str(p).isdigit() else (1, str(p))

            image_entries = []
            for meta in self.unique_images.values():
                p = meta.get('path', None)
                if not p:
                    continue
                try:
                    pp = Path(p)
                    if not pp.exists():
                        continue
                except Exception:
                    continue
                image_entries.append({
                    "path": pp,
                    # Every page this (deduplicated) image appeared on, in reading order -
                    # carried through so the manifest can label each image by its actual
                    # source page(s) instead of a meaningless "Image N" counter.
                    "pages": sorted(meta.get("pages", set()), key=page_sort_key),
                    # OCR text already extracted from the image itself (Figure.py runs a
                    # real OCR pass to decide whether to keep the image at all) - surfaced
                    # here as a IIIF "supplementing" annotation instead of being discarded
                    # after that gate check, so it isn't computed and thrown away.
                    "text": meta.get("text") or None,
                    "language": meta.get("language"),
                })

            # Present in the order the images actually appear in the source document
            # (first page each deduplicated image was seen on), not dict-insertion order.
            image_entries.sort(key=lambda e: page_sort_key(e["pages"][0]) if e["pages"] else (2, ""))

            # "Generated from" must never leak the local filesystem path (server
            # username, directory layout, repo/project structure) into what's a
            # publicly-served manifest.json - only the filename itself carries useful
            # provenance information, so strip the directory component entirely rather
            # than embedding self.pdf_path verbatim.
            manifest_path = manifest_builder.create_from_images(
                image_entries, metadata={"Generated from": Path(self.pdf_path).name}
            )
            if manifest_path:
                self.logger.info("Created IIIF manifest at %s", manifest_path)
                self.manifest_url = manifest_builder.get_manifest_uri()
            return manifest_path
        except Exception as e:
            self.logger.exception("Failed to create IIIF manifest: %s", e)
            return None

    def add_manifest_link_to_html(self, content):
        if self.pdf_type not in {'egazette', 'sebi'} or not self.manifest_url:
            return content
        link_html = (
            f'<p><a href="{self.manifest_url}" target="_blank">'
            f'click here for IIIF manifest</a></p>\n'
        )
        if '<body>' in content:
            return content.replace('<body>', '<body>\n' + link_html, 1)
        return link_html + content

    def write_html(self, content, start_page, end_page):
        if not content:
            self.logger.warning(f'HTML content not generate for pdf pdth: {self.pdf_path}')
            return
        content = self.add_manifest_link_to_html(content)
        try:
            if start_page or end_page:
                if start_page is None:
                    start_page = 1
                elif end_page is None:
                    end_page = self.total_pgs - 1 + int(start_page)
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +f"pg:{start_page}_pg:{end_page}.html"
            else:
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".html"
        except Exception as e:
            filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".html"
        try:
            output_dir = Path(self.output_dir)

            # Check if the directory exists
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created directory: {output_dir.resolve()}")
            else:
                self.logger.info(f"Directory already exists: {output_dir.resolve()}")

            # Write the HTML content to the specified file
            output_path = output_dir / filename
            with output_path.open("w", encoding="utf-8") as f:
                f.write(content)

            self.logger.info("content written successfully to %s", output_path)
        except Exception as e:
            self.logger.exception("Failed to write HTML content: %s", e)

    def write_bluebell(self, content, start_page, end_page):
        content = self.escape_inline_markup(content)
        if not content:
            self.logger.warning('Content not available to save')
            return
        try:
            if start_page or end_page:
                if start_page is None:
                    start_page = 1
                elif end_page is None:
                    end_page = self.total_pgs - 1 + int(start_page)
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +f"pg:{start_page}_pg:{end_page}.bluebell"
            else:
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".bluebell"
        except Exception as e:
            filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".bluebell"
        try:
            output_dir = Path(self.output_dir)

            # Check if the directory exists
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created directory: {output_dir.resolve()}")
            else:
                self.logger.info(f"Directory already exists: {output_dir.resolve()}")

            # Write the HTML content to the specified file
            output_path = output_dir / filename
            with output_path.open("w", encoding="utf-8") as f:
                f.write(content)

            self.logger.info("content written successfully to %s", output_path)

        except Exception as e:
            self.logger.exception("Failed to write  content: %s", e)
    
    def clear_xml_cache(self):
        if self.is_scanned_copy:
            return
        if not hasattr(self, "xml_path") or not self.xml_path:
            self.logger.warning("No xml_path attribute set for this instance")
            return
        if not os.path.exists(self.xml_path):
            self.logger.warning("XML file was not created or already deleted: %s", self.xml_path)
            return

        try:
            os.remove(self.xml_path)
            self.logger.info("Successfully removed XML file: %s", self.xml_path)
        except OSError as e:
            self.logger.error("Error deleting XML file %s: %s", self.xml_path, e)


    def get_path_cache_pdf(self):
        current_file = Path(__file__).resolve()       
        source_dir = current_file.parent.parent              
        cache_xml_dir = source_dir / "cache_pdf"      
        cache_xml_dir.mkdir(parents=True, exist_ok=True)  
        return cache_xml_dir

    def clear_cache_pdf(self):
        cache_dir = self.get_path_cache_pdf()
        if not os.path.exists(self.pdf_path):
            self.logger.warning("File was not created or already deleted: %s", self.pdf_path)
        else:
            if os.path.commonpath([os.path.abspath(self.pdf_path), os.path.abspath(cache_dir)]) == os.path.abspath(cache_dir):
                try:
                    os.remove(self.pdf_path)
                    self.logger.info("Successfully removed cached_pdf: %s", self.pdf_path)
                except OSError as e:
                    self.logger.error("Error deleting cached file %s: %s", self.pdf_path, e)
            else:
                self.logger.debug("Skipping delete, file not in cache_pdf: %s", self.pdf_path)

    def clear_ocr_engines(self):
        if self.ocr_engine == "paddleocr":
            clear_paddle_ocr_engines()
            self.logger.info("Released paddleocr model(s) held by this run")


    def detect_header_pre(self, pages):

        def parse_bbox(elem):
            try:
                return tuple(map(float, elem.attrib["bbox"].split(",")))
            except:
                return None

        def norm(txt):
            return re.sub(r"\s+", " ", txt or "").strip()

        def tl_text(tl):
            vals = []
            for t in tl.findall(".//text"):
                if t.text:
                    vals.append(t.text)
            return norm("".join(vals))

        def spaced(word):
            chars = []
            for ch in word:
                if ch.isspace():
                    chars.append(r'\s+')
                else:
                    chars.append(re.escape(ch))
            return r'\s*'.join(chars)


        def phrase(txt):
            return r'\s+'.join(spaced(x) for x in txt.split())


        months = (
            f"{spaced('January')}|{spaced('February')}|{spaced('March')}|"
            f"{spaced('April')}|{spaced('May')}|{spaced('June')}|"
            f"{spaced('July')}|{spaced('August')}|{spaced('September')}|"
            f"{spaced('October')}|{spaced('November')}|{spaced('December')}"
        )

        
        # TIER 1
        tier1 = [

            re.compile(
                rf'\n\s*({phrase("THE")}\s+)?'
                rf'({phrase("BRIEF")}\s+)?'
                rf'({phrase("REASONS FOR THE")}\s+)?'
                rf'{spaced("JUDGMENT")}\s*:?\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*({spaced("JUDGMENT")}|{spaced("JUDGEMENT")})'
                rf'\s*(\(.+\))?\s*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'^\s*({spaced("JUDGMENT")}|{spaced("JUDGEMENT")})',
                re.I
            ),

            re.compile(
                rf'\n\s*[-:]*\s*{spaced("JUDGMENT")}\s*[-:]*\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*[-:]*\s*{spaced("JUDGEMENT")}\s*[-:]*\s*\n?',
                re.I
            ),


            re.compile(
                rf'\n\s*{phrase("EX PARTE JUDGMENT")}\s*\n?',
                re.I
            ),


            re.compile(
                rf'\n\s*[-:]*\s*{spaced("\u0ca4\u0cc0\u0cb0\u0ccd\u0caa\u0cc1")}\s*[-:]*\s*\n?',   # ತೀರ್ಪು
                re.I
            ),

            re.compile(
                rf'^\s*{spaced("\u0ca4\u0cc0\u0cb0\u0ccd\u0caa\u0cc1")}\s*$',   # ತೀರ್ಪು
                re.I
            ),

            re.compile(
                rf'\n\s*[-:]*\s*{spaced("\u0906\u0926\u0947\u0936")}\s*[-:]*\s*\n?',   # आदेश
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0928\u094d\u092f\u093e\u092f\u0928\u093f\u0930\u094d\u0923\u092f")}\s*\n?',   # न्यायनिर्णय
                re.I
            ),

            
            re.compile(
                rf'\n\s*[-:]*\s*{spaced("\u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}\s*[-:]*\s*\n?',   # निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u0940 \u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}\s*\n?',   # एकतर्फी निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u093e \u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}\s*\n?',   # एकतर्फा निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("\u0935\u093e\u091f\u092a \u0906\u0926\u0947\u0936")}\s*\n?',   # वाटप आदेश
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u093e \u0906\u0926\u0947\u0936")}\s*\n?',   # एकतर्फा आदेश
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u0940 \u0906\u0926\u0947\u0936")}\s*\n?',   # एकतर्फी आदेश
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0ba4\u0bc0\u0bb0\u0bcd\u0baa\u0bc1\u0bb0\u0bc8")}\s*\n?',   # தீர்ப்புரை
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0ba4\u0bc0\u0bb0\u0bcd\u0baa\u0bcd\u0baa\u0bc1")}\s*\n?',   # தீர்ப்பு
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0b89\u0ba4\u0bcd\u0ba4\u0bbf\u0bb0\u0bb5\u0bc1")}\s*\n?',   # உத்திரவு
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0b89\u0ba4\u0bcd\u0ba4\u0bb0\u0bb5\u0bc1")}\s*\n?',   # உத்தரவு
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0d35\u0d3f\u0d27\u0d3f\u0d28\u0d4d\u0d2f\u0d3e\u0d2f\u0d02")}\s*\n?',   # വിധിന്യായം
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("\u0d09\u0d24\u0d4d\u0d24\u0d30\u0d35\u0d4d")}\s*\n?',   # ഉത്തരവ്
                re.I
            ),



            # AWARD
            re.compile(
                rf'\n\s*({phrase("FINAL AWARD")}|{phrase("INTERIM AWARD")}|{spaced("AWARD")})\s*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'\n\s*({phrase("ORAL AWARD")}|{spaced("AWARD")}\s*\(\s*{spaced("ORAL")}\s*\))',
                re.I
            ),

            # P.C.
            re.compile(
                r'\n\s*((P\s*\.?\s*C\s*\.?)|(P\s*E\s*R\s*C\s*O\s*U\s*R\s*T))\s*(:|-)?\s*\n?',
                re.I
            ),

            # ORDER
            re.compile(
                rf'\n\s*({spaced("ORDER")}|{phrase("COMMON ORDER")})'
                rf'\s*(\(.+\))?\s*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("DISTRIBUTION ORDER")}\s*\n?',
                re.I
            ),


            re.compile(
                rf'^\s*{spaced("ORDER")}',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("ORDER UNDER")}.*',
                re.I
            ),

            # ORAL ORDER / ORAL JUDGMENT
            re.compile(
                rf'\n\s*(({spaced("ORAL")}\s+({spaced("ORDER")}|{spaced("JUDGMENT")}))|'
                rf'(({spaced("ORDER")}|{spaced("JUDGMENT")})\s*\(\s*{spaced("ORAL")}\s*\)))',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("ORDER BELOW EXH")}\s*\n?',
                re.I
            ),


            # COMMON
            re.compile(
                rf'\n\s*{phrase("COMMON")}\s+'
                rf'({spaced("JUDGMENT")}|{spaced("ORDER")})\s*(\n|:)?',
                re.I
            ),
        ]

        
        # TIER 2

        tier2 = [

            # MANDEEP PANNU, J. (ORAL)
            re.compile(
                rf'\n?[A-Z .]{{3,120}},\s*J\.?\s*\(?{spaced("ORAL")}\)?\s*\n?',
                re.I
            ),

            # XYZ, J.
            re.compile(
                r',[ ]*(J|Judge|Justice|Chief Justice|C\.J\.?)'
                r'([. ]+\(?(Oral|ORAL|oral)\)?)?:?[ ]*\n?',
                re.I
            ),

            re.compile(
                r',[ \xa0]*(((C\.|J)?(J[. \r\t]+:?|Judge:)'
                r'([ ]*\(?(Oral|ORAL)\)?)?)|(J\s*\(Oral\)))'
                r'[. \r\t:]*\n?',
                re.I
            ),

            re.compile(
                r'\n\s*(Per|PER).+,\s*J\s*(:\s*)?\n?',
                re.I
            ),

            # CORAM
            re.compile(
                rf'\n\s*({spaced("PER")}|{spaced("CORAM")})\s*:\s*{spaced("JUSTICE")}.*',
                re.I
            ),

            # "HON'BLE" (straight or curly apostrophe) is the near-universal
            # spelling in Indian court captions (apostrophe elided from
            # "Honourable") - spaced("HONBLE") doesn't tolerate it, so this
            # never matched that spelling and fell through to the much
            # weaker generic comma-based fallback below.
            re.compile(
                rf"\n\s*(H\s*O\s*N\s*['’]?\s*B\s*L\s*E|{spaced('HONOURABLE')}).{{3,20}}{spaced('JUSTICE')}.*",
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("JUSTICE")}.{{0,30}}',
                re.I
            ),

            # PRESENT
            re.compile(
                rf'\n\s*{spaced("PRESENT")}\s*\n?',
                re.I
            ),

            # PRONOUNCED
            re.compile(
                rf'\n\s*({spaced("PRONOUNCED")}|{spaced("DICTATED")})'
                rf'(\s+{phrase("IN COURT")})?',
                re.I
            ),
        ]


        # TIER 3

        tier3 = [

            re.compile(
                rf'\n\s*(({spaced("MEMBER")})\s*\((J|A|T)\)|'
                rf'({spaced("CHAIRMAN")})(\s*\((A|J)\))?|'
                rf'(({phrase("JUDICIAL MEMBER")}|{phrase("ADMINISTRATIVE MEMBER")}|{phrase("TECHNICAL MEMBER")})))'
                rf'\s*:?\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("MEMBER")}[ .:-]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[ \r\t]*{spaced("PER")}[ \r\t]+.*,[ \r\t]+'
                rf'({phrase("JUDICIAL MEMBER")}|A\.?M|J\.?M|{phrase("ACCOUNTANT MEMBER")}).*',
                re.I
            ),
        ]


        # TIER 4

        tier4 = [

            # Arabic numerals
            re.compile(
                r'\n[ ]*[12]\.',
                re.I
            ),

            # Devanagari / Marathi / Hindi : १ २
            re.compile(
                r'\n[ ]*[\u0967\u0968]\.',
                re.I
            ),

            # Kannada : ೧ ೨
            re.compile(
                r'\n[ ]*[\u0ce7\u0ce8]\.',
                re.I
            ),

            # Tamil : ௧ ௨
            re.compile(
                r'\n[ ]*[\u0be7\u0be8]\.',
                re.I
            ),

            # Malayalam : ൧ ൨
            re.compile(
                r'\n[ ]*[\u0d67\u0d68]\.',
                re.I
            ),

            re.compile(
                rf'\n[ ]*((({phrase("BRIEF FACTS")})|{spaced("BACKGROUND")}|({phrase("FACTUAL BACKGROUND")}))).*',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("FACT OF THE CASE")}\s*:?\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("EXORDIUM")}\s*:?\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n[ \r]*{phrase("INFORMATION SOUGHT")}',
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("RESPONDENT")}(S)?.{{0,15}}',
                re.I
            ),
        ]

        # TIER 5

        tier5 = [

            re.compile(
                rf'\n\s*({spaced("DATED")}[\s:,]+({spaced("THE")}\s+)?)'
                rf'\d{{1,2}}\s*(th|rd|nd|st)\s+({months})[,\s]+\d{{4}}\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*{spaced("DATED")}\s*:.*',
                re.I
            ),

            re.compile(
                r'\n(\d+[ /]+)?\d+[/.-]\d+[/.-]\d+',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("ORDER DATED")}\s*:\s*\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}\s*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("ORDER DATED")}\s*:\s*\n?',
                re.I
            ),
        ]

        # TIER 6
        tier6 = [

            re.compile(
                rf'\n\s*({phrase("BY THE COURT")}|{phrase("BY COURT")}|{phrase("PER COURT")}).*',
                re.I
            ),

            re.compile(
                rf'\n\s*{phrase("FOR THE PETITIONER")}(S)?[\s.]*\n?',
                re.I
            ),

            re.compile(
                rf'\n\s*({phrase("PUBLIC PROSECUTOR")}|{phrase("FOR THE RESPONDENT")})(S)?[\s.]*\n?',
                re.I
            ),

            re.compile(
                r'\n[ \t]*([-]+|=+|\*+|[.]+)[ \t]*\n?',
                re.I
            ),
        ]


       
        # FINAL PRIORITY
        

        tiers = [tier1, tier2, tier3, tier4, tier5, tier6]

        # COLLECT ROWS


        rows = []

        for pg_idx, pg in enumerate(pages):

            page_num = pg_idx + 1

            if page_num not in self.all_pgs:
                continue

            page_obj = self.all_pgs[page_num]

            for tb, label in page_obj.all_tbs.items():

                if label is not None:
                    continue

                for tl in tb.tbox.findall(".//textline"):

                    bb = parse_bbox(tl)
                    if not bb:
                        continue

                    txt = tl_text(tl)
                    if not txt:
                        continue

                    x0, y0, x1, y1 = bb

                    rows.append({
                        "page": page_num,
                        "tb": tb,
                        "text": txt,
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                    })

        if not rows:
            return


        # top to bottom
        rows.sort(key=lambda z: (z["page"], -z["y0"], z["x0"]))


        # SEARCH TIER BY TIER

        hit = None
        hit_row = None

        for tier_idx, tier in enumerate(tiers, start=1):

            built = ""

            for i, row in enumerate(rows):

                built += "\n" + row["text"]

                matched = False

                # A real heading occupies its own row - nothing of substance
                # follows it there. A tier regex is anchored to the *start* of
                # a row (via the "\n" every row is joined with) but has no
                # matching guarantee at the other end, so it can just as
                # easily match the first word of an ordinary sentence that
                # happens to line-wrap onto its own row (e.g. a body
                # paragraph reading "judgment of the Supreme Court does not
                # apply..." - "judgment" alone satisfies the JUDGMENT tier1
                # pattern). Requiring the match to reach (within a small
                # trailing-punctuation/whitespace slack of) the end of the
                # text built so far rules that out: it forces the matched
                # row to be essentially *only* the heading, not a sentence
                # that merely starts with the heading word.
                trailing_slack = 3

                for rgx in tier:
                    m = rgx.search(built)
                    if m and (len(built) - m.end()) <= trailing_slack:
                        hit = i
                        hit_row = row
                        matched = True

                        self.logger.info(
                            f"Matched tier {tier_idx} regex: {rgx.pattern}"
                        )
                        break

                if matched:
                    break

            if hit is not None:
                break


        if hit is None:
            return

        # FIND ALL ROWS WITH EXACT SAME y0,y1 AS MATCHED ROW

        same_line_idx = set()

        target_page = hit_row["page"]
        target_y0 = hit_row["y0"]
        target_y1 = hit_row["y1"]

        for j, r in enumerate(rows):

            if r["page"] != target_page:
                continue

            if r["y0"] == target_y0 and r["y1"] == target_y1:
                same_line_idx.add(j)


        # LABEL
        # 1) all rows upto hit
        # 2) all rows having exact same y0,y1 as matched row

        mark_indexes = set(range(hit + 1)) | same_line_idx

        seen = set()

        for i in sorted(mark_indexes):

            row = rows[i]

            page_obj = self.all_pgs[row["page"]]
            tb = row["tb"]

            key = (row["page"], id(tb))

            if key in seen:
                continue

            seen.add(key)

            if page_obj.all_tbs[tb] is None:
                page_obj.all_tbs[tb] = "pre_header"

    def detect_sebi_header_pre(self, pages):
        body_start_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
        )

        rows = []
        for pg_idx, pg in enumerate(pages):
            page_num = pg_idx + 1
            if page_num not in self.all_pgs:
                continue
            page_obj = self.all_pgs[page_num]
            for tb, label in page_obj.all_tbs.items():
                if label is not None:
                    continue
                text = re.sub(r'\s+', ' ', tb.extract_text_from_tb()).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = tb.coords
                rows.append({"page": page_num, "tb": tb, "text": text, "y0": y0})

        if not rows:
            return

        rows.sort(key=lambda z: (z["page"], -z["y0"]))

        body_start_idx = None
        for i, row in enumerate(rows):
            if body_start_re.match(row["text"]):
                body_start_idx = i
                break

        if not body_start_idx:
            return

        for row in rows[:body_start_idx]:
            page_obj = self.all_pgs[row["page"]]
            if page_obj.all_tbs[row["tb"]] is None:
                page_obj.all_tbs[row["tb"]] = "pre_header"

    def detect_toc(self, pages):
        TOC_HEADING_RE = re.compile(
            r'^\s*(TABLE\s+OF\s+CONTENTS?|INDEX|CONTENTS?|SYNOPSIS|'
            r'LIST\s+OF\s+CONTENTS?|'
            r'ARRANGEMENT\s+OF\s+(?:SECTIONS?|CLAUSES?|PARAGRAPHS?|REGULATIONS?))\s*$',
            re.I
        )
        PAGE_NO_HEADER_RE = re.compile(r'^\s*PAGE\s*(?:NO\.?|NUMBER)\s*$', re.I)
        ROMAN_RE = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.I)
        TOC_ENTRY_RE = re.compile(r'^(.*?\S)[\s.․…]{2,}(\(?[A-Za-z0-9]{1,7}\)?)\.?\s*$')
        MAX_MISS_STREAK = 2
        MAX_CONTINUATION_LEN = 120
        LEVEL_TOL = 10.0
        MIN_ENTRIES = 3
        ROW_Y_TOL = 0.6

        def page_token(raw):
            token = raw.strip().strip('().').strip()
            if not token:
                return None
            if token.isdigit() and len(token) <= 4:
                return token
            if ROMAN_RE.match(token):
                return token
            return None

        def match_entry(row):
            if len(row["texts"]) > 1:
                token = page_token(row["texts"][-1])
                if token:
                    body = " ".join(row["texts"][:-1]).strip()
                    if body:
                        return body, token
            match = TOC_ENTRY_RE.match(row["text"])
            if match:
                token = page_token(match.group(2))
                if token:
                    return match.group(1).strip(), token
            return None

        rows = []
        for pg_idx, pg in enumerate(pages):
            page_num = pg_idx + 1
            if page_num not in self.all_pgs:
                continue
            page_obj = self.all_pgs[page_num]
            for tb, label in page_obj.all_tbs.items():
                if label is not None:
                    continue
                text = re.sub(r'\s+', ' ', tb.extract_text_from_tb()).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = tb.coords
                rows.append({
                    "page": page_num, "tbs": [tb], "texts": [text], "text": text,
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                })

        if not rows:
            return

        rows.sort(key=lambda z: (z["page"], -z["y0"], z["x0"]))

        merged_rows = []
        i = 0
        while i < len(rows):
            group = [rows[i]]
            j = i + 1
            while j < len(rows) and rows[j]["page"] == group[0]["page"]:
                row_height = max(group[-1]["y1"] - group[-1]["y0"], rows[j]["y1"] - rows[j]["y0"], 1.0)
                if abs(rows[j]["y0"] - group[-1]["y0"]) <= row_height * ROW_Y_TOL:
                    group.append(rows[j])
                    j += 1
                    continue
                break
            if len(group) > 1:
                merged_rows.append({
                    "page": group[0]["page"],
                    "tbs": [g["tbs"][0] for g in group],
                    "texts": [g["text"] for g in group],
                    "text": " ".join(g["text"] for g in group),
                    "x0": group[0]["x0"], "y0": group[0]["y0"],
                    "x1": group[-1]["x1"], "y1": group[0]["y1"],
                })
            else:
                merged_rows.append(rows[i])
            i = j
        rows = merged_rows

        heading_idx = None
        for idx, row in enumerate(rows):
            if any(TOC_HEADING_RE.match(t) for t in row["texts"]):
                heading_idx = idx
                break

        if heading_idx is None:
            return

        heading_row = rows[heading_idx]
        consumed_tbs = [(heading_row["page"], tb) for tb in heading_row["tbs"]]

        entries = []
        pending_prefix = ""
        pending_start_x0 = None
        pending_tbs = []
        miss_streak = 0

        i = heading_idx + 1
        while i < len(rows):
            row = rows[i]
            text = row["text"]

            if len(row["tbs"]) == 1 and PAGE_NO_HEADER_RE.match(text):
                consumed_tbs.extend((row["page"], tb) for tb in row["tbs"])
                i += 1
                continue

            matched = match_entry(row)
            if matched:
                body, page_no = matched
                if pending_prefix:
                    body = f"{pending_prefix} {body}"
                entries.append({
                    "text": body,
                    "page_no": page_no,
                    "x0": pending_start_x0 if pending_start_x0 is not None else row["x0"],
                })
                consumed_tbs.extend((row["page"], tb) for tb in row["tbs"])
                consumed_tbs.extend(pending_tbs)
                pending_prefix = ""
                pending_start_x0 = None
                pending_tbs = []
                miss_streak = 0
                i += 1
                continue

            if len(text) <= MAX_CONTINUATION_LEN and miss_streak < MAX_MISS_STREAK:
                if pending_start_x0 is None:
                    pending_start_x0 = row["x0"]
                pending_prefix = f"{pending_prefix} {text}".strip()
                pending_tbs.extend((row["page"], tb) for tb in row["tbs"])
                miss_streak += 1
                i += 1
                continue

            break

        if len(entries) < MIN_ENTRIES:
            return

        stack = []
        for entry in entries:
            x0 = entry["x0"]
            while stack and x0 < stack[-1][0] - LEVEL_TOL:
                stack.pop()
            if stack and abs(x0 - stack[-1][0]) <= LEVEL_TOL:
                level = stack[-1][1]
            elif stack and x0 > stack[-1][0] + LEVEL_TOL:
                level = stack[-1][1] + 1
                stack.append((x0, level))
            else:
                level = 1
                stack = [(x0, level)]
            entry["level"] = level

        out = [
            '<nav class="toc">',
            '<p class="toc-title">Table of Contents</p>',
            '<table class="toc-table">',
        ]
        for entry in entries:
            level = entry["level"]
            indent = f' style="padding-left: {(level - 1) * 1.5}em;"' if level > 1 else ''
            title = html.escape(entry["text"])
            page_no = html.escape(entry["page_no"])
            out.append(
                f'<tr class="toc-level-{level}">'
                f'<td class="toc-entry"{indent}>{title}</td>'
                f'<td class="toc-page">{page_no}</td></tr>'
            )
        out.append('</table>')
        out.append('</nav>')

        self.html_builder.toc_html = '\n'.join(out) + '\n'

        for page_num, tb in consumed_tbs:
            page_obj = self.all_pgs[page_num]
            if page_obj.all_tbs[tb] is None:
                page_obj.all_tbs[tb] = "toc"

# --- func to define argument parser required for the tool ---
def get_arg_parser():
    parser = argparse.ArgumentParser(description="To automate pdf Parse and Convert to structured", add_help=True)
    parser.add_argument('-i','--input-filePath',dest='input_file_path',action='store',\
                        required=True,help='mention input file path')
    parser.add_argument('-fp','--start-page',dest='start_page', action='store',\
                        type=int,required=False, default=None, help='mention start page')
    parser.add_argument('-lp','--end-page',dest='end_page', action='store',\
                        type=int,required=False, default = None, help='mention end page')
    parser.add_argument('-s', '--sidenotes', dest = 'has_sidenotes', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has sidenotes')
    parser.add_argument('-a','--amendments',dest= "is_amendment_pdf",action = "store_true",\
                        required = False,default=False, help = 'mention if pdf contains amendments')
    parser.add_argument('-l', '--loglevel', dest='loglevel', action='store',\
                        required = False, default = 'info', \
                        help='log level(error|warning|info|debug)')
    parser.add_argument('-g', '--logfile', dest='logfile', action='store',\
                        required = False, default = None, help='log file')
    parser.add_argument('-o','--output-directory',dest = "output_dir",action="store",\
                        required=True,help = "Directory to store output file")
    parser.add_argument('-x','--keep-xml',dest="keep_xml",action = "store_true",\
                        required = False, default = False, help = "saves the intermediate xml in cache_xml folder")
    parser.add_argument('-t','--type', dest= 'pdf_type', action = 'store', \
                        required = False, help= 'which helps to process and convert html type = (sebi | acts)' )
    parser.add_argument('-lm', '--line-margin', dest='line_margin', action='store', \
                        required=False, default=None, help = 'if requires, set line margin threshold for pdf miner')
    parser.add_argument('-cm', '--char-margin', dest='char_margin', action='store', \
                        required=False, default=None, help = 'if requires, set char margin threshold for pdf miner')
    parser.add_argument('-wm', '--word-margin', dest='word_margin', action='store', \
                        required=False, default=None, help = 'if requires, set word margin threshold for pdf miner')
    parser.add_argument('-de', '--doc-end', dest = 'has_doc_end', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has document end symbol (---)')
    parser.add_argument('-fnc', '--footnote-continuation', dest='is_footnote_continuation', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has footnote that continued across the pages')
    parser.add_argument('-mip', '--min-img-pixels', dest = 'min_img_pixels', action = 'store', \
                      required = False,  default = 0,  help = 'minimum pixel area threshold for initial filtering (area = dimension^2). Images are further filtered based on text content detection.')
    parser.add_argument('-ol', '--ocr-language', dest='ocr_language', action='store', \
                      required=False, default='eng', choices=TESSERACT_LANGUAGES,
                      help=f'tesseract language code for OCR (default: eng). One of: {", ".join(TESSERACT_LANGUAGES)}')
    parser.add_argument('-oe', '--ocr-engine', dest='ocr_engine', action='store', \
                      required=False, default='tesseract', choices=OCR_ENGINES_AVAILABLE,
                      help=f'OCR engine to use for figure text extraction (default: tesseract). One of: {", ".join(OCR_ENGINES_AVAILABLE)}')
    parser.add_argument('-sc', '--scanned-copy', dest = 'scanned_copy', action = 'store_true',
                        required = False, default = False, help = 'mention if the pdf copy is scanned')
    parser.add_argument('-te', '--table-extract', dest = 'table_extract', action = 'store_true',
                        required = False, default = False, help = 'mention if the pdf has borderless table or pdf is scanned copy to extract table content')
    parser.add_argument('-ftx', '--figure-text', dest = 'figure_text', action = 'store_true',
                        required = False, default = False,
                        help = 'extract OCR text for figures (checked against a fasttext language-confidence '
                               'threshold) and include it in the html output; images without confident text are '
                               'dropped. Default: keep every figure as-is, with no OCR/confidence check. Has no '
                               'effect for acts/sebi_circulars (bluebell output never includes figure text).')
    parser.add_argument('-pu', '--public-base-url', dest = 'public_base_url', action = 'store',
                        required = False, default = None,
                        help = 'public URL that --output-directory will be served from (e.g. '
                               'https://gazettes.servantsofknowledge.in/gzdl/html/andhra_extraordinary/2025-01-01), '
                               'used as the base for image/canvas/manifest URIs in the IIIF manifest (egazette/sebi types only). '
                               'Falls back to the PUBLIC_BASE_URL env var, then http://localhost:8000.')
    parser.add_argument('-sr', '--server-root', dest = 'server_root', action = 'store',
                        required = False, default = None,
                        help = 'local filesystem directory that acts as the web server\'s document root '
                               '(e.g. /var/www), used only to compute the URL path segment between '
                               '--public-base-url and "manifest/<pdfname>/..." in the IIIF manifest '
                               '(egazette/sebi types only) - never affects where output files are written. '
                               'output_dir must be located within it. If not given, output_dir itself is '
                               'assumed to be the server root (no extra path segment).')
    parser.add_argument('-rt', '--rights', dest = 'rights', action = 'store',
                        required = False, default = None,
                        help = 'IIIF manifest "rights" URI (a Creative Commons or RightsStatements.org '
                               'license URI, e.g. https://creativecommons.org/publicdomain/mark/1.0/) '
                               '(egazette/sebi types only). Omitted entirely if not supplied - never '
                               'defaulted to a guessed license.')
    parser.add_argument('-pi', '--provider-id', dest = 'provider_id', action = 'store',
                        required = False, default = None,
                        help = 'URI identifying the organization presenting the manifest (e.g. its '
                               'homepage), used for the IIIF "provider" field (egazette/sebi types only). '
                               'Requires --provider-name too to be added; ignored alone.')
    parser.add_argument('-pn', '--provider-name', dest = 'provider_name', action = 'store',
                        required = False, default = None,
                        help = 'Display name of the organization presenting the manifest, used for the '
                               'IIIF "provider" field (egazette/sebi types only). Requires --provider-id '
                               'too to be added; ignored alone.')
    parser.add_argument('-at', '--attribution', dest = 'attribution', action = 'store',
                        required = False, default = None,
                        help = 'Attribution text for the IIIF manifest\'s "requiredStatement" (egazette/sebi '
                               'types only). Omitted entirely if not supplied.')
    return parser



logformat = '%(asctime)s: %(name)s: [%(funcName)s:%(lineno)d] %(levelname)s  %(message)s'
dateformat  = '%Y-%m-%d %H:%M:%S'

def initialize_file_logging(loglevel, filepath):
    logging.basicConfig(\
        level    = loglevel,   \
        format   = logformat,  \
        datefmt  = dateformat, \
        stream   = filepath
    )

def initialize_stream_logging(loglevel = logging.INFO):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat \
    )

def setup_logging(level, filename = None):
    leveldict = {'critical': logging.CRITICAL, 'error': logging.ERROR, \
                 'warning': logging.WARNING,   'info': logging.INFO, \
                 'debug': logging.DEBUG}
    loglevel = leveldict[level]

    if filename:
        filestream = codecs.open(filename, 'w', encoding='utf8')
        initialize_file_logging(loglevel, filestream)
    else:
        initialize_stream_logging(loglevel)

if __name__ == "__main__":
    logger = logging.getLogger(__name__)

    parser = get_arg_parser()
    args = parser.parse_args()
    setup_logging(args.loglevel, filename = args.logfile)
    pdf_path = args.input_file_path
    logger.debug(f"Input PDF path attached to process-{pdf_path}")
    start_page = None
    if args.start_page:
        start_page = int(args.start_page)
    logger.debug(f"Mentioned section start page-{start_page}")
    end_page = None
    if args.end_page:
        end_page = int(args.end_page)
        logger.debug(f"Mentioned section end page-{end_page}")
    is_amendment_pdf = args.is_amendment_pdf
    logger.debug(f"Is the pdf contains amendments - {"Yes" if is_amendment_pdf else "No"}")
    has_sidenotes = args.has_sidenotes
    logger.debug(f"Is the pdf contains side notes - {"Yes" if has_sidenotes else "No"}")
    output_dir = args.output_dir
    has_doc_end = args.has_doc_end
    is_footnote_continuation = args.is_footnote_continuation
    min_img_pixels = args.min_img_pixels
    if min_img_pixels and isinstance(min_img_pixels, str):
        min_img_pixels = int(min_img_pixels)
    ocr_language = args.ocr_language
    ocr_engine = args.ocr_engine
    is_scanned_copy = args.scanned_copy
    table_extract = args.table_extract
    figure_text = args.figure_text
    public_base_url = args.public_base_url
    server_root = args.server_root
    rights = args.rights
    provider_id = args.provider_id
    provider_name = args.provider_name
    attribution = args.attribution
    main = Main(pdf_path,is_amendment_pdf,output_dir, args.pdf_type, has_sidenotes, has_doc_end,
                is_footnote_continuation, min_img_pixels, ocr_language,
                is_scanned_copy, table_extract, public_base_url, server_root,
                rights, provider_id, provider_name, attribution,
                figure_text, ocr_engine)#start,end,is_amendment_pdf,output_dir, args.pdf_type)
    # margins = compute_optimal_char_margin(pdf_path)
    char_margin = args.char_margin # str(margins)
    word_margin = args.word_margin # str(margins['word_margin'])
    line_margin = args.line_margin # str(margins['line_margin'])
    logger.info(f'char_margin : {char_margin}, word_margin: {word_margin}, line_margin: {line_margin}')
    try:
        is_success = main.parsePDF(args.pdf_type, char_margin, word_margin, line_margin, \
                                   start_page, end_page)
        if is_success:
            main.buildHTML(start_page, end_page) #end)
    finally:
        main.clear_cache_pdf()
        if not args.keep_xml:
            main.clear_xml_cache()
        main.clear_ocr_engines()