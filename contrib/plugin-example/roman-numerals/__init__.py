"""Roman numerals: an example TagMender plugin (docs/PLUGINS.md)."""

NUMERALS = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def roman(n: int) -> str:
    out = []
    for value, letters in NUMERALS:
        while n >= value:
            out.append(letters)
            n -= value
    return "".join(out)


def register(api):
    def script_roman(parser, text):
        """$roman(number): the number in Roman numerals; text that is not a number from 1 to 3999 stays as it is."""
        try:
            n = int(text.strip())
        except ValueError:
            return text
        return roman(n) if 0 < n < 4000 else text

    api.naming_function("roman", script_roman)

    def disc_subtitles(tags, info):
        if not api.setting("disc_subtitle"):
            return tags
        total = int((tags.get("totaldiscs") or ["1"])[0] or 1)
        disc = (tags.get("discnumber") or [""])[0]
        if total > 1 and disc.isdigit() and not (tags.get("discsubtitle") or [""])[0]:
            tags["discsubtitle"] = [f"Disc {roman(int(disc))}"]
        return tags

    api.tag_processor(disc_subtitles)
