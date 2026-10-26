"""picard_util.py - small helpers copied from MusicBrainz Picard 3.0.2 (GPL-2.0-or-later):
picard/util/__init__.py (pattern_as_regex, wildcards_to_regex_pattern, iswbound, titlecase)
and picard/track.py (TagGenreFilter, the genre filter of Options > Metadata > Genres).
Unchanged but for logging and translation, which TagMender does not need."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
import unicodedata


def _(s):
    return s


class _Log:
    @staticmethod
    def error(*a, **k):
        pass


log = _Log()


def pattern_as_regex(pattern: str, allow_wildcards: bool = False, flags: int = 0) -> re.Pattern[str]:
    """Parses a string and interprets it as a matching pattern.

    - If pattern is of the form /pattern/flags it is interpreted as a regular expression (e.g. `/foo.*/`).
      The flags are optional and in addition to the flags passed in the `flags` function parameter. Supported
      flags in the expression are "i" (ignore case) and "m" (multiline)
    - Otherwise if `allow_wildcards` is True, it is interpreted as a pattern that allows wildcard matching (see below)
    - If `allow_wildcards` is False a regex matching the literal string is returned

    Wildcard matching currently supports these characters:
    - `*`: Matches an arbitrary number of characters or none, e.g. `fo*` matches "foo" or "foot".
    - `?`: Matches exactly one character, e.g. `fo?` matches "foo" or "for".
    - `[...]`: Matches any character in the set, e.g. `[fo?]` matches all of "f", "o" and "?".
    - `?`, `*`, `[`, `]` and `\\` can be escaped with a backslash \\ to match the literal
      character, e.g. `fo\\?` matches "fo?".

    Args:
        pattern: The pattern as a string
        allow_wildcards: If true and if the pattern is not interpreted as a regex wildcard matching is allowed.
        flags: Additional regex flags to set (e.g. `re.I`)

    Returns: An re.Pattern instance

    Raises: `re.error` if the regular expression could not be parsed
    """
    plain_pattern = pattern.rstrip('im')
    if len(plain_pattern) > 2 and plain_pattern[0] == '/' and plain_pattern[-1] == '/':
        extra_flags = pattern[len(plain_pattern) :]
        if 'i' in extra_flags:
            flags |= re.IGNORECASE
        if 'm' in extra_flags:
            flags |= re.MULTILINE
        regex = plain_pattern[1:-1]
    elif allow_wildcards:
        regex = '^' + wildcards_to_regex_pattern(pattern) + '$'
    else:
        regex = re.escape(pattern)
    return re.compile(regex, flags)


def wildcards_to_regex_pattern(pattern: str) -> str:
    """Converts a pattern with shell like wildcards into a regular expression string.

    The following syntax is supported:
    - `*`: Matches an arbitrary number of characters or none, e.g. `fo*` matches "foo" or "foot".
    - `?`: Matches exactly one character, e.g. `fo?` matches "foo" or "for".
    - `[...]`
    - `?`, `*` and `\\` can be escaped with a backslash \\ to match the literal character, e.g. `fo\\?` matches "fo?".

    Args:
        pattern: The pattern as a string

    Returns: A string with a valid regular expression.
    """
    regex = []
    group = None
    escape = False
    for c in pattern:
        if group is not None:
            if escape:
                if c in {'\\', '[', ']'}:
                    c = '\\' + c
                else:
                    group.append('\\\\')
                escape = False
            if c == ']':
                group.append(c)
                part = ''.join(group)
                group = None
            elif c == '\\':
                escape = True
                continue
            else:
                group.append(c)
                continue
        elif escape:
            if c in {'*', '?', '\\', '[', ']'}:
                part = '\\' + c
            else:
                part = re.escape('\\' + c)
            escape = False
        elif c == '\\':
            escape = True
            continue
        elif c == '[':
            group = ['[']
            continue
        elif c == '*':
            part = '.*'
        elif c == '?':
            part = '.'
        else:
            part = re.escape(c)
        regex.append(part)

    # There might be an unclosed character group. Interpret the starting
    # bracket of the group as a literal bracket and re-evaluate the rest.
    if group is not None:
        regex.append('\\[')
        regex.append(wildcards_to_regex_pattern(''.join(group[1:])))
    return ''.join(regex)


def iswbound(char: str) -> bool:
    # GPL 2.0 licensed code by Javier Kohen, Sambhav Kothari
    # from https://github.com/metabrainz/picard-plugins/blob/2.0/plugins/titlecase/titlecase.py
    """Checks whether the given character is a word boundary"""
    category = unicodedata.category(char)
    return category in {'Zs', 'Sk'} or 'P' == category[0]


def titlecase(text: str) -> str:
    # GPL 2.0 licensed code by Javier Kohen, Sambhav Kothari
    # from https://github.com/metabrainz/picard-plugins/blob/2.0/plugins/titlecase/titlecase.py
    """Converts text to title case following word boundary rules.

    Capitalizes the first character of each word in the input text, where words
    are determined by Unicode word boundaries. Preserves existing capitalization
    after the first character of each word.

    Args:
        text (str): The input text to convert to title case.

    Returns:
        str: The text converted to title case. Returns empty string if input is empty.

    Examples:
        >>> titlecase("hello world")
        'Hello World'
        >>> titlecase("children's music")
        'Children's Music'
    """
    if not text:
        return text
    capitalized = text[0].capitalize()
    capital = False
    for i in range(1, len(text)):
        t = text[i]
        if t in "’'" and text[i - 1].isalpha():
            capital = False
        elif iswbound(t):
            capital = True
        elif capital and t.isalpha():
            capital = False
            t = t.capitalize()
        else:
            capital = False
        capitalized += t
    return capitalized


class TagGenreFilter:
    def __init__(self, filters):
        self.errors = {}
        self.match_regexes = defaultdict(list)
        for lineno, line in enumerate(filters.splitlines()):
            line = line.strip()
            if line and line[0] in {'+', '-'}:
                _list = line[0]
                remain = line[1:].strip()
                if not remain:
                    continue
                try:
                    regex_search = pattern_as_regex(remain, allow_wildcards=True, flags=re.IGNORECASE)
                    self.match_regexes[_list].append(regex_search)
                except re.error as e:
                    log.error("Failed to compile regex /%s/: %s", remain, e)
                    self.errors[lineno] = str(e)

    def skip(self, tag):
        if not self.match_regexes:
            return False
        for regex in self.match_regexes['+']:
            if regex.search(tag):
                return False
        for regex in self.match_regexes['-']:
            if regex.search(tag):
                return True
        return False

    def filter(self, counter: Counter, minusage=0) -> Counter:
        result: Counter = Counter()
        for name, count in counter.items():
            if not self.skip(name):
                result[name] = count
        if not result:
            return result
        topcount = result.most_common(1)[0][1]
        for name, count in counter.items():
            percent = 100 * count // topcount
            if percent < minusage:
                del result[name]
        return result

    def format_errors(self):
        fmt = _("Error line %(lineno)d: %(error)s")
        for lineno, error in self.errors.items():
            yield fmt % {'lineno': lineno + 1, 'error': error}

