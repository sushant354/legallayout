import xml.etree.ElementTree as ET
import subprocess
import logging

class ParserTool:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    def convert_to_xml(self,pdf_path, base_name_of_file):
        output_xml_path = f"{base_name_of_file}.xml"
        cmd = [
            "pdf2txt.py",
            "-A",
            "-t", "xml",
            "-o", output_xml_path,
            pdf_path
        ]
        try:
            subprocess.run(cmd, check=True)
            self.logger.info(f"[✔] Parse completed: {output_xml_path}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"[✖] Parse failed: {e}")
    
    def get_pages_from_xml(self,xml_path):
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            pages = root.findall(".//page")
            if not pages:
                self.logger.warning(f"No <page> elements found in XML file: {xml_path}")
            else:
                self.logger.debug(f"Collected {len(pages)} page(s) from XML: {xml_path}")
            return pages
        except ET.ParseError as e:
            self.logger.error(f"XML parsing error in file {xml_path}: {e}")
            raise
        except FileNotFoundError as e:
            self.logger.error(f"XML file not found: {xml_path}")
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error while parsing XML: {xml_path} -- {e}")
            raise
