#
# Copyright (c) 2020, Quantum Espresso Foundation and SISSA.
# Internazionale Superiore di Studi Avanzati). All rights reserved.
# This file is distributed under the terms of the BSD 3-Clause license.
# See the file 'LICENSE' in the root directory of the present distribution,
# or https://opensource.org/licenses/BSD-3-Clause
#
import os
import sys
import inspect
import logging
from abc import ABC, ABCMeta
from fnmatch import fnmatch
from pathlib import Path
from jinja2 import Environment, ChoiceLoader, FileSystemLoader, \
    TemplateNotFound, TemplateAssertionError

import xmlschema
from xmlschema.validators import XsdType, XsdElement, XsdAttribute

from .helpers import NCNAME_PATTERN, QNAME_PATTERN, is_shell_wildcard, xsd_qname, \
    filter_method

logger = logging.getLogger('xmlschema-codegen')
logging_formatter = logging.Formatter('[%(levelname)s] %(message)s')
logging_handler = logging.StreamHandler(sys.stderr)
logging_handler.setFormatter(logging_formatter)
logger.addHandler(logging_handler)


class GeneratorMeta(ABCMeta):
    """Metaclass for creating code generators."""

    def __new__(mcs, name, bases, attrs):
        module = attrs['__module__']
        module_path = sys.modules[module].__file__

        formal_language = None
        default_paths = []
        default_filters = {}
        builtin_types = {}
        for base in bases:
            if getattr(base, 'formal_language', None):
                if formal_language is None:
                    formal_language = base.formal_language
                elif formal_language != base.formal_language:
                    msg = "Ambiguous formal_language from {!r} base classes"
                    raise ValueError(msg.format(name))

            if getattr(base, 'default_paths', None):
                default_paths.extend(base.default_paths)
            if hasattr(base, 'default_filters'):
                default_filters.update(base.default_filters)
            if getattr(base, 'builtin_types', None):
                builtin_types.update(base.builtin_types)

        if 'formal_language' not in attrs:
            attrs['formal_language'] = formal_language
        elif formal_language:
            msg = "formal_language can be defined only once for each generator class hierarchy"
            raise ValueError(msg)

        try:
            for path in attrs['default_paths']:
                if Path(path).is_absolute():
                    dirpath = Path(path)
                else:
                    dirpath = Path(module_path).parent.joinpath(path)

                if not dirpath.is_dir():
                    raise ValueError("Path {!r} is not a directory!".format(str(path)))
                default_paths.append(dirpath)

        except (KeyError, TypeError):
            pass
        else:
            attrs['default_paths'] = default_paths

        for k, v in attrs.items():
            if inspect.isfunction(v):
                if getattr(v, 'is_filter', False):
                    default_filters[k] = v
            elif inspect.isroutine(v):
                # static and class methods
                if getattr(v.__func__, 'is_filter', False):
                    default_filters[k] = v

        attrs['default_filters'] = default_filters

        try:
            for k, v in attrs['builtin_types'].items():
                builtin_types[xsd_qname(k)] = v
        except (KeyError, AttributeError):
            pass
        finally:
            if not builtin_types and not name.startswith('Abstract'):
                raise ValueError("Empty builtin_types for {}".format(name))
            attrs['builtin_types'] = builtin_types

        return type.__new__(mcs, name, bases, attrs)


class AbstractGenerator(ABC, metaclass=GeneratorMeta):
    """
    Abstract base class for code generators. A generator works using the
    Jinja2 template engine by an Environment instance.

    :param schema: the XSD schema instance.
    :param searchpath: additional search path for custom templates.
    :param filters: additional custom filter functions.
    :param types_map: a dictionary with custom mapping for XSD types.
    """
    formal_language = None
    """The formal language associated to the code generator."""

    default_paths = None
    """Default paths for templates."""

    default_filters = None
    """Default filter functions."""

    builtin_types = {
        'anyType': '',
        'anySimpleType': '',
    }
    """Translation map for XSD builtin types."""

    def __init__(self, schema, searchpath=None, filters=None, types_map=None):
        if isinstance(schema, xmlschema.XMLSchemaBase):
            self.schema = schema
        else:
            self.schema = xmlschema.XMLSchema(schema)

        self.searchpath = searchpath
        file_loaders = []
        if searchpath is not None:
            file_loaders.append(FileSystemLoader(searchpath))
        if isinstance(self.default_paths, list):
            file_loaders.extend(
                FileSystemLoader(str(path)) for path in reversed(self.default_paths)
            )

        if not file_loaders:
            raise ValueError("At least one search path required for generator instance!")
        loader = ChoiceLoader(file_loaders) if len(file_loaders) > 1 else file_loaders[0]

        self.filters = dict(self.default_filters)
        for name, func in self.default_filters.items():
            if isinstance(func, (staticmethod, classmethod)) or \
                    func.__name__ != func.__qualname__:
                # Replace unbound method with instance bound one
                self.filters[name] = getattr(self, name)
            else:
                self.filters[name] = func
        if filters:
            self.filters.update(filters)

        xsd_type_filter = '{}_type'.format(self.formal_language).lower().replace(' ', '_')
        if xsd_type_filter not in self.filters:
            self.filters[xsd_type_filter] = self.map_type

        self.types_map = self.builtin_types.copy()
        if types_map:
            if not self.schema.target_namespace:
                self.types_map.update(types_map)
            else:
                ns_part = '{%s}' % self.schema.target_namespace
                self.types_map.update((ns_part + k, v) for k, v in types_map.items())

        self._env = Environment(loader=loader)
        self._env.filters.update(self.filters)

    def __repr__(self):
        return '%s(xsd_file=%r, searchpath=%r)' % (
            self.__class__.__name__, self.xsd_file, self.searchpath
        )

    @classmethod
    def register_filter(cls, func):
        """Registers a function as default filter for the code generator."""
        cls.default_filters[func.__name__] = func
        func.is_filter = True
        return func

    @property
    def xsd_file(self):
        url = self.schema.url
        return os.path.basename(url) if url else None

    def list_templates(self, extensions=None, filter_func=None):
        return self._env.list_templates(extensions, filter_func)

    def matching_templates(self, name):
        return self._env.list_templates(filter_func=lambda x: fnmatch(x, name))

    def get_template(self, name, parent=None, globals=None):
        return self._env.get_template(name, parent, globals)

    def select_template(self, names, parent=None, globals=None):
        return self._env.select_template(names, parent, globals)

    def render(self, names, parent=None, globals=None):
        if isinstance(names, str):
            names = [names]
        elif not all(isinstance(x, str) for x in names):
            raise TypeError("'names' argument must contain only strings!")

        results = []
        for name in names:
            try:
                template = self._env.get_template(name, parent, globals)
            except TemplateNotFound as err:
                logger.debug("name %r: %s", name, str(err))
            except TemplateAssertionError as err:
                logger.warning("template %r: %s", name, str(err))
            else:
                results.append(template.render(schema=self.schema))
        return results

    def render_to_files(self, names, parent=None, globals=None, output_dir='.', force=False):
        if isinstance(names, str):
            names = [names]
        elif not all(isinstance(x, str) for x in names):
            raise TypeError("'names' argument must contain only strings!")

        template_names = []
        for name in names:
            if is_shell_wildcard(name):
                template_names.extend(self.matching_templates(name))
            else:
                template_names.append(name)

        output_dir = Path(output_dir)
        rendered = []

        for name in template_names:
            try:
                template = self._env.get_template(name, parent, globals)
            except TemplateNotFound as err:
                logger.debug("name %r: %s", name, str(err))
            except TemplateAssertionError as err:
                logger.warning("template %r: %s", name, str(err))
            else:
                output_file = output_dir.joinpath(Path(name).name).with_suffix('')
                if not force and output_file.exists():
                    continue

                result = template.render(schema=self.schema,
                                         sorted_complex_types=self.sorted_complex_types(self.schema.types))
                print(result)
                logger.info("write file %r", str(output_file))
                # with open(output_file, 'w') as fp:
                # fp.write(result)
                rendered.append(template.filename)

        return rendered

    def map_type(self, obj):
        """
        Maps an XSD type to a type declaration of the target language.

        :param obj: an XSD type or another type-related declaration as \
        an attribute or an element.
        :return: an empty string for non-XSD objects.
        """
        if isinstance(obj, XsdType):
            xsd_type = obj
        elif isinstance(obj, (XsdAttribute, XsdElement)):
            xsd_type = obj.type
        else:
            return ''

        try:
            return self.types_map[xsd_type.name]
        except KeyError:
            try:
                return self.types_map[xsd_type.base_type.name]
            except KeyError:
                if xsd_type.is_complex():
                    return self.types_map[xsd_qname('anyType')]
                else:
                    return self.types_map[xsd_qname('anySimpleType')]

    @staticmethod
    @filter_method
    def local_name(obj):
        try:
            local_name = obj.local_name
        except AttributeError:
            try:
                obj = obj.name
            except AttributeError:
                pass

            if not isinstance(obj, str):
                return ''

            try:
                if obj[0] == '{':
                    _, local_name = obj.split('}')
                elif ':' in obj:
                    prefix, local_name = obj.split(':')
                    if NCNAME_PATTERN.match(prefix) is None:
                        return ''
                else:
                    local_name = obj
            except (IndexError, ValueError):
                return ''
        else:
            if not isinstance(local_name, str):
                return ''

        if NCNAME_PATTERN.match(local_name) is None:
            return ''
        return local_name

    @staticmethod
    @filter_method
    def qname(obj):
        try:
            qname = obj.prefixed_name
        except AttributeError:
            try:
                obj = obj.name
            except AttributeError:
                pass

            if not isinstance(obj, str):
                return ''

            try:
                if obj[0] == '{':
                    _, local_name = obj.split('}')
                    return obj
                else:
                    qname = obj
            except (IndexError, ValueError):
                return ''

        if QNAME_PATTERN.match(qname) is None:
            return ''
        return qname

    @staticmethod
    @filter_method
    def tag_name(obj):
        try:
            tag = obj.tag
        except AttributeError:
            return ''

        if not isinstance(tag, str):
            return ''

        try:
            if tag[0] == '{':
                _, local_name = tag.split('}')
            else:
                local_name = tag
        except (IndexError, ValueError):
            return ''

        if NCNAME_PATTERN.match(local_name) is None:
            return ''
        return local_name

    @staticmethod
    @filter_method
    def type_name(obj):
        if isinstance(obj, XsdType):
            return obj.local_name or ''
        elif isinstance(obj, (XsdAttribute, XsdElement)):
            return obj.type.local_name or ''
        else:
            return ''

    @staticmethod
    @filter_method
    def namespace(obj):
        try:
            namespace = obj.target_namespace
        except AttributeError:
            try:
                obj = obj.name
            except AttributeError:
                pass

            try:
                if not isinstance(obj, str) or obj[0] != '{':
                    return ''
                namespace, _ = obj.split('}')
            except (IndexError, ValueError):
                return ''
        else:
            if not isinstance(namespace, str):
                return ''
        return namespace

    @staticmethod
    @filter_method
    def sorted_types(xsd_types, accept_circularity=False):
        """
        Returns a sorted sequence of XSD types. Sorted types can be used to build code declarations.

        :param xsd_types: a sequence with XSD types.
        :param accept_circularity: if set to `True` circularities are accepted. Defaults to `False`.
        :return: a list with ordered types.
        """
        try:
            xsd_types = list(xsd_types.values())
        except AttributeError:
            pass

        assert all(isinstance(x, XsdType) for x in xsd_types)
        ordered_types = [x for x in xsd_types if x.is_simple()]
        ordered_types.extend(x for x in xsd_types if x.is_complex() and x.has_simple_content())
        unordered = {x: [] for x in xsd_types if x.is_complex() and not x.has_simple_content()}

        for xsd_type in unordered:
            for e in xsd_type.content_type.iter_elements():
                if e.type in unordered:
                    unordered[xsd_type].append(e.type)

        while unordered:
            deleted = 0
            for xsd_type in xsd_types:
                if xsd_type in unordered:
                    if not unordered[xsd_type]:
                        del unordered[xsd_type]
                        ordered_types.append(xsd_type)
                        deleted += 1

            for xsd_type in unordered:
                unordered[xsd_type] = [x for x in unordered[xsd_type] if x in unordered]

            if not deleted:
                if not accept_circularity:
                    raise ValueError("Circularity found between {!r}".format(list(unordered)))
                ordered_types.extend(list(unordered))
                break

        assert len(xsd_types) == len(ordered_types)
        return ordered_types

    @classmethod
    @filter_method
    def sorted_complex_types(cls, xsd_types, accept_circularity=False):
        """Like `sorted_types` but remove simple types."""
        try:
            xsd_types = [x for x in xsd_types.values() if not x.is_simple()]
        except AttributeError:
            xsd_types = [x for x in xsd_types if not x.is_simple()]

        return cls.sorted_types(xsd_types, accept_circularity)
