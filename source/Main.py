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

class Main:
    def __init__(self,pdfPath,is_amendment_pdf,output_dir, pdf_type, has_side_notes): #start,end,is_amendment_pdf,output_dir, pdf_type):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdfPath
        self.output_dir = output_dir
        # self.section_start_page = start
        # self.section_end_page = end
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
    
    def get_htmlBuilder(self, pdf_type):
        if pdf_type == 'sebi':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";') #( ".", ":", "?",  ".'", '."', ";", ";'", ';"')
            return HTMLBuilder(sentence_completion_punctutation, pdf_type)
        elif pdf_type == 'acts':
            sentence_completion_punctutation = ('.', ';', ':', '—')
            return Acts(sentence_completion_punctutation, pdf_type)
        else:
            sentence_completion_punctutation = ('.', ':')
            return HTMLBuilder(sentence_completion_punctutation, pdf_type)
        
    # --- func to build HTML after text classification ---
    def buildHTML(self): #, section_page_end):
        for page in self.all_pgs.values():
            self.logger.info(f"HTML build starts for page num-{page.pg_num}")
            self.html_builder.build(page) #, section_page_end)
        
        self.logger.debug("Fetching Full HTML content")
        if self.pdf_type != "acts":
            html_content = self.html_builder.get_html()
            self.write_html(html_content)
        else:
            content = self.html_builder.get_content()
            self.write_bluebell(content)


    
    # --- look for page header,footer,tables of all pages ---
    # def get_page_header_footer(self,pages):
    #     self.sorted_footer_units = []
    #     self.sorted_header_units = []
    #     self.headers_footers = []
    #     self.headers = []
    #     self.footers = []
    #     for pg in pages:
    #         pdf_dir = self.get_path_cache_pdf()
    #         if not self.pdf_path.lower().endswith(".pdf"):
                
    #             base_name = os.path.basename(self.pdf_path) + ".pdf"
    #             new_pdf_path = os.path.join(pdf_dir, base_name)

                
    #             shutil.copy(self.pdf_path, new_pdf_path)

    #             self.logger.debug(f"Copied input file to cache dir as: {new_pdf_path}")
    #             self.pdf_path = new_pdf_path

    #         page = Page(pg,self.pdf_path)
    #         self.total_pgs +=1
    #         self.all_pgs[self.total_pgs]=page
    #         page.process_textboxes(pg)
    #         page.label_table_tbs()
    #         self.contour_header_footer_of_page(page)
            

    #     self.process_footer_and_header()
    #     self.set_page_headers_footers()

    # --- classify the page texboxes sidenotes, section, para, titles(headings) ---
    def process_pages_acts(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            # page.print_tbs()
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            page.get_side_notes(self.has_side_notes) #self.section_start_page,self.section_end_page)
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            if self.is_amendment_pdf:
                self.amendment.check_for_amendment_acts(page)#,self.section_start_page,self.section_end_page)
            self.section_state.state = 'article'
            page.get_article(self.section_state)
            page.print_all()
            self.section_state.state = 'section'
            # page.get_section_para(self.section_state)#, self.section_start_page,self.section_end_page)
            self.section_state.state = None
            page.get_titles(pdf_type)
            page.sort_all_boxes()
            # page.print_all()
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
    # --- in each page do contour to detect possible header/footer content ---
    # def contour_header_footer_of_page(self,pg):
    #     try:
    #         units = []
    #         for tb in pg.all_tbs.keys():
    #             try:
    #                 if pg.all_tbs[tb] is None:
    #                     paragraph = tb.extract_text_from_tb()
    #                     if not paragraph.isspace():
    #                         units.append({'pg_num':pg.pg_num,'tb':tb,'para':paragraph,'x0':tb.coords[0],'y0':tb.coords[1]})
    #                     else:
    #                         continue
    #             except Exception as e:
    #                 self.logger.warning("Error extracting text or coordinates from textbox on page %d: %s", pg.pg_num, e)
    #         if not units:
    #             self.logger.info("No units detected for header/footer detection on page %s", pg.pg_num)
    #             return
            
    #         most_bottom_unit = sorted(units, key= lambda d: d['y0'], reverse=False)
    #         footer_area_units = []
    #         header_area_units = []

    #         headers = [most_bottom_unit[-1]]
    #         footers = [most_bottom_unit[0]]

    #         for ele in most_bottom_unit:
    #             smallest = most_bottom_unit[0]['y0']
    #             largest = most_bottom_unit[-1]['y0']
    #             if (ele['y0']-smallest) >= 0 and (ele['y0']- smallest) < 0.025 * pg.pg_height:
    #                 if ele['para'] != most_bottom_unit[0]['para']:
    #                     footers.append(ele)
    #                     continue
    #                 else:
    #                     continue
    #             if (largest - ele['y0']) >= 0 and (largest - ele['y0']) < 0.025* pg.pg_height:
    #                 if ele['para'] != most_bottom_unit[-1]['para']:
    #                     headers.append(ele)
    #                     continue
    #                 else:
    #                     continue
                
    #             if ele['y0'] - pg.pg_height/2 >= 0:
    #                 header_area_units.append(ele)
    #             if ele['y0'] - pg.pg_height/2 < 0:
    #                 footer_area_units.append(ele)
                
    #         header_area_units = sorted(header_area_units, key=lambda d: d['y0'], reverse=True)
    #         self.sorted_footer_units.append(footer_area_units)
    #         self.sorted_header_units.append(header_area_units)
    #         headers = sorted(headers, key=lambda d: d['x0'], reverse=False)
    #         footers = sorted(footers, key=lambda d: d['x0'], reverse=False)
    #         headers = [el for el in headers if el['para'].strip()]
    #         footers = [el for el in footers if el['para'].strip()]
    #         headers = [el for el in headers if el['para'].strip()]
    #         footers = [el for el in footers if el['para'].strip()]
    #         header = '!!??!!'.join(el['para'] for el in headers)
    #         footer = '!!??!!'.join(el['para'] for el in footers)
    #         self.headers_footers.append({
    #     'page': pg.pg_num,
    #     'header': " ".join(header.split()),
    #     'footer': " ".join(footer.split()),
    #     'header_units': headers,
    #     'footer_units': footers })
    #         self.logger.debug("Detected header/footer on page %d: header='%s' | footer='%s'", 
    #                       pg.pg_num, header[:100], footer[:100])
        
    #     except Exception as e:
    #         self.logger.exception("Error during header/footer contour detection on page %d: %s", pg.pg_num, e)


        
    # #  --- Detection of proper header/footer by squence matcher across all pages ---
    # def process_footer_and_header(self):
    #     def similar(text1, text2):
    #         try:
    #             return SequenceMatcher(None, text1, text2).ratio()
    #         except Exception as e:
    #             self.logger.warning("Similarity check failed: %s vs %s | error: %s", text1, text2, e)
    #             return 0.0

        
    #     MAX_HEADER_FOOTER_DEPTH = 100

    #     try:
    #         counter_in_loop_hf = 0
    #         while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
    #             units_with_same_index = []
    #             i_break = False
    #             for el in self.sorted_footer_units:
    #                 try:
    #                     units_with_same_index.append(el[counter_in_loop_hf])
    #                 except IndexError:
    #                     continue
    #                 except Exception as e:
    #                     self.logger.warning("Unexpected error accessing footer unit: %s", e)
    #                     continue
    #             for unitt in units_with_same_index:
    #                 similar_counter = 0
    #                 for rest in units_with_same_index:
    #                     if similar(unitt['para'],rest['para']) > 0.4:
    #                         similar_counter += 1
    #                 if similar_counter > 0.05 * self.total_pgs:
    #                     a = " ".join(unitt['para'].split())
    #                     for el in self.headers_footers:
    #                         if el['page'] == unitt['pg_num']:
    #                             el['footer'] = str(el['footer']+'!!??!!'+a)
                                
    #                 else:
    #                     i_break = True
    #             if i_break:
    #                 break
    #             counter_in_loop_hf +=1
    #     except Exception as e:
    #         self.logger.exception("Error while processing footers: %s", e)

    #     #_____________

    #     try:
    #         counter_in_loop_hf = 0
    #         while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
    #             units_with_same_index = []
    #             i_break = False
    #             for el in self.sorted_header_units:
    #                 try:
    #                     units_with_same_index.append(el[counter_in_loop_hf])
    #                 except IndexError:
    #                     continue
    #                 except Exception as e:
    #                     self.logger.warning("Unexpected error accessing header unit: %s", e)
    #                     continue
    #             for unitt in units_with_same_index:
    #                 similar_counter = 0
    #                 for rest in units_with_same_index:
    #                     if similar(unitt['para'],rest['para']) > 0.4:
    #                         similar_counter += 1
    #                 if similar_counter > 0.05 * self.total_pgs:
    #                     a = " ".join(unitt['para'].split())
    #                     for el in self.headers_footers:
    #                         if el['page'] == unitt['pg_num']:
    #                             el['header'] = str(el['header']+'!!??!!'+a)
    #                 else:
    #                     i_break = True
    #             if i_break:
    #                 break
    #             counter_in_loop_hf +=1
    #     except Exception as e:
    #         self.logger.exception("Error while processing headers: %s", e)
        
    #     #------------------------------------------------------

    #     try:
    #         for el in self.headers_footers:
    #             counter_f = 0
    #             counter_h = 0
    #             for rest in self.headers_footers:
    #                 if similar(el['footer'],rest['footer']) > 0.4:
    #                     counter_f +=1
    #             for rest in self.headers_footers:
    #                 if similar(el['header'],rest['header']) > 0.4:
    #                     counter_h +=1

    #             if counter_f >= 0.05 * self.total_pgs :
    #                 self.footers.append({
    #                     'page': int(el['page']),
    #                     'footers': [{'para': unit['para'], 'tb': unit['tb']} for unit in el.get('footer_units', [])]})
    #                 self.logger.debug("Page %d footer accepted with %d similar entries.", el['page'], counter_f)

    #             if counter_h >= 0.05 * self.total_pgs:
    #                 self.headers.append({
    #                 'page': int(el['page']),
    #                 'headers': [{'para': unit['para'], 'tb': unit['tb']} for unit in el.get('header_units', [])]
    #                 })
    #                 self.logger.debug("Page %d header accepted with %d similar entries.", el['page'], counter_h)

    #     except Exception as e:
    #         self.logger.exception("Error during final header/footer classification: %s", e)

    # # --- once detected set the header and footer of the page, apply to their page object ---
    # def set_page_headers_footers(self):
    #     try:
    #         for pg in self.headers:
    #             page_num = int(pg['page'])
    #             if page_num not in self.all_pgs:
    #                 self.logger.warning("Page %d not found in all_pgs while setting headers.", page_num)
    #                 continue

    #             for textbox in pg.get('headers', []):
    #                 tb = textbox.get('tb')
    #                 if tb in self.all_pgs[page_num].all_tbs:
    #                     self.all_pgs[page_num].all_tbs[tb] = "header"
    #                     self.logger.debug("Marked header on page %d for textbox: %s", page_num, tb)
    #                 else:
    #                     self.logger.warning("Textbox not found in page %d for header: %s", page_num, tb)
            
    #         # for pg in self.footers:
    #         #     page_num = int(pg['page'])
    #         #     if page_num not in self.all_pgs:
    #         #         self.logger.warning("Page %d not found in all_pgs while setting footers.", page_num)
    #         #         continue
    #         #     for textbox in pg.get('footers', []):
    #         #         tb = textbox.get('tb')
    #         #         if tb in self.all_pgs[page_num].all_tbs:
    #         #             self.all_pgs[page_num].all_tbs[tb] = "footer"
    #         #             self.logger.debug("Marked footer on page %d for textbox: %s", page_num, tb)
    #         #         else:
    #         #             self.logger.warning("Textbox not found in page %d for footer: %s", page_num, tb)


    #         for attr in ['sorted_footer_units', 'sorted_header_units', 'headers_footers', 'headers', 'footers']:
    #             if hasattr(self, attr):
    #                 delattr(self, attr)
    #                 self.logger.debug("Deleted attribute: %s", attr)
    #             else:
    #                 self.logger.debug("Attribute %s not found for deletion.", attr)
    #     except Exception as e:
    #         self.logger.exception("Failed during set_page_headers_footers: %s", e)

    # --- NEW ADAPTIVE HEADER/FOOTER DETECTION ---
    def get_page_header_footer(self, pages, base_name_of_file, output_dir):
        """
        New adaptive header/footer detection that replaces the old commented methods above.
        Uses percentage-based thresholds and advanced similarity matching.
        """
        # Initialize page objects first
        for pg in pages:
            pdf_dir = self.get_path_cache_pdf()
            if not self.pdf_path.lower().endswith(".pdf"):
                base_name = os.path.basename(self.pdf_path) + ".pdf"
                new_pdf_path = os.path.join(pdf_dir, base_name)
                shutil.copy(self.pdf_path, new_pdf_path)
                self.logger.debug(f"Copied input file to cache dir as: {new_pdf_path}")
                self.pdf_path = new_pdf_path

            page = Page(pg, self.pdf_path, base_name_of_file, output_dir, self.pdf_type)
            self.total_pgs += 1
            self.all_pgs[self.total_pgs] = page
            page.process_textboxes(pg)
            page.get_figures(pg)
            page.label_table_tbs()

        # Run adaptive header/footer detection
        self.logger.info("Starting adaptive header/footer detection...")
        self.adaptive_header_footer_detection(pages, self.pdf_type)

    def adaptive_header_footer_detection(self, pages, pdf_type=None):
        """
        Simple working header/footer detection - restored original approach.
        """
        self.adaptive_headers = []
        self.adaptive_footers = []
        page_elements = []
        
        # Simple working configuration
        HEADER_ZONE_THRESHOLD = 0.15    # Top 15% of page height
        FOOTER_ZONE_THRESHOLD = 0.15    # Bottom 15% of page height
        SIMILARITY_THRESHOLD =  0.8 #0.8      # 80% similarity
        MIN_OCCURRENCE_RATE =   0.4#0.4       # Must appear on at least 40% of pages
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
    
    def _basic_header_footer_filter(self, text):
        """
        Basic filtering to identify likely header/footer content.
        Less strict than the complex analysis but still effective.
        """
        import re
        
        text_lower = text.lower().strip()
        
        # Skip very long text (likely body content)
        if len(text) > 100:
            return False
        
        # Common header/footer indicators
        if re.search(r'page\s*\d+|\d+\s*page|^\d+$', text_lower):
            return True
        if re.search(r'\d{4}|copyright|©|confidential|draft', text_lower):
            return True
        if re.search(r'chapter\s*\d+|section\s*\d+', text_lower):
            return True
        # Add regex pattern for "uploaded by" text
        if re.search(r'^uploaded\s*by\s*\S*\s*', text_lower):
            return True
        # Skip obvious body content
        if re.search(r'[.!?]\s+[A-Z]', text):  # Multiple sentences
            return False
        if len(text.split()) > 8:  # More than 8 words
            return False
        
        # Default: allow it through for position-based filtering
        return True
    
    def _validate_header_footer_groups_improved(self, groups, hf_type, total_pages):
        """
        Improved validation that's less strict than the complex version but more accurate than original.
        """
        validated_groups = []
        
        for group in groups:
            elements = group['elements']
            
            # Must appear on enough pages (very permissive)
            if len(elements) < max(1, total_pages * 0.1):  # At least 10% of pages
                self.logger.debug("Group rejected: not enough pages (%d/%d)", len(elements), total_pages)
                continue
            
            # Check position consistency (more permissive)
            y_positions = [elem['y0_pct'] for elem in elements]
            y_std = self._calculate_std(y_positions)
            
            # Headers/footers should be in reasonably consistent positions
            if y_std > 0.15:  # Less than 15% position variance (increased from 8%)
                self.logger.debug("Group rejected: too much position variance (%.3f)", y_std)
                continue
            
            # Check text patterns (more permissive)
            texts = [elem['text'] for elem in elements]
            unique_texts = set(texts)
            
            # Filter out groups with too much text variation (more permissive)
            if len(unique_texts) > max(3, len(elements) * 0.6):  # Allow more variation
                self.logger.debug("Group rejected: too much text variation (%d unique texts)", len(unique_texts))
                continue
            
            # Calculate quality score (more lenient)
            position_consistency = max(0, 1 - y_std / 0.15)
            text_consistency = 1 - (len(unique_texts) - 1) / len(texts) if len(texts) > 0 else 0
            coverage_score = len(elements) / total_pages
            
            quality_score = (position_consistency * 0.3 + text_consistency * 0.3 + coverage_score * 0.4)
            
            if quality_score > 0.4:  # Lower threshold
                representative_text = max(set(texts), key=texts.count)
                group['quality_score'] = quality_score
                group['representative_text'] = representative_text
                validated_groups.append(group)
                
                self.logger.info("Validated %s: '%s' (quality=%.2f, pages=%d/%d)", 
                               hf_type, representative_text[:40], quality_score, len(elements), total_pages)
        
        # Sort by quality score
        validated_groups.sort(key=lambda g: g['quality_score'], reverse=True)
        return validated_groups
    
    def _analyze_header_footer_content(self, text):
        """
        Analyze text content to determine if it's likely to be header/footer material.
        Returns True if the text has characteristics typical of headers/footers.
        """
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
    
    def _determine_dynamic_zones(self, all_page_data, max_zone, min_zone):
        """
        Dynamically determine header and footer zones based on content distribution.
        """
        header_zones = {}
        footer_zones = {}
        
        for page_data in all_page_data:
            page_num = page_data['page_num']
            elements = page_data['elements']
            
            if not elements:
                header_zones[page_num] = 1 - max_zone
                footer_zones[page_num] = max_zone
                continue
            
            # Sort elements by vertical position
            sorted_elements = sorted(elements, key=lambda e: e['y0_pct'])
            
            # Find potential header zone (look for gaps at top)
            top_elements = [e for e in sorted_elements if e['y0_pct'] > 0.8]
            if top_elements:
                # Find the lowest header-like element
                header_candidates = [e for e in top_elements if e['is_likely_header_footer']]
                if header_candidates:
                    lowest_header = min(header_candidates, key=lambda e: e['y0_pct'])
                    header_zone = max(min_zone, min(max_zone, 1 - lowest_header['y0_pct']))
                    header_zones[page_num] = 1 - header_zone
                else:
                    header_zones[page_num] = 1 - min_zone
            else:
                header_zones[page_num] = 1 - min_zone
            
            # Find potential footer zone (look for gaps at bottom)
            bottom_elements = [e for e in sorted_elements if e['y0_pct'] < 0.2]
            if bottom_elements:
                # Find the highest footer-like element
                footer_candidates = [e for e in bottom_elements if e['is_likely_header_footer']]
                if footer_candidates:
                    highest_footer = max(footer_candidates, key=lambda e: e['y0_pct'])
                    footer_zone = max(min_zone, min(max_zone, highest_footer['y0_pct'] + highest_footer['height_pct']))
                    footer_zones[page_num] = footer_zone
                else:
                    footer_zones[page_num] = min_zone
            else:
                footer_zones[page_num] = min_zone
        
        return header_zones, footer_zones
    
    #original
    def _validate_header_footer_groups_strict(self, groups, hf_type, total_pages):
        """
        Strict validation for header/footer groups to prevent false positives.
        """
        validated_groups = []
        
        for group in groups:
            elements = group['elements']
            
            # Strict requirements
            if len(elements) < max(2, total_pages * 0.3):  # Must appear on at least 30% of pages
                continue
            
            # Check text consistency (headers/footers should be very similar)
            texts = [elem['text'] for elem in elements]
            unique_texts = set(texts)
            
            # If too many unique texts, it's probably not header/footer
            if len(unique_texts) > max(1, len(elements) * 0.3):
                continue
            
            # Check position consistency
            y_positions = [elem['y0_pct'] for elem in elements]
            y_std = self._calculate_std(y_positions)
            
            # Headers/footers should be in very consistent positions
            if y_std > 0.05:  # Less than 5% position variance
                continue
            
            # Check for content patterns that suggest it's actually header/footer
            representative_text = max(set(texts), key=texts.count)
            if not self._analyze_header_footer_content(representative_text):
                continue
            
            # Calculate quality score with stricter criteria
            position_consistency = max(0, 1 - y_std / 0.05)
            text_consistency = 1 - (len(unique_texts) - 1) / len(texts) if len(texts) > 0 else 0
            coverage_score = len(elements) / total_pages
            content_score = 1.0 if self._analyze_header_footer_content(representative_text) else 0.0
            
            quality_score = (position_consistency * 0.3 + text_consistency * 0.3 + 
                           coverage_score * 0.2 + content_score * 0.2)
            
            if quality_score > 0.7:  # High threshold for quality
                group['quality_score'] = quality_score
                group['representative_text'] = representative_text
                validated_groups.append(group)
                
                self.logger.info("Validated %s: '%s' (quality=%.2f, pages=%d)", 
                               hf_type, representative_text[:50], quality_score, len(elements))
        
        # Sort by quality score
        validated_groups.sort(key=lambda g: g['quality_score'], reverse=True)
        return validated_groups
    
    def _group_uploaded_by_patterns(self, uploaded_by_candidates, total_pages):
        """
        Special grouping for 'uploaded by' patterns with relaxed criteria.
        These should be marked as footers regardless of similarity threshold or occurrence rate.
        """
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
        """
        Special handling for single-page PDFs to avoid false positive header/footer detection.
        Uses stricter criteria focusing on content patterns and position rather than occurrence rates.
        """
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
        """
        When an 'uploaded by' pattern is found, mark textboxes above it as headers 
        and textboxes below it as footers on the same page, but only within the 
        respective header/footer threshold areas.
        """
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
        """Group similar elements across pages"""
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
    
    def _validate_header_footer_groups(self, groups, hf_type, total_pages):
        """Validate and rank header/footer groups by quality"""
        validated_groups = []
        
        for group in groups:
            # Skip validation for special pattern types like "uploaded by" and single-page patterns
            if group.get('pattern_type') in ['uploaded_by', 'single_page_header', 'single_page_footer']:
                validated_groups.append(group)
                self.logger.info("Skipped validation for '%s' pattern group (auto-approved)", group.get('pattern_type'))
                continue
                
            # Additional validation criteria
            elements = group['elements']
            
            # Check consistency of positioning
            x_positions = [elem['x0_pct'] for elem in elements]
            y_positions = [elem['y0_pct'] for elem in elements]
            
            x_std = self._calculate_std(x_positions)
            y_std = self._calculate_std(y_positions)
            
            # Check text consistency
            texts = [elem['text'] for elem in elements]
            unique_texts = len(set(texts))
            text_consistency = 1 - (unique_texts - 1) / len(texts) if len(texts) > 0 else 0
            
            # Calculate quality score
            position_consistency = max(0, 1 - (x_std + y_std) / 0.2)  # Penalize high position variance
            coverage_score = group['occurrence_rate']
            
            quality_score = (position_consistency * 0.4 + text_consistency * 0.3 + coverage_score * 0.3)
            
            self.logger.debug("Group quality: score=%.3f, pos_consistency=%.3f, text_consistency=%.3f, coverage=%.3f", 
                            quality_score, position_consistency, text_consistency, coverage_score)
            
            if quality_score > 0.3:  # Lowered threshold from 0.5 to 0.3
                group['quality_score'] = quality_score
                group['position_consistency'] = position_consistency
                group['text_consistency'] = text_consistency
                validated_groups.append(group)
                
                self.logger.debug("Validated %s group: '%s' (quality=%.2f, coverage=%.2f)", 
                                hf_type, group['representative_text'][:50], quality_score, coverage_score)
        
        # Sort by quality score (best first)
        validated_groups.sort(key=lambda g: g['quality_score'], reverse=True)
        return validated_groups
    
    def _calculate_std(self, values):
        """Calculate standard deviation of a list of values"""
        if len(values) <= 1:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5
    
    def _extend_headers_footers_by_line(self, page_elements, line_tolerance=0.02):
        """
        Extend header/footer detection to include textboxes on the same line
        as already detected headers/footers
        
        Args:
            page_elements: List of all page elements with their coordinates
            line_tolerance: Vertical tolerance for considering elements on same line (default: 2% of page height)
        """
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
        """
        Extend a specific header/footer group to include textboxes on same lines
        """
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
        """
        Check if an element is already marked as header or footer in existing groups
        """
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
        """Apply the detected headers and footers to the page objects"""
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
    def parsePDF(self, pdf_type):
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
            self.parserTool.convert_to_xml(self.pdf_path,self.xml_path, self.pdf_type)

            
            if not os.path.exists(self.xml_path):
                self.logger.error("XML file was not created: %s", self.xml_path)
                return False

            self.logger.debug("Parsing pages from XML: %s", self.xml_path)
            pages = self.parserTool.get_pages_from_xml(self.xml_path)
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
    def write_html(self, content):
        if not content:
            self.logger.warning('HTML content not available to save')
            return
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

            self.logger.info("HTML written successfully to %s", output_path)

        except Exception as e:
            self.logger.exception("Failed to write HTML content: %s", e)

    def write_bluebell(self, content):
        if not content:
            self.logger.warning('Bluebell content not available to save')
            return
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
    # parser.add_argument('-s','--section-startPage',dest='section_start_page', action='store',\
    #                     type=int,required=False,help='mention section start page if exists')
    # parser.add_argument('-e','--section-endPage',dest='section_end_page', action='store',\
    #                     type=int,required=False,help='mention section end page if exists')
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
    logger = logging.getLogger("parser-and-converter")

    parser = get_arg_parser()
    args = parser.parse_args()
    setup_logging(args.loglevel, filename = args.logfile)
    pdf_path = args.input_file_path
    logger.debug(f"Input PDF path attached to process-{pdf_path}")
    # start = args.section_start_page
    # logger.debug(f"Mentioned section start page-{start}")
    # end = args.section_end_page
    # logger.debug(f"Mentioned section end page-{end}")
    is_amendment_pdf = args.is_amendment_pdf
    logger.debug(f"Is the pdf contains amendments - {"Yes" if is_amendment_pdf else "No"}")
    has_sidenotes = args.has_sidenotes
    logger.debug(f"Is the pdf contains side notes - {"Yes" if has_sidenotes else "No"}")
    output_dir = args.output_dir
    main = Main(pdf_path,is_amendment_pdf,output_dir, args.pdf_type, has_sidenotes)#start,end,is_amendment_pdf,output_dir, args.pdf_type)
    is_success = main.parsePDF(args.pdf_type)
    if is_success:
        main.buildHTML() #end)
    main.clear_cache_pdf()
    if not args.keep_xml:
        main.clear_cache()