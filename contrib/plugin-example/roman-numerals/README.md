# Roman numerals: an example TagMender plugin

Adds `$roman(number)` to naming and tagger scripts (`Disc $roman(%discnumber%)/` gives `Disc IV/`),
and, when its setting is on, fills an empty Disc Subtitle on a box set with `Disc I`, `Disc II`, ...

Install it: zip this folder (`zip -r roman-numerals.zip roman-numerals`), then Preferences › Plugins ›
Install from File…, and switch it on. Or `tagmender plugins install roman-numerals`.

See [docs/PLUGINS.md](../../../docs/PLUGINS.md) for how plugins work.
