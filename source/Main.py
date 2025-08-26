import os
import argparse
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict
import re
import codecs
import logging
import shutil
from ParserTool import ParserTool
from Page import Page
from HTMLBuilder import HTMLBuilder
from Amendment import Amendment

class Main:
    def __init__(self,pdfPath,start,end,is_amendment_pdf,output_dir):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdfPath
        self.output_dir = output_dir
        self.section_start_page = start
        self.section_end_page = end
        self.parserTool = ParserTool()
        self.total_pgs = 0
        self.all_pgs = {}
        self.html_builder = HTMLBuilder()
        self.is_amendment_pdf = is_amendment_pdf

        if self.is_amendment_pdf:
            self.amendment = Amendment()
    
    # --- func to build HTML after text classification ---
    def buildHTML(self):
        for page in self.all_pgs.values():
            self.logger.info(f"HTML build starts for page num-{page.pg_num}")
            self.html_builder.build(page)
        
        self.logger.debug("Fetching Full HTML content")
        html_content = self.html_builder.get_html()
        self.write_html(html_content)


    
    # --- look for page header,footer,tables of all pages ---
    def get_page_header_footer(self,pages):
        self.sorted_footer_units = []
        self.sorted_header_units = []
        self.headers_footers = []
        self.headers = []
        self.footers = []
        for pg in pages:
            pdf_dir = self.get_path_cache_pdf()
            if not pdf_path.lower().endswith(".pdf"):
                
                base_name = os.path.basename(pdf_path) + ".pdf"
                new_pdf_path = os.path.join(pdf_dir, base_name)

                
                shutil.copy(pdf_path, new_pdf_path)

                self.logger.debug(f"Copied input file to cache dir as: {new_pdf_path}")
                self.pdf_path = new_pdf_path

            page = Page(pg,self.pdf_path)
            self.total_pgs +=1
            self.all_pgs[self.total_pgs]=page
            page.process_textboxes(pg)
            page.label_table_tbs()
            self.contour_header_footer_of_page(page)
            

        self.process_footer_and_header()
        self.set_page_headers_footers()

    # --- classify the page texboxes sidenotes, section, para, titles(headings) ---
    def process_pages(self):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            # page.print_tbs()
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            page.get_side_notes(self.section_start_page,self.section_end_page)
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            if self.is_amendment_pdf:
                self.amendment.check_for_amendments(page,self.section_start_page,self.section_end_page)
            page.get_section_para(self.section_start_page,self.section_end_page)
            page.get_titles()
            # page.print_table_content()
            # page.print_headers()
            # page.print_footers()
            # page.print_sidenotes()
            # page.print_titles()
            # page.print_section_para()
            # page.print_all()
            # page.print_amendment()
            # page.print_tbs()
            
    # --- in each page do contour to detect possible header/footer content ---
    def contour_header_footer_of_page(self,pg):
        try:
            units = []
            for tb in pg.all_tbs.keys():
                try:
                    if pg.all_tbs[tb] is None:
                        paragraph = tb.extract_text_from_tb()
                        if not paragraph.isspace():
                            units.append({'pg_num':pg.pg_num,'tb':tb,'para':paragraph,'x0':tb.coords[0],'y0':tb.coords[1]})
                        else:
                            continue
                except Exception as e:
                    self.logger.warning("Error extracting text or coordinates from textbox on page %d: %s", pg.pg_num, e)
            if not units:
                self.logger.info("No units detected for header/footer detection on page %d", pg.pg_num)
                return
            
            most_bottom_unit = sorted(units, key= lambda d: d['y0'], reverse=False)
            footer_area_units = []
            header_area_units = []

            headers = [most_bottom_unit[-1]]
            footers = [most_bottom_unit[0]]

            for ele in most_bottom_unit:
                smallest = most_bottom_unit[0]['y0']
                largest = most_bottom_unit[-1]['y0']
                if (ele['y0']-smallest) >= 0 and (ele['y0']- smallest) < 0.025 * pg.pg_height:
                    if ele['para'] != most_bottom_unit[0]['para']:
                        footers.append(ele)
                        continue
                    else:
                        continue
                if (largest - ele['y0']) >= 0 and (largest - ele['y0']) < 0.025* pg.pg_height:
                    if ele['para'] != most_bottom_unit[-1]['para']:
                        headers.append(ele)
                        continue
                    else:
                        continue
                
                if ele['y0'] - pg.pg_height/2 >= 0:
                    header_area_units.append(ele)
                if ele['y0'] - pg.pg_height/2 < 0:
                    footer_area_units.append(ele)
                
            header_area_units = sorted(header_area_units, key=lambda d: d['y0'], reverse=True)
            self.sorted_footer_units.append(footer_area_units)
            self.sorted_header_units.append(header_area_units)
            headers = sorted(headers, key=lambda d: d['x0'], reverse=False)
            footers = sorted(footers, key=lambda d: d['x0'], reverse=False)
            headers = [el for el in headers if el['para'].strip()]
            footers = [el for el in footers if el['para'].strip()]
            headers = [el for el in headers if el['para'].strip()]
            footers = [el for el in footers if el['para'].strip()]
            header = '!!??!!'.join(el['para'] for el in headers)
            footer = '!!??!!'.join(el['para'] for el in footers)
            self.headers_footers.append({
        'page': pg.pg_num,
        'header': " ".join(header.split()),
        'footer': " ".join(footer.split()),
        'header_units': headers,
        'footer_units': footers })
            self.logger.debug("Detected header/footer on page %d: header='%s' | footer='%s'", 
                          pg.pg_num, header[:100], footer[:100])
        
        except Exception as e:
            self.logger.exception("Error during header/footer contour detection on page %d: %s", pg.pg_num, e)


        
    #  --- Detection of proper header/footer by squence matcher across all pages ---
    def process_footer_and_header(self):
        def similar(text1, text2):
            try:
                return SequenceMatcher(None, text1, text2).ratio()
            except Exception as e:
                self.logger.warning("Similarity check failed: %s vs %s | error: %s", text1, text2, e)
                return 0.0

        
        MAX_HEADER_FOOTER_DEPTH = 100

        try:
            counter_in_loop_hf = 0
            while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
                units_with_same_index = []
                i_break = False
                for el in self.sorted_footer_units:
                    try:
                        units_with_same_index.append(el[counter_in_loop_hf])
                    except IndexError:
                        continue
                    except Exception as e:
                        self.logger.warning("Unexpected error accessing footer unit: %s", e)
                        continue
                for unitt in units_with_same_index:
                    similar_counter = 0
                    for rest in units_with_same_index:
                        if similar(unitt['para'],rest['para']) > 0.4:
                            similar_counter += 1
                    if similar_counter > 0.05 * self.total_pgs:
                        a = " ".join(unitt['para'].split())
                        for el in self.headers_footers:
                            if el['page'] == unitt['pg_num']:
                                el['footer'] = str(el['footer']+'!!??!!'+a)
                                
                    else:
                        i_break = True
                if i_break:
                    break
                counter_in_loop_hf +=1
        except Exception as e:
            self.logger.exception("Error while processing footers: %s", e)

        #_____________

        try:
            counter_in_loop_hf = 0
            while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
                units_with_same_index = []
                i_break = False
                for el in self.sorted_header_units:
                    try:
                        units_with_same_index.append(el[counter_in_loop_hf])
                    except IndexError:
                        continue
                    except Exception as e:
                        self.logger.warning("Unexpected error accessing header unit: %s", e)
                        continue
                for unitt in units_with_same_index:
                    similar_counter = 0
                    for rest in units_with_same_index:
                        if similar(unitt['para'],rest['para']) > 0.4:
                            similar_counter += 1
                    if similar_counter > 0.05 * self.total_pgs:
                        a = " ".join(unitt['para'].split())
                        for el in self.headers_footers:
                            if el['page'] == unitt['pg_num']:
                                el['header'] = str(el['header']+'!!??!!'+a)
                    else:
                        i_break = True
                if i_break:
                    break
                counter_in_loop_hf +=1
        except Exception as e:
            self.logger.exception("Error while processing headers: %s", e)
        
        #------------------------------------------------------

        try:
            for el in self.headers_footers:
                counter_f = 0
                counter_h = 0
                for rest in self.headers_footers:
                    if similar(el['footer'],rest['footer']) > 0.4:
                        counter_f +=1
                for rest in self.headers_footers:
                    if similar(el['header'],rest['header']) > 0.4:
                        counter_h +=1

                if counter_f >= 0.05 * self.total_pgs :
                    self.footers.append({
                        'page': int(el['page']),
                        'footers': [{'para': unit['para'], 'tb': unit['tb']} for unit in el.get('footer_units', [])]})
                    self.logger.debug("Page %d footer accepted with %d similar entries.", el['page'], counter_f)

                if counter_h >= 0.05 * self.total_pgs:
                    self.headers.append({
                    'page': int(el['page']),
                    'headers': [{'para': unit['para'], 'tb': unit['tb']} for unit in el.get('header_units', [])]
                    })
                    self.logger.debug("Page %d header accepted with %d similar entries.", el['page'], counter_h)

        except Exception as e:
            self.logger.exception("Error during final header/footer classification: %s", e)

    # --- once detected set the header and footer of the page, apply to their page object ---
    def set_page_headers_footers(self):
        # for pg in self.headers:
        #     for textbox in pg['headers']:
        #         self.all_pgs[int(pg['page'])].all_tbs[(textbox['tb'])] = "header"
        
        # for pg in self.footers:
        #     for textbox in pg['footers']:
        #         self.all_pgs[int(pg['page'])].all_tbs[(textbox['tb'])] = "footer"

        try:
            for pg in self.headers:
                page_num = int(pg['page'])
                if page_num not in self.all_pgs:
                    self.logger.warning("Page %d not found in all_pgs while setting headers.", page_num)
                    continue

                for textbox in pg.get('headers', []):
                    tb = textbox.get('tb')
                    if tb in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[tb] = "header"
                        self.logger.debug("Marked header on page %d for textbox: %s", page_num, tb)
                    else:
                        self.logger.warning("Textbox not found in page %d for header: %s", page_num, tb)
            
            # for pg in self.footers:
            #     page_num = int(pg['page'])
            #     if page_num not in self.all_pgs:
            #         self.logger.warning("Page %d not found in all_pgs while setting footers.", page_num)
            #         continue
            #     for textbox in pg.get('footers', []):
            #         tb = textbox.get('tb')
            #         if tb in self.all_pgs[page_num].all_tbs:
            #             self.all_pgs[page_num].all_tbs[tb] = "footer"
            #             self.logger.debug("Marked footer on page %d for textbox: %s", page_num, tb)
            #         else:
            #             self.logger.warning("Textbox not found in page %d for footer: %s", page_num, tb)


            for attr in ['sorted_footer_units', 'sorted_header_units', 'headers_footers', 'headers', 'footers']:
                if hasattr(self, attr):
                    delattr(self, attr)
                    self.logger.debug("Deleted attribute: %s", attr)
                else:
                    self.logger.debug("Attribute %s not found for deletion.", attr)
        except Exception as e:
            self.logger.exception("Failed during set_page_headers_footers: %s", e)

    
    def get_path_cache_xml(self):
        current_file = Path(__file__).resolve()       
        source_dir = current_file.parent.parent              
        cache_xml_dir = source_dir / "cache_xml"      
        cache_xml_dir.mkdir(parents=True, exist_ok=True)  
        return cache_xml_dir

    # --- parse pdf using pdfminer to convert to XML ---       
    def parsePDF(self):
        try:
            base_name_of_file = os.path.splitext(os.path.basename(self.pdf_path))[0]
            self.logger.info("Starting PDF parsing for: %s", self.pdf_path)
            cache_xml_path = self.get_path_cache_xml()
            self.xml_path =  cache_xml_path / f"{base_name_of_file}.xml"
            self.logger.debug("Converting PDF to XML...")
            self.parserTool.convert_to_xml(self.pdf_path,self.xml_path)

            
            if not os.path.exists(self.xml_path):
                self.logger.error("XML file was not created: %s", self.xml_path)
                return

            self.logger.debug("Parsing pages from XML: %s", self.xml_path)
            pages = self.parserTool.get_pages_from_xml(self.xml_path)
            self.logger.debug("Extracting header and footer info...")
            self.get_page_header_footer(pages)
            self.logger.debug("Processing content from pages...")
            self.process_pages()
            self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
        except Exception as e:
            self.logger.exception("Exception occurred while parsing PDF: %s", e)


    
    # --- func for writing the html content to the desired output file ---
    def write_html(self, content):
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
    
    def clear_cache(self):
        if not os.path.exists(self.xml_path):
            self.logger.warning("XML file was not created or already deleted: %s", self.xml_path)
        else:
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
                    print("i am here")
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
    parser.add_argument('-s','--section-startPage',dest='section_start_page', action='store',\
                        type=int,required=False,help='mention section start page if exists')
    parser.add_argument('-e','--section-endPage',dest='section_end_page', action='store',\
                        type=int,required=False,help='mention section end page if exists')
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
    start = args.section_start_page
    logger.debug(f"Mentioned section start page-{start}")
    end = args.section_end_page
    logger.debug(f"Mentioned section end page-{end}")
    is_amendment_pdf = args.is_amendment_pdf
    logger.debug(f"Is the pdf contains amendments - {"Yes" if is_amendment_pdf else "No"}")
    output_dir = args.output_dir
    main = Main(pdf_path,start,end,is_amendment_pdf,output_dir)
    main.parsePDF()
    main.buildHTML()
    main.clear_cache_pdf()
    if not args.keep_xml:
        main.clear_cache()
  
    