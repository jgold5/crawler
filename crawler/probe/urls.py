"""URL shapes and link filtering for the read-only BFS."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_ID_SEG = re.compile(
    r"^(?:\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{16,}"
    r"|[A-Za-z0-9_-]{20,}|.*\d.*)$")
_DOWNLOAD_EXT = re.compile(
    r"\.(?:pdf|zip|gz|tgz|tar|7z|rar|csv|tsv|xlsx?|xlsm|docx?|pptx?|odt|ods|rtf|txt|json|xml|png|jpe?g|gif|svg|webp|ico"
    r"|mp4|mp3|wav|avi|mov|exe|msi|dmg|apk|jar|war|iso|bin|ics|vcf|eml|msg)$", re.I)
_LOGOUT = re.compile(r"log[-_ ]?out|sign[-_ ]?out|log[-_ ]?off|sign[-_ ]?off|end[-_]?session|logoff|signoff", re.I)
_ACTION = re.compile(
    r"(?:^|[/_.?&=-])(?:delete|remove|destroy|drop|purge|approve|reject|cancel|archive|revoke|disable|enable|activate"
    r"|deactivate|execute|run|submit|confirm|unsubscribe|reset|void|refund|pay|checkout|lock|unlock|kill|stop|start"
    r"|restart|shutdown|import|export|download|print)(?:$|[/_.?&=-])", re.I)
_EXT_KEEP = ("html", "htm", "aspx", "jsp", "php", "do", "action", "xhtml", "jsf")


def _route(fragment: str) -> str | None:
    """A hash-router route ('#/x/y' or '#!/x'), or None for plain in-page anchors."""
    if fragment.startswith("/"):
        return fragment
    if fragment.startswith("!/"):
        return fragment[1:]
    return None


def _shape_segs(path: str) -> list[str]:
    out = []
    for seg in path.split("/"):
        if seg == "":
            continue
        out.append("{id}" if _ID_SEG.match(seg) else seg)
    return out


def dedupe_key(url: str) -> str:
    """Path shape used to dedupe pages: id-like segments -> {id}; query VALUES ignored; hash routes included."""
    u = urlsplit(url)
    key = "/" + "/".join(_shape_segs(u.path))
    route = _route(u.fragment)
    if route is not None:
        key += "#/" + "/".join(_shape_segs(route.split("?")[0]))
    qk = sorted({k for k, _ in parse_qsl(u.query, keep_blank_values=True)})
    if qk:
        key += "?" + "&".join(qk)
    return key


def public_shape(url: str) -> str:
    """Path shape safe to publish: every word segment is '<w>', ids are '{id}', query is '?<q>'."""
    u = urlsplit(url)

    def gen(segs: list[str]) -> str:
        parts = []
        for s in segs:
            if s == "{id}":
                parts.append("{id}")
                continue
            m = re.fullmatch(r".+\.([A-Za-z]+)", s)
            parts.append(f"<w>.{m.group(1).lower()}" if m and m.group(1).lower() in _EXT_KEEP else "<w>")
        return "/" + "/".join(parts) if parts else "/"

    shape = gen(_shape_segs(u.path))
    route = _route(u.fragment)
    if route is not None:
        shape += "#" + gen(_shape_segs(route.split("?")[0]))
    if u.query:
        shape += "?<q>"
    return shape


def normalize(url: str) -> str:
    """Drop plain anchors (keep hash routes)."""
    u = urlsplit(url)
    frag = u.fragment if _route(u.fragment) is not None else ""
    return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, frag))


def classify_link(href: str | None, origin: str, *, logout_flag: bool, download_flag: bool, method: str | None,
                  same_origin_only: bool, current: str) -> str | None:
    """Reason to skip this link (a LINK_SKIPS member), or None if it should be crawled."""
    if not href:
        return "non-http"
    u = urlsplit(href)
    if u.scheme not in ("http", "https"):
        return "non-http"
    if logout_flag or _LOGOUT.search(u.path) or _LOGOUT.search(u.query) or _LOGOUT.search(u.fragment):
        return "logout"
    if same_origin_only and f"{u.scheme}://{u.netloc}" != origin:
        return "external"
    if download_flag or _DOWNLOAD_EXT.search(u.path):
        return "download"
    if method and method != "get":
        return "non-get"
    if _ACTION.search(u.path) or _ACTION.search(u.query) or _ACTION.search(_route(u.fragment) or ""):
        return "action-like"
    if normalize(href) == normalize(current):
        return "same-document"
    return None


def origin_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"
