#
# Copyright (c) 2020, Quantum Espresso Foundation and SISSA.
# Internazionale Superiore di Studi Avanzati). All rights reserved.
# This file is distributed under the terms of the BSD 3-Clause license.
# See the file 'LICENSE' in the root directory of the present distribution,
# or https://opensource.org/licenses/BSD-3-Clause
#
from xmlschema.qnames import local_name
from xmlschema.namespaces import XSD_NAMESPACE

from .base import filter_method, AbstractGenerator


XSD_BUILTINS_MAP = {
    'string': 'CHARACTER(len=256)',
    'boolean': 'LOGICAL',
    'double': 'REAL(DP)',
    'integer': 'INTEGER',
    'unsignedByte': 'INTEGER',
    'nonNegativeInteger': 'INTEGER',
    'positiveInteger': 'INTEGER',
}


def get_fortran_type(xsd_type):
    if xsd_type.target_namespace == XSD_NAMESPACE:
        return XSD_BUILTINS_MAP[local_name(xsd_type.name)]
    elif xsd_type.is_simple():
        print(xsd_type)
        breakpoint()
    return xsd_type.name


class FortranGenerator(AbstractGenerator):
    """
    Fortran code generic generator for XSD schemas.
    """
    formal_language = 'Fortran'

    default_paths = ['templates/fortran/']

    builtin_types = {
        'anyType': '',
        'anySimpleType': '',
        'string': 'CHARACTER(len=256)',
        'boolean': 'LOGICAL',
        'double': 'REAL(DP)',
        'integer': 'INTEGER',
        'unsignedByte': 'INTEGER',
        'nonNegativeInteger': 'INTEGER',
        'positiveInteger': 'INTEGER',
    }
