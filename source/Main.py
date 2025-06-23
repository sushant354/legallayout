import os
from difflib import SequenceMatcher
from ParserTool import ParserTool
from Page import Page
from HTMLBuilder import HTMLBuilder
class Main:
    def __init__(self,pdfPath):
        self.pdf_path = pdfPath
        self.parserTool = ParserTool()
        self.total_pgs = 0
        self.all_pgs = {}
        self.html_builder = HTMLBuilder()
    
    def buildHTML(self):
        for page in self.all_pgs.values():
            self.html_builder.build(page)
        
        html_content = self.html_builder.get_html()
        self.write_html(html_content)


    
    # --- look for page header,footer,table,section,para of all pages ---
    def get_page_header_footer(self,pages):
        self.sorted_footer_units = []
        self.sorted_header_units = []
        self.headers_footers = []
        self.headers = []
        self.footers = []
        for pg in pages:
            page = Page(pg,self.pdf_path)
            self.total_pgs +=1
            self.all_pgs[self.total_pgs]=page
            page.process_textboxes(pg)
            page.label_table_tbs()
            page.get_section_para()
            self.contour_header_footer_of_page(page)

        self.process_footer_and_header()
        self.set_page_headers_footers()

    # --- classify the page texboxes sidenotes , titles(headings) ---
    def process_pages(self):
        for page in self.all_pgs.values():
            print("pg_num:",page.pg_num)
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            is_single_column = page.is_single_column_page()
            page.get_side_notes()
            page.get_titles()
            # page.print_table_content()
            # page.print_headers()
            # page.print_footers()
            # page.print_sidenotes()
            # page.print_titles()
            # page.print_section_para()
            # page.print_all()
            
    # --- in each page do contour to detect possible header/footer content ---
    def contour_header_footer_of_page(self,pg):
        units = []
        for tb in pg.all_tbs.keys():
            if pg.all_tbs[tb] is None:
                paragraph = tb.extract_text_from_tb()
                if not paragraph.isspace():
                    units.append({'pg_num':pg.pg_num,'tb':tb,'para':paragraph,'x0':tb.coords[0],'y0':tb.coords[1]})
                else:
                    pass
        if not units:
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

        
    #  --- Detection of proper header/footer by squence matcher across all pages ---
    def process_footer_and_header(self):
        def similar(text1, text2):
            return SequenceMatcher(None, text1, text2).ratio()
        
        MAX_HEADER_FOOTER_DEPTH = 100
        counter_in_loop_hf = 0
        while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
            units_with_same_index = []
            i_break = False
            for el in self.sorted_footer_units:
                try:
                    units_with_same_index.append(el[counter_in_loop_hf])
                except Exception as e:
                    pass
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
        #_____________
        counter_in_loop_hf = 0
        while counter_in_loop_hf < MAX_HEADER_FOOTER_DEPTH:
            units_with_same_index = []
            i_break = False
            for el in self.sorted_header_units:
                try:
                    units_with_same_index.append(el[counter_in_loop_hf])
                except Exception as e:
                    pass
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
        #------------------------------------------------------
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

            if counter_h >= 0.05 * self.total_pgs:
                self.headers.append({
                'page': int(el['page']),
                'headers': [{'para': unit['para'], 'tb': unit['tb']} for unit in el.get('header_units', [])]
                })

    # --- once detected set the header and footer of the page, apply to their page object ---
    def set_page_headers_footers(self):
        for pg in self.headers:
            for textbox in pg['headers']:
                self.all_pgs[int(pg['page'])].all_tbs[(textbox['tb'])] = "header"
        
        # for pg in self.footers:
        #     for textbox in pg['footers']:
        #         self.all_pgs[int(pg['page'])].all_tbs[(textbox['tb'])] = "footer"

        del self.sorted_footer_units
        del self.sorted_header_units
        del self.headers_footers
        del self.headers
        del self.footers

    # --- parse pdf using pdfminer to convert to XML ---       
    def parsePDF(self):
        base_name_of_file = os.path.splitext(os.path.basename(self.pdf_path))[0]
        self.parserTool.convert_to_xml(self.pdf_path,base_name_of_file)
        xml_path = f"{base_name_of_file}.xml"
        pages = self.parserTool.get_pages_from_xml(xml_path)
        self.get_page_header_footer(pages)
        self.process_pages()
    
    def write_html(self,content):
        with open("output1.html", "w", encoding="utf-8") as f:
            f.write(content)
        


if __name__ == "__main__":
    pdf_path = r'/home/barath-kumar/Downloads/209478.pdf'  #  Replace with your PDF path
    main = Main(pdf_path)
    main.parsePDF()
    main.buildHTML()
  
    