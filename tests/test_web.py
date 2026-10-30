"""Checks of the browser code.

The static ones catch what would otherwise only show up as a blank panel in the
browser: a missing file, a broken import, an element id or a field key spelt
differently in the page, the scripts and gcs/fields.py. With Node.js installed,
every script is also parsed, and the unit tests in tests/js run.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gcs.fields import FIELDS
from gcs.gozcu import GOZCU_FIELDS

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
SCRIPTS = {path.name: path.read_text(encoding="utf-8") for path in (WEB / "js").glob("*.js")}
NODE_VERSION = (22, 7)  # the first that loads .js files as ES modules without being told


def find_node() -> str | None:
    """A new enough Node.js: on PATH, or where nvm puts it (outside PATH in non-login shells)."""
    candidates = [shutil.which("node"), *Path.home().glob(".nvm/versions/node/*/bin/node")]
    for node in filter(None, candidates):
        version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
        found = re.match(r"v(\d+)\.(\d+)\.", version)
        if found and (int(found[1]), int(found[2])) >= NODE_VERSION:
            return str(node)
    return None


@pytest.fixture(scope="module")
def node() -> str:
    found = find_node()
    if found is None:
        pytest.skip("Node.js {}.{} or newer is not installed".format(*NODE_VERSION))
    return found


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
        wanted = re.findall(r'(?:getElementById|byId)\("([^"]+)"\)', code)
        wanted += re.findall(r'querySelector\("#([\w-]+)"\)', code)
        assert set(wanted) <= ids, f"{name} looks for {set(wanted) - ids}"


def test_every_field_key_in_the_page_is_registered():
    used = set(re.findall(r'data-key="([^"]+)"', PAGE))
    assert used
    assert used <= {field.key for field in FIELDS}
    used = set(re.findall(r'data-gozcu-key="([^"]+)"', PAGE))
    assert used
    assert used <= {field.key for field in GOZCU_FIELDS}


def test_every_script_parses(node):
    for path in sorted((WEB / "js").glob("*.js")):
        run = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr


def test_the_unit_tests_of_the_browser_code_pass(node):
    tests = sorted((ROOT / "tests" / "js").glob("*.test.js"))
    assert tests
    run = subprocess.run([node, "--test", *tests], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
