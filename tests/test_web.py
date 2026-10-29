"""Static checks of the browser code, which has no test runner of its own here.

They catch what would otherwise only show up as a blank panel in the browser: a
missing file, a broken import, an element id or a field key spelt differently in
the page, the scripts and gcs/fields.py.
"""

import re
from pathlib import Path

from gcs.fields import FIELDS

WEB = Path(__file__).resolve().parents[1] / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
SCRIPTS = {path.name: path.read_text(encoding="utf-8") for path in (WEB / "js").glob("*.js")}


def test_every_file_the_page_loads_exists():
    references = re.findall(r'(?:src|href)="([^"#:]+)"', PAGE)
    assert references
    assert [ref for ref in references if not (WEB / ref).is_file()] == []


def test_every_module_import_resolves():
    for name, code in SCRIPTS.items():
        for target in re.findall(r'from\s+"(\.\.?/[^"]+)"', code):
            assert (WEB / "js" / target).resolve().is_file(), f"{name} imports {target}"


def test_every_element_id_the_scripts_look_up_is_in_the_page():
    ids = set(re.findall(r'\bid="([^"]+)"', PAGE))
    for name, code in SCRIPTS.items():
        wanted = re.findall(r'getElementById\("([^"]+)"\)', code)
        wanted += re.findall(r'querySelector\("#([\w-]+)"\)', code)
        assert set(wanted) <= ids, f"{name} looks for {set(wanted) - ids}"


def test_every_field_key_in_the_page_is_registered():
    used = set(re.findall(r'data-key="([^"]+)"', PAGE))
    assert used
    assert used <= {field.key for field in FIELDS}
