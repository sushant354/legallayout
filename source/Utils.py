import re

#used in
#htmlbuilder.py
RELEVANT_TAGS = {"body", "section", "p", "table", "tr", "td", "a", "blockquote", "br",
                 "h4", "center", "li"}

VOID_TAGS = {"br"}

sebi_level_close_re = re.compile(r'^(?:(?:Date|Dated)\s*[:\-]{1}\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{4})|(?:Place)\s*[:\-]{1}\s*[A-Z][A-Za-z .,&-]*|\(.*?(?:Judgment\s+pronounced|Order\s+pronounced|Decision\s+pronounced).*?\)|Sd/-)$', re.IGNORECASE)

token_end_continuation_check = ('.','?','!',';',':',":-", "---", "...", '—',':','."', ".'",';"',";'", '…', '-')

side_note_text_split = r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)'

section_subsection_split = r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)'

find_type_re = r'^\(\s*([^\s\)]+)\s*\)\s*\S*'

is_section_re = r'^\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*'




#main.py 
sentence_end_sebi_re = ("'.",'".',".'", '."', "';", ";'", ';"','";')

sentence_end_acts_re = ('.', ';', ':', '—')

sentence_end_general_re = ('.', ':')

HEADER_ZONE_THRESHOLD = 0.15  

FOOTER_ZONE_THRESHOLD = 0.15 

SIMILARITY_THRESHOLD =  0.8 

MIN_OCCURRENCE_RATE =   0.4

LINE_TOLERANCE = 0.02   

