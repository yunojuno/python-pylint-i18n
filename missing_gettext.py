# -*- coding: utf-8 -*-

""" :class:`MissingGettextChecker`, extenting ``FormatChecker`` of PyLint. """

# pylint: disable=E0611
# pylint: disable=F0401
# pylint: disable=W0401
# pylint: disable=W0611
# pylint: disable=W0614
# pylint: disable=W0622

# TODO: get ride of this - this is the module that we're currently creating
# pylint: disable=W9903

import re
import string
import tokenize

from os import getenv, path

import astroid
from astroid.node_classes import *

try:
    from pylint.interfaces import IAstroidChecker
except ImportError:
    # fallback to older pylint naming
    from pylint.interfaces import IASTNGChecker as IAstroidChecker

from pylint.checkers.format import FormatChecker, ContinuedLineState

try:
    # ``urllib.parse`` is new (since Python 3.3), so may not be available to
    # everyone.
    import urllib.parse
    _PARSE_URL = True
except ImportError:
    _PARSE_URL = False

SELECTED_QUOTED_STRINGS_TO_IGNORE = getenv(
    'SELECTED_QUOTED_STRINGS_TO_IGNORE', []
)

SINGLE_QUOTED_STRING_REGEX = r"^(?:\')\s*([\w\@\.\-]+)\s*(?:\')"
DOUBLE_QUOTED_STRING_REGEX = r'^(?:\")\s*([\w\@\.\-]+)\s*(?:\")'
UNICODE_MULTILINE_REGEX_FLAG = re.MULTILINE + re.UNICODE
UNNAMED_POSITIONAL_PLACEHOLDER_REGEX = r'(\%\w|\{\})'

GETTEXT_FUNCTION_NAMES = [
    '_',
    '_lazy',
    'ugettext',
    'ugettext_lazy'
    'ungettext',
    'ungettext_lazy',
    'gettext_noop'
]


def is_number(text):
    """Returns True if this text is a representation of a number"""
    try:
        float(text)
        return True
    except ValueError:
        return False


def is_child_node(child, parent):
    """Returns True if child is an eventual child node of parent"""
    node = child
    while node is not None:
        if node == parent:
            return True
        node = node.parent
    return False


def _is_str(obj):
    """
    Is this a string or a unicode string?
    """
    if isinstance(obj, str):
        return True
    try:
        if isinstance(obj, unicode):  # pylint: disable=E0602
            return True
    except NameError:  # unicode not defined in Python 3
        pass
    return False


def _is_url(text):
    """
    Test if ``text`` seems to be an URL, using ``urllib.parse`` if available,
    a fall‑back otherwise. The fall back test common protocol prefixes
    and filname extensions.

    :param str text:
    :rtype: bool

    Note: this just test URL, not general URI.

    Examples which will return ``True``:

     * ``"file://localhost"``
     * ``"file://localhost/document.txt"``
     * ``"file:///document.txt"``
     * ``"file:///"``

    Samples which will return ``False``:

     * ``"document.txt"``
     * ``"/document.txt"``
     * ``"file://"``

    """

    if _PARSE_URL:
        url = urllib.parse.urlparse(text)
        has_scheme = url.scheme != ''
        has_netloc = url.netloc != ''
        has_path = url.path != ''
        result = has_scheme and (has_netloc or has_path)
    else:
        # Fall‑back for when ``urllib`` is not available.

        def strictly_starts_with(text, prefix):
            """Test if ``prefix`` is a prefix of ``text``, and there is
            more after."""
            return text.startswith(prefix) and (text != prefix)

        def strictly_ends_with(text, suffix):
            """Test if ``suffix`` is a suffix of ``text``, and there
            is more before."""
            return text.startswith(suffix) and (text != suffix)

        protocols = ['file', 'ftp', 'http', 'https', 'sftp', 'ssh']
        extensions = ['asp', 'html', 'php', 'xhtml']

        result = False

        for protocol in protocols:
            if strictly_starts_with(text, protocol + '://'):
                result = True
                break

        if not result:
            for extension in extensions:
                if strictly_ends_with(text, '.' + extension):
                    result = True
                    break

    return result


def _is_path(text):
    """
    Test if ``text`` seems to be an URL, using ``urllib.parse`` if available,
    a fall‑back otherwise. The fall back test common protocol prefixes
    and filname extensions.

    :param str text:
    :rtype: bool

    This is not intended to be reliable for other mean than telling if
    whether or not, the string is translatable. Don't use this function
    for other purpose, as it may return a lot of false negative.

    Examples which will return ``True``:

     * ``"~/document.txt"``
     * ``"../document.txt"``
     * ``"something/.."``
     * ``"parent/../child"``

    Samples which will return ``False``:

     * ``"parent/child"``
     * ``"document.txt"``

    """

    result = False

    if path.expanduser(text) != text:
        # Expands ``$HOME`` on Windows, but not on UNIces. Still don't
        # use ``path.expandvars``, as this expands everything,
        # including what's not really related t paths.
        result = True
    elif text.find('./') != -1:
        # Testing ``./`` includes testing ``../``.
        result = True
    elif text.find('/.') != -1:
        # Same comment as above.
        result = True

    return result


class MissingGettextChecker(FormatChecker):

    """
    Checks for strings that aren't wrapped in a _ call somewhere
    """

    name = 'missing_gettext'
    msgs = {
        'W9903': ('non-gettext-ed string %r',
                  'no-gettext',
                  "There is a raw string that's not passed through gettext"),
        'W9912': ('Possible key/const wrapped in double quotes %r',
                  'double-quotes-key',
                  'Keys/constants should be wrapped in single quotes'),
        'W9913': ('Variable placeholder in string lacks name %r',
                  'positional-placeholder',
                  'All variable placeholders should be named'),
    }
    # options = []
    options = (
        (
            'whitelist-single-quoted',
            {
                'default': False,
                'type': 'yn',
                'metavar': '<y_or_n>',
                'help': (
                    'Whitelist strings that look like keys but'
                    ' warn if wrapped in double quotes'
                )
            }
        ),
        (
            'check-string-placeholders',
            {
                'default': False,
                'type': 'yn',
                'metavar': '<y_or_n>',
                'help': (
                    'Warn about positional (ie, not named) '
                    'placeholders in strings'
                )
            }
        ),
    )

    # this is important so that your checker is executed before others
    priority = -1

    def visit_const(self, node):
        if not _is_str(node.value):
            return

        # Ignore some strings based on the contents.
        # Each element of this list is a one argument function. if any of them
        # return true for this string, then this string is ignored
        whitelisted_strings = [
            # ignore empty strings
            lambda x: x == '',

            # This string is probably used as a key or something, and should
            # be ignored
            lambda x: len(x) > 3 and x.upper() == x,

            # pure number
            is_number,

            # URL, can't be translated
            _is_url,

            # Paths, usually can't be translated
            _is_path,

            # probably a regular expression
            lambda x: x.startswith("^") and x.endswith("$"),

            # probably a URL fragment
            lambda x: x.startswith("/") and x.endswith("/"),

            # Only has format specifiers and non-letters, so ignore it
            lambda x:(not any([z in x.replace("%s", "").replace("%d", "")
                      for z in string.ascii_letters])),

            # sending http attachment header
            lambda x: x.startswith("attachment; filename="),

            # sending http header
            lambda x: x.startswith("text/html; charset="),

        ]

        if self.config.whitelist_single_quoted:
            whitelisted_strings.append(
                lambda x: x in getattr(self, 'tokenizer_whitelist', [])
            )
        elif SELECTED_QUOTED_STRINGS_TO_IGNORE:
            # still allow excluding just some strings
            whitelisted_strings.append(
                lambda x: x in SELECTED_QUOTED_STRINGS_TO_IGNORE
            )

        for func in whitelisted_strings:
            if func(node.value):
                return

        # Whitelist some strings based on the structure.
        # Each element of this list is a 2-tuple, class and then a 2 arg
        # function. Starting with the current string, and going up the parse
        # tree to the root (i.e. the whole file), for every whitelist element,
        # if the current node is an instance of the first element, then the
        # 2nd element is called with that node and the original string. If
        # that returns True, then this string is assumed to be OK.
        # If any parent node of this string returns True for any of these
        # functions then the string is assumed to be OK
        whitelist = [
            # {'shouldignore': 1}
            (Dict,
             lambda curr_node,
             node: node in [x[0] for x in curr_node.items]),

            # dict['shouldignore']
            (Index, lambda curr_node, node: curr_node.value == node),

            # list_display = [....]
            # e.g. Django Admin class Meta:...
            (Assign,
             lambda curr_node,
             node: (len(curr_node.targets) == 1
                    and hasattr(curr_node.targets[0], 'name')
                    and curr_node.targets[0].name in [
                        'list_display', 'js', 'css', 'fields', 'exclude',
                        'list_filter', 'list_display_links', 'ordering',
                        'search_fields', 'actions', 'unique_together',
                        'db_table', 'custom_filters', 'search_fields',
                        'custom_date_list_filters', 'export_fields',
                        'date_hierarchy'])),

            # Just a random doc-string-esque string in the code
            (Discard, lambda curr_node, node: curr_node.value == node),

            # X(attrs={'class': 'somecssclass', 'maxlength': '20'})
            (Keyword,
             lambda curr_node,
             node: (curr_node.arg == 'attrs'
                    and hasattr(curr_node.value, 'items')
                    and node in [x[1] for x in curr_node.value.items
                                 if x[0].value in [
                                 'class', 'maxlength', 'cols', 'rows',
                                 'checked', 'disabled', 'readonly']])),
            # X(attrs=dict(....))
            (Keyword,
             lambda curr_node,
             node: (curr_node.arg == 'attrs'
                    and isinstance(curr_node.value, CallFunc)
                    and hasattr(curr_node.value.func, 'name')
                    and curr_node.value.func.name == 'dict')),
            # x = CharField(default='xxx', related_name='tickets') etc.
            (Keyword,
             lambda curr_node,
             node: (curr_node.arg in [
                    'regex', 'prefix', 'css_class', 'mimetype',
                    'related_name', 'default', 'initial', 'upload_to']
                    and curr_node.value == node)),
            (Keyword,
             lambda curr_node,
             node: (curr_node.arg in ['input_formats']
                    and len(curr_node.value.elts) == 1
                    and curr_node.value.elts[0] == node)),
            (Keyword,
             lambda curr_node,
             node: (curr_node.arg in ['fields']
                    and node in curr_node.value.elts)),
            # something() == 'string'
            (Compare, lambda curr_node, node: node == curr_node.ops[0][1]),
            # 'something' == blah()
            (Compare, lambda curr_node, node: node == curr_node.left),

            # Try to exclude queryset.extra(something=[..., 'some sql',...]
            (CallFunc,
             lambda curr_node,
             node: (curr_node.func.attrname in ['extra']
                    and any(is_child_node(node, x) for x in curr_node.args))),

            # Queryset functions, queryset.order_by('shouldignore')
            (CallFunc,
             lambda curr_node,
             node: (isinstance(curr_node.func, Getattr)
                    and curr_node.func.attrname in [
                    'has_key', 'pop', 'order_by', 'strftime', 'strptime',
                    'get', 'select_related', 'values', 'filter',
                    'values_list'])),
            # logging.info('shouldignore')
            (CallFunc,
             lambda curr_node,
             node: curr_node.func.expr.name in ['logging']),


            # hasattr(..., 'should ignore')
            # HttpResponseRedirect('/some/url/shouldnt/care')
            # first is function name, 2nd is the position the string must be
            # in (none to mean don't care)
            (CallFunc,
             lambda curr_node,
             node: (curr_node.func.name in ['hasattr', 'getattr']
                    and curr_node.args[1] == node)),
            (CallFunc,
             lambda curr_node,
             node: (curr_node.func.name in [
                    'HttpResponseRedirect', 'HttpResponse'])),
            (CallFunc,
             lambda curr_node,
             node: (curr_node.func.name == 'set_cookie'
                    and curr_node.args[0] == node)),
            (CallFunc,
             lambda curr_node,
             node: (curr_node.func.name in ['ForeignKey', 'OneToOneField']
                    and curr_node.args[0] == node)),
        ]

        string_ok = False

        debug = False
        # debug = True
        curr_node = node
        if debug:
            import pdb
            pdb.set_trace()

        # we have a string. Go upwards to see if we have a _ function call
        try:
            while curr_node.parent is not None:
                if debug:
                    print(repr(curr_node))
                    print(repr(curr_node.as_string()))
                    print(curr_node.repr_tree())
                if isinstance(curr_node, CallFunc):
                    if (hasattr(curr_node, 'func')
                            and hasattr(curr_node.func, 'name')):
                        if (curr_node.func.name in GETTEXT_FUNCTION_NAMES):
                            # we're in a _() call
                            string_ok = True
                            break

                # Look at our whitelist
                for cls, func in whitelist:
                    if isinstance(curr_node, cls):
                        try:
                            # Ignore any errors from here. Otherwise we have to
                            # pepper the whitelist with loads of defensive
                            # hasattrs, which increase bloat
                            if func(curr_node, node):
                                string_ok = True
                                break
                        except AttributeError:
                            pass

                curr_node = curr_node.parent

        except Exception as error:  # pylint: disable=W0703
            print(node, node.as_string())
            print(curr_node, curr_node.as_string())
            print(error)
            import pdb
            pdb.set_trace()

        if not string_ok:
            # we've gotten to the top of the code tree / file level and we
            # haven't been whitelisted,fi so add an error here
            self.add_message('W9903', line=node.fromlineno, args=(node.value, ))

    def process_tokens(self, tokens):

        # TODO: Initialising all the below might not be needed:
        self._bracket_stack = [None]
        indents = [0]
        check_equal = False
        line_num = 0
        self._lines = {}
        self._visited_lines = {}
        token_handlers = self._prepare_token_dispatcher()
        self._last_line_ending = None
        last_blank_line_num = 0
        # TODO END -------

        self.tokenizer_whitelist = []

        if not self.config.whitelist_single_quoted:
            return

        self._current_line = ContinuedLineState(tokens, self.config)
        for idx, (tok_type, token, start, _, line) in enumerate(tokens):
            if start[0] != line_num:
                line_num = start[0]

            if tok_type != tokenize.STRING:
                continue

            no_quotes_string = token[1:-1]

            if re.findall(
                DOUBLE_QUOTED_STRING_REGEX,
                token,
                UNICODE_MULTILINE_REGEX_FLAG
            ):
                self.add_message('W9912', line=line_num, args=(token, ))
                self.tokenizer_whitelist.append(no_quotes_string)
            elif re.findall(
                SINGLE_QUOTED_STRING_REGEX,
                token,
                UNICODE_MULTILINE_REGEX_FLAG
            ):
                self.tokenizer_whitelist.append(no_quotes_string)

            # Also look for positional placeholder strings

            if self.config.check_string_placeholders:
                if re.findall(
                    UNNAMED_POSITIONAL_PLACEHOLDER_REGEX,
                    token,
                    UNICODE_MULTILINE_REGEX_FLAG
                ):
                    self.add_message('W9913', line=line_num, args=(token, ))


def register(linter):
    """required method to auto register this checker"""
    linter.register_checker(MissingGettextChecker(linter))
