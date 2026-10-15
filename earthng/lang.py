"""The interface language of an OSM tile package: Ukrainian for Ukraine and Kyiv, English for the
rest (the UK, California, New York, Zanzibar and Romania packages). The launcher, the companion's stage messages, the tester
package's instructions and the mod (build_mod.py --lang) follow it."""
from pathlib import Path

UKRAINIAN = ('ukraine', 'kyiv')   # ukraine-*, kyiv-*


def of(tiles):
    """'uk' or 'en' for a tile package folder (path or name)."""
    return 'uk' if Path(str(tiles)).name.startswith(UKRAINIAN) else 'en'


def text(lang, uk, en):
    return en if lang == 'en' else uk
