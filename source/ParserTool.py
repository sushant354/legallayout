import xml.etree.ElementTree as ET
import subprocess
import logging

class ParserTool:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def add_opt(self, cmd, flag, value, pdf_type):
        if value is not None:
            cmd.append(flag)
            cmd.append(value)
            return 
        if pdf_type == 'sebi' and value is None:
            cmd.append("--char-margin")
            cmd.append("25.0")
            return

    def convert_to_xml(self,pdf_path, xml_path, pdf_type, \
                       char_margin, word_margin, line_margin):
        cmd = [
            "pdf2txt.py",
            "-A",
            "-t", "xml",
            "-o", xml_path,
        ]

        self.add_opt(cmd, '--char-margin', char_margin, pdf_type)
        self.add_opt(cmd, '--word-margin', word_margin, pdf_type)
        self.add_opt(cmd, '--line-margin', line_margin, pdf_path)
        cmd.append(pdf_path)
            
        try:
            subprocess.run(cmd, check=True)
            self.logger.info(f"[✔] Parse completed: {xml_path}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"[✖] Parse failed: {e}")
    
    def get_pages_from_xml(self,xml_path,start_page,end_page):
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            pages = root.findall(".//page")
            if not pages:
                self.logger.warning(f"No <page> elements found: {xml_path}")
                return []
            
            filtered = []
            if start_page and end_page and start_page > end_page:
                self.logger.warning(f"start_page ({start_page}) > end_page ({end_page}), swapping.")
                start_page, end_page = end_page, start_page

            for p in pages:
                num_attr = p.get("id")
                if num_attr is None:
                    continue  

                try:
                    num = int(num_attr)
                except ValueError:
                    continue 

                if (start_page is None or num >= start_page) and \
                            (end_page is None or num <= end_page):
                    filtered.append(p)

            self.logger.debug(
                f"Collected {len(filtered)} page(s) from XML: {xml_path} "
                f"(start={start_page}, end={end_page})"
            )

            return filtered
        except ET.ParseError as e:
            self.logger.error(f"XML parsing error in file {xml_path}: {e}")
            raise
        except FileNotFoundError as e:
            self.logger.error(f"XML file not found: {xml_path}")
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error while parsing XML: {xml_path} -- {e}")
            raise
