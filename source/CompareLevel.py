import re
import logging


ARTICLE      = 4
DECIMAL      = 3
SMALLSTRING  = 2
GENSTRING    = 1
ROMAN        = 0
UROMAN       = -1

class CompareLevel:
    def __init__(self, val, depthType):
        self.logger = logging.getLogger(__name__)
        self.depthTypes = [depthType, -1, -1, -1, -1,-1]
        self.valnum     = [val, None, None, None, None,None]   
        self.nextvals =  self.get_next_vals()

    def get_next_vals(self):
        nextvals = {}

        try:
            nextvals[DECIMAL] = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10',\
                                '11', '12', '13', '14', '15', '16', '17', '18', '19', '20']
            nextvals[ROMAN]   = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',\
                                'xi', 'xii', 'xiii', 'xiv', 'xv', 'xvi', 'xvii', 'xviii', 'xix', 'xx']
            nextvals[SMALLSTRING]  = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l',\
                                    'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']
            nextvals[GENSTRING] = []
            for valueType in list(nextvals.keys()):
                i = 0
                x = {}
                for a in nextvals[valueType]:
                    x[a] = i
                    i+= 1
                nextvals[valueType] = x
        except Exception as e:
            self.logger.error(f"Failed in get_next_vals: {e}")
        return nextvals

    def is_next_val(self, nextval, value1, value2):
        self.logger.debug(f"Comparing: {value1} -> {value2} in nextval[{type}]")
        if value1 in nextval and value2 in nextval and nextval[value2] == nextval[value1] + 1:
            return True
        else:
            return False

    def is_roman(self, number):
        #check if a number is a roman numeral
        #reg = '^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$'
        self.logger.debug(f"Checking if value is Roman: {number}")

        if number in ['iia', 'iib', 'iic', 'iid', 'iiia', 'iiib', 'iva', 'va', 'vb', 'vc', 'vd', 'via', 'viia']:
            return True

        reg = '^(X|IX|IV|V?I{0,3})$'
        try:
            tmp = str(number)
            if re.search(reg, tmp.upper()):
                return True
            else:
                return False
        except Exception as e:
            self.logger.warning(f"Failed Roman check on {number}: {e}")
            return False

    def is_decimal(self, value):
        if re.match(r'\d+[a-zA-Z]*$', value) != None:
            return True
        else:
            return False
    
    def value_type(self, value):
        try:
            isDecimal  = self.is_decimal(value)
            if isDecimal == True:
                return DECIMAL 
            isRoman = self.is_roman(value)
            if isRoman == True:
                return ROMAN 
            elif re.match('[a-z]+$', value) != None:
                return SMALLSTRING
            else:
                return GENSTRING
        except Exception as e:
            self.logger.error(f"Failed to determine value type for {value}: {e}")
            return GENSTRING  # Fallback 
    
    # compares two section numbers and returns 
    # 0 if value1 and value2 are at the same level
    # 1 if value2 is higher in hierarchy that value1
    # -1 if value2 is lower in hierarchy than value1
    # Example: (1,a) = -1
    #          (a,2) = 1
    #          (a,b) = 0 
    def comp_special_nums(self, value1, value2):
        self.logger.debug(f"Checking special comparison: {value1} vs {value2}")
        if value1 == 'i' and value2 == 'j':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'i' and (value1 == 'h' or value1 == 'hh' or value1 == 'ha'):
            retval = (SMALLSTRING, 0) 
        elif value2 == 'x' and value1 == 'w':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'y' and value1 == 'x':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'x' and value1 == 'ix':
            retval = (ROMAN, 0) 
        elif value2 == 'xi' and value1 == 'x':
            retval = (ROMAN, 0) 
        elif value2 == 'v' and value1 == 'u':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'w' and value1 == 'v':
            retval = (SMALLSTRING, 0) 
        else:
            retval = None

        return retval

    def comp_nums(self, depth, value1, value2, valueType1):
        #print 'value1: %s type:%d value2: %s type: %d' % (value1, valueType1, value2, valueType2)
        # handle the special case of i

        self.logger.debug(f"Comparing at depth {depth}: {value1} ({valueType1}) vs {value2}")
        valueType2 = self.value_type(value2)
        if valueType1 == ARTICLE:
            compval = -1
        else:
            retval = self.comp_special_nums(value1, value2)
            if retval != None:
                (valueType2, compval) = retval
            else:
                if valueType1 == None:
                    valueType1 = self.value_type(value1)

                compval    = self.comp_level(depth, value1, value2, valueType1, valueType2)

        i = compval 
        while i < 0:
            self.depthTypes[depth-i] = -1
            self.valnum    [depth-i] = -1
            i += 1
        # store the state
        self.valnum    [depth - compval] = value2
        self.depthTypes[depth - compval] = valueType2
        return (valueType2, compval)
        

    def prev_level_match(self, value, valueType, depth):
        self.logger.debug(f"Searching previous match for: {value} of type {valueType} at depth {depth}")

        matches = []
        for i in range(0, depth):
            if valueType == self.depthTypes[i]:
                matches.append(i)

        if len(matches) <= 0:
            depthmatch = None
        else:
            finalmatch = []
            nextval    = self.nextvals[valueType]
            for match in matches:    
               if self.is_next_val(nextval, self.valnum[match], value):
                  finalmatch.append(match)
            if len(finalmatch) <= 0:
                matches.sort(reverse=True)
                depthmatch = matches[0]
            else:
                finalmatch.sort(reverse=True)
                depthmatch = finalmatch[0]
        if depthmatch == None:
            compval = None
        else:
            compval = depth - depthmatch
        return compval

    def comp_level(self, depth, value1, value2, valueType1, valueType2):
        if valueType1 == valueType2:
            compval =  0
        else:
            # its a new level if it starts with the starting of each type
            if value2 in ['A', '1', 'a']:
                compval = -1
            else:
                compval = self.prev_level_match(value2, valueType2, depth)
                if compval == None: 
                    # move up one level
                    compval = -1

        return compval

#original
# class CompareLevelSebi:
#     """
#     CompareLevel: robust hierarchical comparator and section depth helper.

#     - comp_nums(depth, value1, value2, valueType1) keeps the same return
#       semantics you already use in get_bulletins():
#         returns (valueType2, compVal)
#       where compVal == old_depth - new_depth
#         - compVal < 0 : go deeper (e.g. -1 = one level deeper)
#         - compVal == 0: same level
#         - compVal > 0 : move up (e.g. 1 = one level up)

#     - get_section_level(value) returns the 0-based level index for dotted
#       section numbers:
#         '11'       -> 0  (level1)
#         '11.1'     -> 1  (level2)
#         '11.1.1'   -> 2  (level3)
#       alphabets/roman => level index 3 (level4)
#     """

#     def __init__(self, val=None, depthType=None):
#         self.logger = logging.getLogger(__name__)
#         self.depthTypes = [depthType, -1, -1, -1, -1, -1]
#         self.valnum     = [val, None, None, None, None, None]
#         self.nextvals   = self._get_next_vals()

#     def _get_next_vals(self):
#         nextvals = {}
#         try:
#             nextvals[DECIMAL] = {str(i): i - 1 for i in range(1, 201)}
#             romans = [
#                 'i','ii','iii','iv','v','vi','vii','viii','ix','x',
#                 'xi','xii','xiii','xiv','xv','xvi','xvii','xviii','xix','xx'
#             ]
#             nextvals[ROMAN] = {v: i for i, v in enumerate(romans)}
#             nextvals[SMALLSTRING] = {chr(97 + i): i for i in range(26)}  # a..z
#             nextvals[GENSTRING] = {}
#         except Exception as e:
#             self.logger.error(f"Failed in _get_next_vals: {e}")
#         return nextvals

#     def _normalize(self, token: str) -> str:
#         if token is None:
#             return ''
#         t = token.strip()

#         # remove leading parentheses/spaces
#         t = re.sub(r'^[\s\(]+', '', t)
#         # remove trailing dots, closing paren, colons, spaces
#         t = re.sub(r'[\s\.\)\:]+$', '', t)
#         # if wrapped like "(...)", remove the wrapping now (safe after above)
#         m = re.match(r'^\((.*)\)$', t)
#         if m:
#             t = m.group(1)
#         return t

#     def _is_dotted_decimal(self, token: str) -> bool:
#         return re.fullmatch(r'\d+(?:\.\d+)*', token) is not None

#     def is_decimal(self, value: str) -> bool:
#         # keep your original-ish behavior for detection
#         try:
#             return re.match(r'^\d+[a-zA-Z]*$', value) is not None
#         except Exception:
#             return False

#     def is_roman(self, number: str) -> bool:
#         if not number:
#             return False
#         n = str(number).lower()
#         if n in ['iia','iib','iic','iid','iiia','iiib','iva','va','vb','vc','vd','via','viia']:
#             return True
#         try:
#             reg = r'^(M{0,4}(CM|CD|D?C{0,3})' \
#                   r'(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))$'
#             return re.match(reg, n.upper()) is not None
#         except Exception:
#             return False

#     def value_type(self, value: str) -> int:
#         try:
#             if value is None:
#                 return GENSTRING
#             v = self._normalize(str(value))
#             if self._is_dotted_decimal(v):
#                 return DECIMAL
#             if self.is_decimal(v):
#                 return DECIMAL
#             if self.is_roman(v):
#                 return ROMAN
#             if re.match(r'^[a-z]+$', v, re.IGNORECASE):
#                 return SMALLSTRING
#             return GENSTRING
#         except Exception as e:
#             self.logger.error(f"Failed to determine value type for {value}: {e}")
#             return GENSTRING

#     def get_section_level(self, raw_value: str) -> int:
#         v = self._normalize(raw_value)
#         if self._is_dotted_decimal(v):
#             parts = [p for p in v.split('.') if p != '']
#             return max(0, len(parts) - 1)
#         # single integer
#         if re.fullmatch(r'\d+', v):
#             return 0
#         if self.is_roman(v) or re.fullmatch(r'[A-Za-z]+', v):
#             return 3  # level4 index
#         return 0

#     def comp_special_nums(self, value1, value2):
#         self.logger.debug(f"Checking special comparison: {value1} vs {value2}")
#         if value1 == 'i' and value2 == 'j':
#             retval = (SMALLSTRING, 0)
#         elif value2 == 'i' and (value1 == 'h' or value1 == 'hh' or value1 == 'ha'):
#             retval = (SMALLSTRING, 0)
#         elif value2 == 'x' and value1 == 'w':
#             retval = (SMALLSTRING, 0)
#         elif value2 == 'y' and value1 == 'x':
#             retval = (SMALLSTRING, 0)
#         elif value2 == 'x' and value1 == 'ix':
#             retval = (ROMAN, 0)
#         elif value2 == 'xi' and value1 == 'x':
#             retval = (ROMAN, 0)
#         elif value2 == 'v' and value1 == 'u':
#             retval = (SMALLSTRING, 0)
#         elif value2 == 'w' and value1 == 'v':
#             retval = (SMALLSTRING, 0)
#         else:
#             retval = None
#         return retval

#     def prev_level_match(self, value, valueType, depth):
#         self.logger.debug(f"Searching previous match for: {value} type {valueType} at depth {depth}")
#         matches = []
#         for i in range(0, depth):
#             if valueType == self.depthTypes[i]:
#                 matches.append(i)

#         if not matches:
#             return None

#         finalmatch = []
#         nextval = self.nextvals.get(valueType, {})
#         for match in matches:
#             try:
#                 if self.is_next_val(nextval, self.valnum[match], value):
#                     finalmatch.append(match)
#             except Exception:
#                 # skip if we can't compare
#                 pass

#         depthmatch = (finalmatch or matches)[-1]
#         return depth - depthmatch

#     def is_next_val(self, nextval, value1, value2):
#         try:
#             return value1 in nextval and value2 in nextval and nextval[value2] == nextval[value1] + 1
#         except Exception:
#             return False

#     def comp_level(self, depth, value1, value2, valueType1, valueType2):
#         if valueType1 == valueType2:
#             return 0
#         # special case starting things
#         if value2 in ['A', '1', 'a']:
#             return -1
#         compval = self.prev_level_match(value2, valueType2, depth)
#         return compval if compval is not None else -1

#     def comp_nums(self, depth, value1, value2, valueType1):
#         try:
#             v1 = self._normalize(str(value1)) if value1 is not None else ''
#             v2 = self._normalize(str(value2)) if value2 is not None else ''

#             # 1) If the incoming token is a dotted numeric like 11.1.1 or 11.2
#             if self._is_dotted_decimal(v2):
#                 parts = [p for p in v2.split('.') if p != '']
#                 new_depth = max(0, len(parts) - 1)
#                 valueType2 = DECIMAL
#                 compval = depth - new_depth

#             # 2) Single integer bullet like "1" or "(1)" or "1."
#             elif re.fullmatch(r'\d+', v2):
#                 # treat numeric bullet as one level deeper than current
#                 new_depth = depth + 1
#                 valueType2 = DECIMAL
#                 compval = depth - new_depth

#             # 3) alphabetic bullets: a, aa, (a) etc. -> treat as deeper level
#             elif re.fullmatch(r'[A-Za-z]+', v2):
#                 valueType2 = SMALLSTRING
#                 if valueType1 == SMALLSTRING:
#                     # Stay on the same level (siblings)
#                     new_depth = depth
#                 else:
#                     # First alphabet after a number/roman → go one level deeper
#                     new_depth = depth + 1
#                 compval = depth - new_depth

#             # 4) roman bullets: i, iv -> treat as deeper
#             elif self.is_roman(v2):
#                 valueType2 = ROMAN
#                 new_depth = depth + 1
#                 compval = depth - new_depth

#             # 5) fallback: attempt your existing special cases or prev_level logic
#             else:
#                 retval = self.comp_special_nums(v1, v2)
#                 if retval is not None:
#                     (valueType2, compval) = retval
#                 else:
#                     if valueType1 is None:
#                         valueType1 = self.value_type(v1)
#                     valueType2 = self.value_type(v2)
#                     compval = self.comp_level(depth, v1, v2, valueType1, valueType2)

#             # Maintain same update semantics your code expects:
#             i = compval
#             while i < 0:
#                 idx = depth - i
#                 if 0 <= idx < len(self.depthTypes):
#                     self.depthTypes[idx] = -1
#                 if 0 <= idx < len(self.valnum):
#                     self.valnum[idx] = -1
#                 i += 1

#             # store the state at the resulting depth index (depth - compval)
#             store_index = depth - compval
#             if 0 <= store_index < len(self.valnum):
#                 self.valnum[store_index] = value2
#                 self.depthTypes[store_index] = valueType2

#             return (valueType2, compval)

#         except Exception as e:
#             self.logger.exception(f"comp_nums failed for '{value1}' -> '{value2}': {e}")
#             # fallback: treat as same level
#             vt = self.value_type(value2)
#             return (vt, 0)



#previous good one
class CompareLevelSebi:

    def __init__(self, val=None, depthType=None):

        self.logger = logging.getLogger(__name__)

        self.depthTypes = [depthType, -1, -1, -1, -1, -1]
        self.valnum = [val, None, None, None, None, None]

        self.roman_order = [
            "i", "ii", "iii", "iv", "v",
            "vi", "vii", "viii", "ix", "x",
            "xi", "xii", "xiii", "xiv", "xv",
            "xvi", "xvii", "xviii", "xix", "xx",
            "xxi", "xxii", "xxiii", "xxiv", "xxv",
            "xxvi", "xxvii", "xxviii", "xxix", "xxx"
        ]

        self.roman_index = {
            value: index
            for index, value in enumerate(self.roman_order)
        }

    def _normalize(self, token: str) -> str:

        if token is None:
            return ""

        t = str(token).strip()

        t = re.sub(r'^[\s\(\[]+', '', t)
        t = re.sub(r'[\s\.\)\]\:]+$', '', t)

        return t.strip()

    def is_decimal(self, value: str) -> bool:

        value = self._normalize(value)

        return re.fullmatch(
            r'\d+(?:\.\d+)*',
            value
        ) is not None

    def is_roman(self, value: str) -> bool:

        value = self._normalize(value)

        roman_re = (
            r'^(M{0,4}'
            r'(CM|CD|D?C{0,3})'
            r'(XC|XL|L?X{0,3})'
            r'(IX|IV|V?I{0,3}))$'
        )

        return re.fullmatch(
            roman_re,
            value,
            re.IGNORECASE
        ) is not None

    def is_alpha(self, value: str) -> bool:

        value = self._normalize(value)

        return re.fullmatch(
            r'[A-Za-z]+',
            value
        ) is not None

    def value_type(self, value):

        v = self._normalize(value)

        if self.is_decimal(v):
            return DECIMAL

        if self.is_roman(v):
            return ROMAN

        if self.is_alpha(v):
            return SMALLSTRING

        return GENSTRING

    def resolve_alpha_vs_roman(self, prev, curr):

        prev = self._normalize(prev).lower()
        curr = self._normalize(curr).lower()

        if curr not in self.roman_index:
            return SMALLSTRING

        # multi-char => almost always roman
        if len(curr) > 1:
            return ROMAN

        ambiguous = {"i", "v", "x"}

        if curr not in ambiguous:
            return SMALLSTRING

        # h -> i -> j
        if (
            len(prev) == 1 and
            len(curr) == 1 and
            prev.isalpha()
        ):

            if ord(curr) == ord(prev) + 1:
                return SMALLSTRING

        # roman continuation
        if prev in self.roman_index:

            if (
                self.roman_index[curr]
                ==
                self.roman_index[prev] + 1
            ):
                return ROMAN

        return ROMAN

    def is_same_family(self, v1, v2, t1, t2):

        if t1 != t2:
            return False

        # case-sensitive alpha families
        if t1 == SMALLSTRING:

            if v1.islower() != v2.islower():
                return False

        # case-sensitive roman families
        if t1 == ROMAN:

            if v1.islower() != v2.islower():
                return False

        return True

    def get_decimal_depth(self, token):

        token = self._normalize(token)

        parts = [
            p for p in token.split('.')
            if p.strip()
        ]

        return max(0, len(parts) - 1)

    def comp_nums(self, depth, value1, value2, valueType1):

        try:

            v1 = self._normalize(value1)
            v2 = self._normalize(value2)

            # -----------------------------------------
            # DECIMAL
            # -----------------------------------------

            if self.is_decimal(v2):

                valueType2 = DECIMAL

                new_depth = self.get_decimal_depth(v2)

            # -----------------------------------------
            # ALPHA / ROMAN
            # -----------------------------------------

            elif self.is_alpha(v2) or self.is_roman(v2):

                valueType2 = self.resolve_alpha_vs_roman(
                    v1,
                    v2
                )

                # same family continuation
                if self.is_same_family(
                    v1,
                    v2,
                    valueType1,
                    valueType2
                ):

                    new_depth = depth

                else:

                    # sibling restoration
                    found = False

                    for i in range(depth, -1, -1):

                        prev_type = self.depthTypes[i]
                        prev_val = self.valnum[i]

                        if prev_val is None:
                            continue

                        prev_val = self._normalize(prev_val)

                        if self.is_same_family(
                            prev_val,
                            v2,
                            prev_type,
                            valueType2
                        ):

                            new_depth = i
                            found = True
                            break

                    if not found:
                        new_depth = depth + 1

            # -----------------------------------------
            # FALLBACK
            # -----------------------------------------

            else:

                valueType2 = GENSTRING
                new_depth = depth

            compval = depth - new_depth

            store_index = max(0, new_depth)

            if store_index >= len(self.valnum):
                store_index = len(self.valnum) - 1

            self.valnum[store_index] = v2
            self.depthTypes[store_index] = valueType2

            return valueType2, compval

        except Exception as e:

            self.logger.exception(
                f"comp_nums failed for '{value1}' -> '{value2}': {e}"
            )

            return GENSTRING, 0

# import logging
# import re


# ARTICLE      = 4
# DECIMAL      = 3
# SMALLSTRING  = 2
# GENSTRING    = 1
# ROMAN        = 0
# UROMAN       = -1


# class CompareLevelSebi:

#     def __init__(self, val=None, depthType=None):

#         self.logger = logging.getLogger(__name__)

#         self.depthTypes = [depthType, -1, -1, -1, -1, -1]
#         self.valnum = [val, None, None, None, None, None]

#         self.roman_order = [
#             "i", "ii", "iii", "iv", "v",
#             "vi", "vii", "viii", "ix", "x",
#             "xi", "xii", "xiii", "xiv", "xv",
#             "xvi", "xvii", "xviii", "xix", "xx",
#             "xxi", "xxii", "xxiii", "xxiv", "xxv",
#             "xxvi", "xxvii", "xxviii", "xxix", "xxx"
#         ]

#         self.roman_index = {
#             value: idx
#             for idx, value in enumerate(self.roman_order)
#         }

#     # --------------------------------------------------
#     # normalize
#     # --------------------------------------------------

#     def _normalize(self, token):

#         if token is None:
#             return ""

#         token = str(token).strip()

#         token = re.sub(r'^[\(\[\s]+', '', token)
#         token = re.sub(r'[\)\]\.\:\s]+$', '', token)

#         return token.strip()

#     # --------------------------------------------------
#     # decimal
#     # --------------------------------------------------

#     def is_decimal(self, value):

#         value = self._normalize(value)

#         return (
#             re.fullmatch(
#                 r'\d+(?:\.\d+)*',
#                 value
#             ) is not None
#         )

#     def get_decimal_depth(self, value):

#         value = self._normalize(value)

#         return len(value.split("."))

#     # --------------------------------------------------
#     # alpha
#     # --------------------------------------------------

#     def is_alpha(self, value):

#         value = self._normalize(value)

#         return (
#             re.fullmatch(
#                 r'[A-Za-z]+',
#                 value
#             ) is not None
#         )

#     # --------------------------------------------------
#     # roman
#     # --------------------------------------------------

#     def is_lower_roman(self, value):

#         value = self._normalize(value)

#         return value.lower() in self.roman_index

#     def is_upper_roman(self, value):

#         value = self._normalize(value)

#         return (
#             value.upper() == value
#             and value.lower() in self.roman_index
#         )

#     # --------------------------------------------------
#     # type
#     # --------------------------------------------------

#     def value_type(self, value):

#         value = self._normalize(value)

#         if self.is_decimal(value):
#             return DECIMAL

#         if self.is_upper_roman(value):
#             return UROMAN

#         if self.is_lower_roman(value):
#             return ROMAN

#         if self.is_alpha(value):
#             return SMALLSTRING

#         return GENSTRING

#     # --------------------------------------------------
#     # hierarchy resolver
#     # --------------------------------------------------

#     def _hierarchy_depth(self, token):

#         token = token.strip()

#         norm = self._normalize(token)

#         # ------------------------------------------
#         # decimal hierarchy
#         # ------------------------------------------

#         if self.is_decimal(norm):

#             return min(
#                 self.get_decimal_depth(norm),
#                 4
#             ) - 1

#         # ------------------------------------------
#         # alpha hierarchy
#         # ------------------------------------------

#         if self.is_alpha(norm):

#             if self.is_upper_roman(norm):
#                 return 3

#             if self.is_lower_roman(norm):
#                 return 2

#             return 1

#         return 0

#     # --------------------------------------------------
#     # main
#     # --------------------------------------------------

#     def comp_nums(
#         self,
#         depth,
#         value1,
#         value2,
#         valueType1
#     ):

#         try:

#             valueType2 = self.value_type(value2)

#             new_depth = self._hierarchy_depth(value2)

#             compval = depth - new_depth

#             store_index = max(0, new_depth)

#             if store_index >= len(self.valnum):
#                 store_index = len(self.valnum) - 1

#             self.valnum[store_index] = value2
#             self.depthTypes[store_index] = valueType2

#             return valueType2, compval

#         except Exception as e:

#             self.logger.exception(
#                 f"comp_nums failed "
#                 f"for '{value1}' -> '{value2}': {e}"
#             )

#             return GENSTRING, 0