"""For test suites written against the defaults before schema 3 (Discogs preferred, the
"tagmender" short names, covers/misc in each disc folder, artist photos in the tracks, small
covers replaced, the tagger's settings followed ...): a profile built from a dict without a
"version" reads as a profile file of that time does (profiles.keep_v2_behaviour). What these
suites test is the behaviour under those settings, which every earlier profile keeps.

    import _v2_defaults; _v2_defaults.use()        # after sys.path holds the project

The new defaults themselves are tested in test_settings_schema3.py and test_profiles.py."""


def use():
    import profiles as pf
    if getattr(pf.from_dict, "v2_defaults", False):
        return
    plain = pf.from_dict

    def from_dict(data, *, check=True, **kw):
        if int((data or {}).get("version") or 0) < 3:
            data = pf.keep_v2_behaviour(data)
        return plain(data, check=check, **kw)
    from_dict.v2_defaults = True
    pf.from_dict = from_dict
