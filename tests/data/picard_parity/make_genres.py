import random, sys
from collections import Counter
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
from picard import config
from unittest.mock import Mock
fake = Mock(); fake.setting = {}; config.config = fake; config.setting = fake.setting
from picard.track import Track
import mb_tags, picard_config
names = ["rock", "indie rock", "seen live", "pop", "hip hop", "children's music", "drum and bass", "lo-fi",
         "post-rock", "électronique", "k-pop", "favorites", "rock 'n' roll", "a cappella"]
filters = ["", "-seen live\n-favorites\n-fixme\n-owned", "+rock\n-*rock*", "-/^p/", "+*\n-pop"]
random.seed(7)
bad = 0
CASES = []
for i in range(4000):
    c = Counter({n: random.randint(-1, 20) for n in random.sample(names, random.randint(0, 8))})
    lim, mu, flt, join = random.choice([0, 1, 3, 5, 100]), random.choice([0, 50, 90, 100]), random.choice(filters), random.choice(["", "; ", " / "])
    want = Track._genres_to_metadata(Counter(c), limit=lim, minusage=mu, filters=flt, join_with=join)
    s = dict(picard_config.TAG_DEFAULTS, use_genres=True, max_genres=lim, min_genre_usage=mu, genres_filter=flt,
             join_genres=join)
    rel = {"genres": [{"name": k, "count": v} for k, v in c.items()]}
    got = mb_tags.genres({}, rel, s)
    want = [] if want == [''] else want
    if i < 300:
        CASES.append({'counts': dict(c), 'settings': {k: s[k] for k in ('max_genres','min_genre_usage','genres_filter','join_genres')}, 'want': want})
    if got != want:
        bad += 1
        if bad < 6:
            print("DIFF", dict(c), lim, mu, repr(flt), repr(join), want, got)
print("differences:", bad)
import json; open(sys.argv[3], "w").write(json.dumps(CASES, indent=0, ensure_ascii=False))
