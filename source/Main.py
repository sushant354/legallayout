import os
import argparse
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict
import re
import codecs
import logging
import shutil
from .ParserTool import ParserTool
from .Page import Page, SectionState
from .HTMLBuilder import HTMLBuilder
from .Acts import Acts
from .Amendment import Amendment
from .Utils import *
from .FontMapper import DynamicFontMapper

class Main:
    def __init__(self,pdfPath,is_amendment_pdf,output_dir, pdf_type, has_side_notes): #start,end,is_amendment_pdf,output_dir, pdf_type):
        self.logger = logging.getLogger('source.Main')
        self.pdf_path = pdfPath
        self.output_dir = output_dir
        self.parserTool = ParserTool()
        self.total_pgs = 0
        self.all_pgs = {}
        self.pdf_type = pdf_type  # Store pdf_type for later use
        if self.pdf_type == 'acts':
            self.html_builder = self.get_htmlBuilder(pdf_type)
        else:
            self.html_builder = self.get_htmlBuilder(pdf_type)
        self.is_amendment_pdf = is_amendment_pdf
        self.has_side_notes = has_side_notes
        self.amendment = Amendment()
        self.section_state = SectionState()
        self.article_state = SectionState()
        self.is_preamble_reached = False
        self.section_shorttitle_notend_status = False
        self.fontmapper = DynamicFontMapper(self.pdf_path, out_dir=self.output_dir)
        # self.fontmapper.extract_fonts()
        
    def get_htmlBuilder(self, pdf_type):
        if pdf_type == 'sebi':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";') #( ".", ":", "?",  ".'", '."', ";", ";'", ';"')
            return HTMLBuilder(sentence_completion_punctutation, pdf_type)
        elif pdf_type == 'acts':
            sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-')
            return Acts(sentence_completion_punctutation, pdf_type)
        else:
            sentence_completion_punctutation = ('.', ':')
            return HTMLBuilder(sentence_completion_punctutation, pdf_type)
        
    # --- func to build HTML after text classification ---
    def buildHTML(self, start_page, end_page): #, section_page_end):
        for page in self.all_pgs.values():
            self.logger.info(f"HTML build starts for page num-{page.pg_num}")
            self.html_builder.build(page, self.has_side_notes) #, section_page_end)
        
        self.logger.debug("Fetching Full HTML content")
        if self.pdf_type != "acts":
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
            self.logger.info(f'page height - {page.pg_height}, page width - {page.pg_width}, body startX - {page.body_startX}, body endX - {page.body_endX}')
            page.print_all()
            # page.print_headers()
            # page.print_footers()

    
    def process_pages_sebi(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_italic_blockquotes(pdf_type)
            self.amendment.check_for_blockquotes(page)
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
            
    def process_pages(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_titles(pdf_type)
            page.get_bulletins(self.section_state)
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

            page = Page(pg, self.pdf_path, base_name_of_file, output_dir, self.pdf_type, self.has_side_notes, self.is_amendment_pdf, self.fontmapper)
            self.total_pgs += 1
            self.all_pgs[self.total_pgs] = page
            page.process_textboxes()#pg)
            page.get_figures()#pg)
            page.label_table_tbs()

            page.line_based_header_footer_detection()
        # Run adaptive header/footer detection
        self.logger.info("Starting adaptive header/footer detection...")
        self.adaptive_header_footer_detection(pages, self.pdf_type)

    def adaptive_header_footer_detection(self, pages, pdf_type=None):
        self.adaptive_headers = []
        self.adaptive_footers = []
        page_elements = []
        
        # Simple working configuration
        HEADER_ZONE_THRESHOLD = 0.12#0.15    # Top 15% of page height
        FOOTER_ZONE_THRESHOLD = 0.12#0.15    # Bottom 15% of page height
        SIMILARITY_THRESHOLD =  0.8       # 80% similarity
        MIN_OCCURRENCE_RATE =   0.4     # Must appear on at least 40% of pages
        LINE_TOLERANCE = 0.02           # 2% of page height tolerance for same line detection
        
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
            # Apply headers
            for header_group in self.adaptive_headers:
                for element in header_group['elements']:
                    page_num = element['page_num']
                    textbox = element['textbox']
                    
                    if page_num in self.all_pgs and textbox in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[textbox] = "header"
                        self.logger.debug("Applied adaptive header on page %d: '%s'", 
                                        page_num, element['text'][:50])
            
            # Apply footers
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
            
            base_name_of_file = os.path.splitext(os.path.basename(self.pdf_path))[0]
            self.logger.info("Starting PDF parsing for: %s", self.pdf_path)
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
            self.logger.debug("Extracting header and footer info...")
            self.get_page_header_footer(pages, base_name_of_file, self.output_dir)
            self.logger.debug("Processing content from pages...")
            if pdf_type == 'acts':
                self.process_pages_acts(pdf_type)
            elif pdf_type == 'sebi':
                self.process_pages_sebi(pdf_type)
            else:
                self.process_pages(pdf_type)
            self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
            return True
        except Exception as e:
            self.logger.exception("Exception occurred while parsing PDF: %s", e)
            return False


    
    # --- func for writing the html content to the desired output file ---
    def write_html(self, content, start_page, end_page):
        if not content:
            self.logger.warning('HTML content not available to save')
            return
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
    
    def clear_cache(self):
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
    main = Main(pdf_path,is_amendment_pdf,output_dir, args.pdf_type, has_sidenotes)#start,end,is_amendment_pdf,output_dir, args.pdf_type)
    # margins = compute_optimal_char_margin(pdf_path)
    char_margin = args.char_margin # str(margins)
    word_margin = args.word_margin # str(margins['word_margin'])
    line_margin = args.line_margin # str(margins['line_margin'])
    logger.info(f'char_margin : {char_margin}, word_margin: {word_margin}, line_margin: {line_margin}')
    is_success = main.parsePDF(args.pdf_type, char_margin, word_margin, line_margin, \
                               start_page, end_page)
    if is_success:
        main.buildHTML(start_page, end_page) #end)
    main.clear_cache_pdf()
    if not args.keep_xml:
        main.clear_cache()