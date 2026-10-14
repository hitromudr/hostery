"""Net View service rows stay on one line (owner, 14.10.2026: names like
`dms-old-rendezvous-relay` wrapped onto two lines and broke the grid)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text()
JS = (ROOT / "static" / "js" / "monitoring.js").read_text()


def _rule(selector):
    m = re.search(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", HTML, re.M)
    assert m, selector
    return {k.strip(): v.strip() for k, v in
            (d.split(":", 1) for d in m.group(1).split(";") if ":" in d)}


def test_service_name_is_cut_not_wrapped():
    r = _rule(".svc-name")
    assert r["white-space"] == "nowrap"
    assert r["overflow"] == "hidden"
    assert r["text-overflow"] == "ellipsis"
    # Without min-width: 0 a flex item never shrinks below its text width.
    assert r["min-width"] == "0"


def test_timing_never_wraps_or_shrinks():
    r = _rule(".svc-ms")
    assert r["white-space"] == "nowrap"
    assert r["flex-shrink"] == "0"


def test_full_name_leads_the_tooltip():
    assert "const tipParts = [svc, labels[st] || st];" in JS
