"""picard_script/_shim.py - the few pieces of Picard that its script engine (parser.py and
functions.py, copied from MusicBrainz Picard 3.0.2, GPL-2.0-or-later) needs, without Qt:
the Metadata store (picard/metadata.py, the methods scripts use), the function registry
(picard/extension_points/script_functions.py), the release country names
(picard/const/countries.py) and a few helpers (picard/util)."""
from __future__ import annotations

from collections import namedtuple
from collections.abc import Iterable, MutableMapping
from inspect import getfullargspec

from picard_util import pattern_as_regex, titlecase  # noqa: F401

MULTI_VALUED_JOINER = "; "


def N_(s):
    return s


def gettext_countries(s):
    return s


def gettext(s):
    return s


def uniqify(seq):
    """Uniqify a list, preserving order (picard/util)."""
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


class Metadata(MutableMapping):
    """picard/metadata.py's Metadata as scripts see it: a value per tag name (a bare string,
    or a list for several), get() joining them with "; "."""
    multi_valued_joiner = MULTI_VALUED_JOINER

    def __init__(self, *args, **kwargs):
        self._store = {}
        self.deleted_tags = set()
        self.length = 0
        if args or kwargs:
            self.update(*args, **kwargs)

    @staticmethod
    def normalize_tag(name):
        return name.rstrip(":")

    @staticmethod
    def _as_list(stored):
        return [stored] if type(stored) is str else stored

    def getall(self, name):
        stored = self._store.get(self.normalize_tag(name))
        return [] if stored is None else self._as_list(stored)

    def getraw(self, name):
        return self._as_list(self._store[self.normalize_tag(name)])

    def get(self, name, default=None):
        stored = self._store.get(self.normalize_tag(name), None)
        if stored is None:
            return default
        if type(stored) is str:
            return stored or default
        if stored:
            return self.multi_valued_joiner.join(stored)
        return default

    def __getitem__(self, name):
        return self.get(name) or ""

    def _set(self, name, values):
        name = self.normalize_tag(name)
        if isinstance(values, str) or not isinstance(values, Iterable):
            values = [values]
        values = [str(value) for value in values if value or value in {0, ""}]
        count = len(values)
        if count and (count > 1 or values[0]):
            self._store[name] = values if count > 1 else values[0]
            self.deleted_tags.discard(name)
        elif name in self._store:
            self._del(name)

    def set(self, name, values):
        self._set(name, values)

    def __setitem__(self, name, values):
        self.set(name, values)

    def __contains__(self, name):
        return self._store.__contains__(self.normalize_tag(name))

    def get_new(self, name, default=""):
        return self.get(name, default)

    def get_original(self, name, default=""):
        return self.get(name, default)

    def _del(self, name):
        name = self.normalize_tag(name)
        self._store.pop(name, None)
        self.deleted_tags.add(name)

    def __delitem__(self, name):
        self._del(name)

    def delete(self, name):
        del self[self.normalize_tag(name)]

    def add(self, name, value):
        if value or value == 0:
            name = self.normalize_tag(name)
            stored = self._store.get(name)
            if stored is None:
                self._store[name] = str(value)
            elif isinstance(stored, str):
                self._store[name] = [stored, str(value)]
            else:
                stored.append(str(value))
            self.deleted_tags.discard(name)

    def add_unique(self, name, value):
        name = self.normalize_tag(name)
        if value not in self.getall(name):
            self.add(name, value)

    def unset(self, name):
        self._store.pop(self.normalize_tag(name), None)

    def __iter__(self):
        yield from self._store

    def __len__(self):
        return len(self._store)

    def items(self):
        for name, stored in self._store.items():
            if isinstance(stored, str):
                yield name, stored
            else:
                for value in stored:
                    yield name, value

    def rawitems(self):
        return [(name, self._as_list(stored)) for name, stored in self._store.items()]


Bound = namedtuple("Bound", ["lower", "upper"])


class FunctionRegistryItem:
    def __init__(self, function, eval_args, argcount, documentation=None, name=None, module=None, signature=None):
        self.function = function
        self.eval_args = eval_args
        self.argcount = argcount
        self.documentation = documentation
        self.name = name
        self.module = module
        self.signature = signature
        self.plugin_id = None


class _Registry:
    """picard.extension_points.script_functions, as the parser reads it: (name, item) pairs."""
    def __init__(self):
        self.items = {}

    def register(self, module, pair):
        self.items[pair[0]] = pair[1]

    def __iter__(self):
        return iter(self.items.items())


class script_functions:                     # (the module the parser imports, as a namespace)
    ext_point_script_functions = _Registry()


def register_script_function(function, name=None, eval_args=True, check_argcount=True, documentation=None,
                             signature=None):
    argspec = getfullargspec(function)
    args = len(argspec.args) - 1
    varargs = argspec.varargs is not None
    defaults = len(argspec.defaults) if argspec.defaults else 0
    argcount = Bound(args - defaults, args if not varargs else None) if check_argcount else None
    if name is None:
        name = getattr(function, "__name__", str(function))
    script_functions.ext_point_script_functions.register(
        function.__module__, (name, FunctionRegistryItem(function, eval_args, argcount, documentation or function.__doc__,
                                                          name=name, module=function.__module__,
                                                          signature=signature)))


def script_function(name=None, eval_args=True, check_argcount=True, prefix="func_", documentation=None,
                    signature=None):
    def decorator(func):
        fname = func.__name__
        sname = fname[len(prefix):] if name is None and prefix and fname.startswith(prefix) else name
        register_script_function(func, name=sname, eval_args=eval_args, check_argcount=check_argcount,
                                 documentation=documentation, signature=signature)
        return func
    return decorator


RELEASE_COUNTRIES = {
    'AD': 'Andorra',
    'AE': 'United Arab Emirates',
    'AF': 'Afghanistan',
    'AG': 'Antigua and Barbuda',
    'AI': 'Anguilla',
    'AL': 'Albania',
    'AM': 'Armenia',
    'AN': 'Netherlands Antilles',
    'AO': 'Angola',
    'AQ': 'Antarctica',
    'AR': 'Argentina',
    'AS': 'American Samoa',
    'AT': 'Austria',
    'AU': 'Australia',
    'AW': 'Aruba',
    'AX': 'Åland Islands',
    'AZ': 'Azerbaijan',
    'BA': 'Bosnia and Herzegovina',
    'BB': 'Barbados',
    'BD': 'Bangladesh',
    'BE': 'Belgium',
    'BF': 'Burkina Faso',
    'BG': 'Bulgaria',
    'BH': 'Bahrain',
    'BI': 'Burundi',
    'BJ': 'Benin',
    'BL': 'Saint Barthélemy',
    'BM': 'Bermuda',
    'BN': 'Brunei',
    'BO': 'Bolivia',
    'BQ': 'Bonaire, Sint Eustatius and Saba',
    'BR': 'Brazil',
    'BS': 'Bahamas',
    'BT': 'Bhutan',
    'BV': 'Bouvet Island',
    'BW': 'Botswana',
    'BY': 'Belarus',
    'BZ': 'Belize',
    'CA': 'Canada',
    'CC': 'Cocos (Keeling) Islands',
    'CD': 'Democratic Republic of the Congo',
    'CF': 'Central African Republic',
    'CG': 'Congo',
    'CH': 'Switzerland',
    'CI': 'Côte d\'Ivoire',
    'CK': 'Cook Islands',
    'CL': 'Chile',
    'CM': 'Cameroon',
    'CN': 'China',
    'CO': 'Colombia',
    'CR': 'Costa Rica',
    'CS': 'Serbia and Montenegro',
    'CU': 'Cuba',
    'CV': 'Cape Verde',
    'CW': 'Curaçao',
    'CX': 'Christmas Island',
    'CY': 'Cyprus',
    'CZ': 'Czechia',
    'DE': 'Germany',
    'DJ': 'Djibouti',
    'DK': 'Denmark',
    'DM': 'Dominica',
    'DO': 'Dominican Republic',
    'DZ': 'Algeria',
    'EC': 'Ecuador',
    'EE': 'Estonia',
    'EG': 'Egypt',
    'EH': 'Western Sahara',
    'ER': 'Eritrea',
    'ES': 'Spain',
    'ET': 'Ethiopia',
    'FI': 'Finland',
    'FJ': 'Fiji',
    'FK': 'Falkland Islands',
    'FM': 'Federated States of Micronesia',
    'FO': 'Faroe Islands',
    'FR': 'France',
    'GA': 'Gabon',
    'GB': 'United Kingdom',
    'GD': 'Grenada',
    'GE': 'Georgia',
    'GF': 'French Guiana',
    'GG': 'Guernsey',
    'GH': 'Ghana',
    'GI': 'Gibraltar',
    'GL': 'Greenland',
    'GM': 'Gambia',
    'GN': 'Guinea',
    'GP': 'Guadeloupe',
    'GQ': 'Equatorial Guinea',
    'GR': 'Greece',
    'GS': 'South Georgia and the South Sandwich Islands',
    'GT': 'Guatemala',
    'GU': 'Guam',
    'GW': 'Guinea-Bissau',
    'GY': 'Guyana',
    'HK': 'Hong Kong',
    'HM': 'Heard Island and McDonald Islands',
    'HN': 'Honduras',
    'HR': 'Croatia',
    'HT': 'Haiti',
    'HU': 'Hungary',
    'ID': 'Indonesia',
    'IE': 'Ireland',
    'IL': 'Israel',
    'IM': 'Isle of Man',
    'IN': 'India',
    'IO': 'British Indian Ocean Territory',
    'IQ': 'Iraq',
    'IR': 'Iran',
    'IS': 'Iceland',
    'IT': 'Italy',
    'JE': 'Jersey',
    'JM': 'Jamaica',
    'JO': 'Jordan',
    'JP': 'Japan',
    'KE': 'Kenya',
    'KG': 'Kyrgyzstan',
    'KH': 'Cambodia',
    'KI': 'Kiribati',
    'KM': 'Comoros',
    'KN': 'Saint Kitts and Nevis',
    'KP': 'North Korea',
    'KR': 'South Korea',
    'KW': 'Kuwait',
    'KY': 'Cayman Islands',
    'KZ': 'Kazakhstan',
    'LA': 'Laos',
    'LB': 'Lebanon',
    'LC': 'Saint Lucia',
    'LI': 'Liechtenstein',
    'LK': 'Sri Lanka',
    'LR': 'Liberia',
    'LS': 'Lesotho',
    'LT': 'Lithuania',
    'LU': 'Luxembourg',
    'LV': 'Latvia',
    'LY': 'Libya',
    'MA': 'Morocco',
    'MC': 'Monaco',
    'MD': 'Moldova',
    'ME': 'Montenegro',
    'MF': 'Saint Martin (French part)',
    'MG': 'Madagascar',
    'MH': 'Marshall Islands',
    'MK': 'North Macedonia',
    'ML': 'Mali',
    'MM': 'Myanmar',
    'MN': 'Mongolia',
    'MO': 'Macao',
    'MP': 'Northern Mariana Islands',
    'MQ': 'Martinique',
    'MR': 'Mauritania',
    'MS': 'Montserrat',
    'MT': 'Malta',
    'MU': 'Mauritius',
    'MV': 'Maldives',
    'MW': 'Malawi',
    'MX': 'Mexico',
    'MY': 'Malaysia',
    'MZ': 'Mozambique',
    'NA': 'Namibia',
    'NC': 'New Caledonia',
    'NE': 'Niger',
    'NF': 'Norfolk Island',
    'NG': 'Nigeria',
    'NI': 'Nicaragua',
    'NL': 'Netherlands',
    'NO': 'Norway',
    'NP': 'Nepal',
    'NR': 'Nauru',
    'NU': 'Niue',
    'NZ': 'New Zealand',
    'OM': 'Oman',
    'PA': 'Panama',
    'PE': 'Peru',
    'PF': 'French Polynesia',
    'PG': 'Papua New Guinea',
    'PH': 'Philippines',
    'PK': 'Pakistan',
    'PL': 'Poland',
    'PM': 'Saint Pierre and Miquelon',
    'PN': 'Pitcairn',
    'PR': 'Puerto Rico',
    'PS': 'Palestine',
    'PT': 'Portugal',
    'PW': 'Palau',
    'PY': 'Paraguay',
    'QA': 'Qatar',
    'RE': 'Réunion',
    'RO': 'Romania',
    'RS': 'Serbia',
    'RU': 'Russia',
    'RW': 'Rwanda',
    'SA': 'Saudi Arabia',
    'SB': 'Solomon Islands',
    'SC': 'Seychelles',
    'SD': 'Sudan',
    'SE': 'Sweden',
    'SG': 'Singapore',
    'SH': 'Saint Helena, Ascension and Tristan da Cunha',
    'SI': 'Slovenia',
    'SJ': 'Svalbard and Jan Mayen',
    'SK': 'Slovakia',
    'SL': 'Sierra Leone',
    'SM': 'San Marino',
    'SN': 'Senegal',
    'SO': 'Somalia',
    'SR': 'Suriname',
    'SS': 'South Sudan',
    'ST': 'Sao Tome and Principe',
    'SU': 'Soviet Union',
    'SV': 'El Salvador',
    'SX': 'Sint Maarten (Dutch part)',
    'SY': 'Syria',
    'SZ': 'Eswatini',
    'TC': 'Turks and Caicos Islands',
    'TD': 'Chad',
    'TF': 'French Southern Territories',
    'TG': 'Togo',
    'TH': 'Thailand',
    'TJ': 'Tajikistan',
    'TK': 'Tokelau',
    'TL': 'Timor-Leste',
    'TM': 'Turkmenistan',
    'TN': 'Tunisia',
    'TO': 'Tonga',
    'TR': 'Türkiye',
    'TT': 'Trinidad and Tobago',
    'TV': 'Tuvalu',
    'TW': 'Taiwan',
    'TZ': 'Tanzania',
    'UA': 'Ukraine',
    'UG': 'Uganda',
    'UM': 'United States Minor Outlying Islands',
    'US': 'United States',
    'UY': 'Uruguay',
    'UZ': 'Uzbekistan',
    'VA': 'Vatican City',
    'VC': 'Saint Vincent and The Grenadines',
    'VE': 'Venezuela',
    'VG': 'British Virgin Islands',
    'VI': 'U.S. Virgin Islands',
    'VN': 'Vietnam',
    'VU': 'Vanuatu',
    'WF': 'Wallis and Futuna',
    'WS': 'Samoa',
    'XC': 'Czechoslovakia',
    'XE': 'Europe',
    'XG': 'East Germany',
    'XK': 'Kosovo',
    'XW': '[Worldwide]',
    'YE': 'Yemen',
    'YT': 'Mayotte',
    'YU': 'Yugoslavia',
    'ZA': 'South Africa',
    'ZM': 'Zambia',
    'ZW': 'Zimbabwe',
}
