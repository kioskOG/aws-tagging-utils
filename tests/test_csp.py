"""The UI must run under a strict CSP: no inline scripts or event handlers."""
import re
from pathlib import Path

from web.app import app

WEB = Path(__file__).resolve().parent.parent / "web"
INLINE_HANDLER = re.compile(r"""\son[a-z]+=["']""", re.I)


def test_csp_header_forbids_inline_script():
    resp = app.test_client().get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
    assert script_src == "script-src 'self'"
    assert "frame-ancestors 'none'" in csp


def test_no_inline_handlers_or_scripts():
    html = (WEB / "templates" / "index.html").read_text()
    js = (WEB / "static" / "app.js").read_text()
    assert not INLINE_HANDLER.findall(html)
    assert not INLINE_HANDLER.findall(js)
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", html), "inline <script> block"


def test_every_ui_action_is_registered():
    html = (WEB / "templates" / "index.html").read_text()
    js = (WEB / "static" / "app.js").read_text()
    used = set(re.findall(r'data-(?:click|change|input)="([a-z0-9-]+)"', html + js))
    block = js[js.index("const UI_ACTIONS = {"):]
    block = block[:block.index("\n};")]
    registered = set(re.findall(r"^\s*'([a-z0-9-]+)':", block, re.M))
    assert used, "no actions found"
    assert used <= registered, f"unregistered: {sorted(used - registered)}"
