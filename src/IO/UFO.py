# -*- coding: utf-8 -*-

import os
import re as reg

from sympy import adjoint, Symbol, expand, conjugate, pi, simplify, transpose

from Definitions import splitPow, Trace

from Logging import loggingCritical

from Substitutions import getSubstitutions, doSubstitutions

class UFOExport():
    def __init__(self, model):
        self._Name = model._Name.replace('-', '').replace('+', '')
        self.model = model
        self.mapping = model.saveSettings['UFOMapping']

        self.string = ""

        # BetaFunc definition
        self.betaFactor = model.betaFactor
        self.kappa = lambda n: 1/(4*pi)**model.betaExponent(n)

        self.translation = {'GaugeCouplings': ('Gauge Couplings', 'gauge'),
                            'Yukawas': ('Yukawa Couplings', 'yuk'),
                            'QuarticTerms': ('Quartic Couplings', 'quartic'),
                            'TrilinearTerms' : ('Trilinear Couplings', 'trilinear'),
                            'ScalarMasses': ('Scalar Mass Couplings', 'scalarMass'),
                            'FermionMasses': ('Fermion Mass Couplings', 'fermionMass'),
                            'Vevs': ('Vacuum-expectation Values', 'vev')}

        self.couplingStructure = {str(model.allCouplings[k][1]): v for k,v in model.couplingStructure.items()}

        self.inconsistentRGset = (model.NonZeroCouplingRGEs != {} or model.NonZeroDiagRGEs != {})
        if self.inconsistentRGset:
            raise TypeError("     -> Error : The RGE set is inconsistent. Please refer to the latex output.")

        try:
            self.UFOsubstitutions(model, inconsistentRGEerror=True)
        except TypeError:
            raise TypeError("     -> Error : The RGE set in the Yukawa sector is inconsistent.")

        self.yukStrucGenerator = False
        self.RGEsByValue = {}
        self.prepareRGEs(model)

        self.preamble(model)
        self.writeRGEs()


    def write(self, folder):
        fileName = os.path.join(folder, 'running.py')
        try:
            self.file = open(fileName, 'w')
        except:
            exit('ERROR while creating the UFO output file')

        self.file.write(self.string)
        self.file.close()


    def preamble(self, model):
        self.string += """### UFO 'running.py' automatically generated by PyR@TE 3 ; using the FeynRules-PyR@TE Interface ###\n
import parameters as P
from object_library import all_running_elements, Running
from function_library import complexconjugate as cc
"""
        if self.yukStrucGenerator:
            self.string += """
############################################################
#     The next few lines help keep the notation compact    #
#  when products or traces of Yukawa matrices are involved #
############################################################

"""
            self.string += "from itertools import product\n\n"

            for k in self.couplingStructure:
                self.string += k + f" = lambda i,j : P.__getattribute__('{k}%dx%d' % (i+1, j+1))\n"

            self.string += """
def generateYukTerm(couplings, indices, ranges, conj=()):
    def conjugate(couplingPos):
        if couplingPos in conj:
            return lambda x: cc(x)
        return lambda x: x

    indsProduct = product(*[range(r) for r in ranges])
    return [couplings[0] + [conjugate(cPos)(c(pInd[i[0]], pInd[i[1]])) for cPos, (c, i) in enumerate(zip(couplings[1], indices))] + couplings[2] for pInd in indsProduct]
"""

    def UFOsubstitutions(self, model):
        """ Transform the UFO mapping dict into substitutions, and apply them to the RGEs """

        subsDic = {}
        yukMats = {}

        for k,v in self.mapping.items():
            if not '[' in k and not ']' in k:
                subsDic[k] = v
            else:
                # Handle Yukawa-matrix assumptions
                base = k[:k.find('[')]
                inds = eval(k.replace(base, ''))

                if base not in yukMats:
                    if base not in self.couplingStructure:
                        loggingCritical(f"Error : coupling matrix '{base}' is unknown.")
                        continue
                    yukMats[base] = (self.couplingStructure[base], {})

                if v != '0':
                    yukMats[base][1][tuple(inds)] = v

        for k, (shape, dic) in yukMats.items():
            matList = [[dic[(i,j)] if (i,j) in dic else 0 for j in range(shape[1])] for i in range(shape[0])]
            subsDic[k] = str(matList).replace("\\'", "'")

        subsDic = getSubstitutions(model, subsDic)
        doSubstitutions(model, subsDic)

    def prepareRGEs(self, model):
        """ Gather all the terms of the RGES by coupling type and by value of the numerical coeff """
        self.indexCount = 0


        def wCoeff(coeff):
            return reg.sub(r'(?<!\*\*)(\d+)',r'\1.', str(coeff), 1).replace('pi', 'cmath.pi')

        def wSymb(symb):
            s = str(symb)
            if 'conjugate' in s:
                return 'cc(P.' + s[10:]
            return 'P.' + s

        def listToString(l):
            return '[' + (wSymb(l[0]) if len(l) > 0 else '') + (', ' + ','.join([wSymb(el) for el in l[1:]]) if len(l) > 1 else '') + ']'

        for cType, loopDic in model.couplingRGEs.items():
            if 'Anomalous' in cType:
                continue

            if cType not in self.RGEsByValue:
                self.RGEsByValue[cType] = {}

            for nLoop, RGEdic in loopDic.items():
                for c, RGE in RGEdic.items():
                    RGE = expand(RGE/self.betaFactor * self.kappa(nLoop+1))
                    termsAdd = RGE.as_coeff_add()[1]

                    for term in termsAdd:
                        symbs = []
                        mats = []
                        traces = []
                        coeff = 1

                        for el in splitPow(term.args):
                            if not el.is_commutative:
                                mats.append(el)
                            elif isinstance(el, Trace):
                                traces.append(el)
                            elif not el.is_number:
                                symbs.append(el)
                            else:
                                coeff *= el

                        coeff = wCoeff(simplify(coeff))
                        if coeff not in self.RGEsByValue[cType]:
                            self.RGEsByValue[cType][coeff] = []

                        if mats == traces == []:
                            self.RGEsByValue[cType][coeff].append(listToString([c] + symbs))
                        else:
                            (pre, symbs, post), inds, ranges, cjs = self.buildIndexStructure(c, coeff, mats, traces, symbs)
                            self.RGEsByValue[cType][coeff].append('*generateYukTerm(['
                                                                    +listToString(pre)
                                                                    +', '+str(symbs)
                                                                    +', '+listToString(post)+']'
                                                                  +', '+str(inds)
                                                                  +', '+str(ranges)
                                                                  +(', '+str(cjs) if cjs != () else '')
                                                                  + ')')

    def generateIndex(self):
        self.indexCount += 1
        return self.indexCount - 1

    def buildIndexStructure(self, c, coeff, mats, traces, symbs):
        if self.yukStrucGenerator is False:
            self.yukStrucGenerator = True


        self.indexCount = 0
        allIndices = []
        allSymbs = []
        pre = []
        if c in self.couplingStructure:
            indices = (self.generateIndex(), self.generateIndex())
            allIndices = [tuple(indices)]
            allSymbs = [Symbol(c)]
        else:
            pre = [str(c)]

        conjs = []
        iPrev = ''

        for p, mat in enumerate(mats):

            if len(mats) == 1:
                inds = indices
            elif p == 0:
                iPrev = self.generateIndex()
                inds = [indices[0], iPrev]
            elif p+1 == len(mats):
                inds = [iPrev, indices[1]]
            else:
                tmp, iPrev = iPrev, self.generateIndex()
                inds = [tmp, iPrev]

            if isinstance(mat, transpose) or isinstance(mat, adjoint):
                inds = inds[::-1]
            if isinstance(mat, conjugate) or isinstance(mat, adjoint):
                conjs.append(len(allSymbs))

            symb = mat
            if isinstance(mat, transpose) or isinstance(mat, adjoint) or isinstance(mat, conjugate):
                symb = symb.args[0]

            allIndices.append(tuple(inds))
            allSymbs.append(symb)

        for tr in traces:
            iFirst = ''
            trMats = tr.args[0].args
            for p, mat in enumerate(trMats):
                if p == 0:
                    iFirst = self.generateIndex()
                    iPrev = self.generateIndex()
                    inds = [iFirst, iPrev]
                elif p+1 == len(trMats):
                    inds = [iPrev, iFirst]
                else:
                    tmp, iPrev = iPrev, self.generateIndex()
                    inds = [tmp, iPrev]

                if isinstance(mat, transpose) or isinstance(mat, adjoint):
                    inds = inds[::-1]
                if isinstance(mat, conjugate) or isinstance(mat, adjoint):
                    conjs.append(len(allSymbs))

                symb = mat
                if isinstance(mat, transpose) or isinstance(mat, adjoint) or isinstance(mat, conjugate):
                    symb = symb.args[0]

                allIndices.append(tuple(inds))
                allSymbs.append(symb)

        # Now determine the ranges
        ranges = {}
        for p, t in enumerate(allIndices):
            if t == ():
                continue

            c = str(allSymbs[p])
            struc = self.couplingStructure[c]

            for iPos, iRange in zip(t, struc):
                if iPos not in ranges:
                    ranges[iPos] = iRange
                else:
                    if iRange != ranges[iPos]:
                        loggingCritical("Error : found two contracted flavor indices with different ranges ...")
                        exit()

        ranges = tuple([ranges[i] for i in range(len(ranges))])

        return [pre, allSymbs, [str(el) for el in symbs]], allIndices, ranges, tuple(conjs)

    def writeRGEs(self):

        for cType, dic in self.RGEsByValue.items():
            self.string += '\n\n'
            strType = self.translation[cType][0]
            self.string += '#' * (len(strType)+4) + '\n'
            self.string += '# ' + self.translation[cType][0] + ' #\n'
            self.string += '#' * (len(strType)+4) + '\n'

            tName = ''
            pad1 = 0
            pad2 = 0
            for i, (val, terms) in enumerate(dic.items()):
                tName = 'R' + self.translation[cType][1] + str(i+1)
                pad1 = len(tName) + 11
                pad2 = pad1 + 16
                self.string += '\n' + tName + " = Running(name        = '" + tName + "',\n"
                self.string += ' '*pad1 + 'run_objects = [\n'

                for j, term in enumerate(terms):
                    self.string += pad2*' ' + term + (',' if j+1 != len(terms) else '') + '\n'

                self.string += ' '*(pad2-2) + '],\n'
                self.string += ' '*pad1 + 'value = ' + val + ')\n'

