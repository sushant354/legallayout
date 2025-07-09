import re



ARTICLE      = 4
DECIMAL      = 3
SMALLSTRING  = 2
GENSTRING    = 1
ROMAN        = 0

class CompareLevel:
    def __init__(self, val, depthType):
        self.depthTypes = [depthType, -1, -1, -1, -1,-1]
        self.valnum     = [val, None, None, None, None,None]   
        self.nextvals =  self.get_next_vals()

    def get_next_vals(self):
        nextvals = {}
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
        return nextvals

    def is_next_val(self, nextval, value1, value2):
        if value1 in nextval and value2 in nextval and nextval[value2] == nextval[value1] + 1:
            return True
        else:
            return False

    def is_roman(self, number):
        #check if a number is a roman numeral
        #reg = '^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$'
        if number in ['iia', 'iib', 'iic', 'iid', 'iiia', 'iiib', 'iva', 'va', 'vb', 'vc', 'vd', 'via', 'viia']:
            return True

        reg = '^(X|IX|IV|V?I{0,3})$'
        tmp = str(number)
        if re.search(reg, tmp.upper()):
            return True
        else:
            return False

    def is_decimal(self, value):
        if re.match('\d+[a-zA-Z]*$', value) != None:
            return True
        else:
            return False
    
    def value_type(self, value):
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
    
    # compares two section numbers and returns 
    # 0 if value1 and value2 are at the same level
    # 1 if value2 is higher in hierarchy that value1
    # -1 if value2 is lower in hierarchy than value1
    # Example: (1,a) = -1
    #          (a,2) = 1
    #          (a,b) = 0 
    def comp_special_nums(self, value1, value2):
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