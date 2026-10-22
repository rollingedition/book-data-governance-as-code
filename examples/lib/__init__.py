"""The book's shared code. Python runs this file before any module of `lib`, so a setting made here holds whichever
module an example imports first, and whatever that module imports.

Presidio's email recognizer checks domains with tldextract, which fetches the Public Suffix List on first use. It reads
TLDEXTRACT_PUBLIC_SUFFIX_LIST_URLS when it builds its shared extractor, on first import, and an empty value means no URLs
to fetch, so it uses the copy it ships with: detection runs offline and answers the same everywhere. An example that
imports Presidio before lib has loaded tldextract already, so its shared extractor is replaced with one that has no
URLs, the extractor tldextract's README shows for production. lib/pii.py checks that the setting held."""
import os
import sys

os.environ["TLDEXTRACT_PUBLIC_SUFFIX_LIST_URLS"] = ""
if "tldextract.tldextract" in sys.modules:
    _tldextract = sys.modules["tldextract.tldextract"]
    _tldextract.TLD_EXTRACTOR = _tldextract.TLDExtract(suffix_list_urls=())    # tldextract.extract() calls this one
