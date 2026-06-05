import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import firebase_admin
import requests
from bs4 import BeautifulSoup
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from flask import (
    Flask,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from markupsafe import Markup, escape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED_HOSTS = {"jw.org", "www.jw.org", "wol.jw.org"}

app = Flask(__name__)


# ----------------------------------------------------------------------------
# Firebase / Firestore
# ----------------------------------------------------------------------------
def init_firebase():
    if firebase_admin._apps:
        return
    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
    else:
        file_candidates = [
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
            "/etc/secrets/serviceAccountKey.json",
            os.path.join(BASE_DIR, "serviceAccountKey.json"),
        ]
        path = next((p for p in file_candidates if p and os.path.exists(p)), None)
        if not path:
            raise RuntimeError(
                "Credenziali Firebase mancanti: imposta la variabile FIREBASE_CREDENTIALS "
                "oppure metti il file serviceAccountKey.json nella cartella del progetto."
            )
        cred = credentials.Certificate(path)
    firebase_admin.initialize_app(cred)


init_firebase()
db = firestore.client()

CATEGORIES = db.collection("categories")
ARTICLES = db.collection("articles")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# Scraping jw.org / wol.jw.org
# ----------------------------------------------------------------------------
def host_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host in ALLOWED_HOSTS


def fetch_article(url: str):
    """Scarica una pagina jw.org / wol.jw.org ed estrae titolo + HTML principale."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    candidates = [
        ("article", {}),
        ("div", {"id": "article"}),
        ("div", {"class": "docClass-13"}),
        ("div", {"id": "main"}),
        ("main", {}),
    ]
    content = None
    for tag, attrs in candidates:
        el = soup.find(tag, attrs=attrs) if attrs else soup.find(tag)
        if el and len(el.get_text(strip=True)) > 200:
            content = el
            break
    if content is None:
        content = soup.body or soup

    for bad in content.find_all(["script", "style", "noscript", "nav", "footer", "form", "iframe"]):
        bad.decompose()

    for cls in ["toolbar", "shareBar", "ribbon", "secondaryColumn", "groupTOC"]:
        for el in content.find_all(attrs={"class": cls}):
            el.decompose()

    for img in content.find_all("img"):
        src = img.get("src")
        if src and src.startswith("/"):
            img["src"] = "https://" + urlparse(url).netloc + src
        if src and src.startswith("//"):
            img["src"] = "https:" + src

    for a in content.find_all("a"):
        href = a.get("href")
        if href and href.startswith("/"):
            a["href"] = "https://" + urlparse(url).netloc + href
        a["target"] = "_blank"
        a["rel"] = "noopener"

    return title or url, str(content)


# ----------------------------------------------------------------------------
# Helper Firestore
# ----------------------------------------------------------------------------
def get_category(cid):
    doc = CATEGORIES.document(cid).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def get_article(aid):
    doc = ARTICLES.document(aid).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = doc.id
    data["tags"] = data.get("tags") or []
    return data


def html_to_text(html):
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def extract_highlights(html):
    """Estrae il testo di tutte le sottolineature (<mark class='user-hl'>)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out = []
    for m in soup.select("mark.user-hl"):
        t = m.get_text(" ", strip=True)
        t = re.sub(r"\s+", " ", t).strip()
        if t and (not out or out[-1] != t):
            out.append(t)
    return out


def make_snippet(text, query, radius=90):
    """Restituisce un estratto attorno alla parola cercata, con il match evidenziato."""
    low = text.lower()
    i = low.find(query.lower())
    if i == -1:
        head = text[: radius * 2]
        return escape(head) + (Markup("…") if len(text) > radius * 2 else Markup(""))
    start = max(0, i - radius)
    end = min(len(text), i + len(query) + radius)
    pre = ("…" if start > 0 else "") + text[start:i]
    match = text[i : i + len(query)]
    post = text[i + len(query) : end] + ("…" if end < len(text) else "")
    return Markup("{}<mark>{}</mark>{}").format(pre, match, post)


def category_name_map():
    return {doc.id: doc.to_dict().get("name", "") for doc in CATEGORIES.stream()}


# ----------------------------------------------------------------------------
# Rotte
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# PWA: service worker (servito dalla radice per avere scope "/") e manifest
# ----------------------------------------------------------------------------
@app.route("/sw.js")
def service_worker():
    resp = make_response(send_from_directory(app.static_folder, "sw.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/manifest.webmanifest")
def manifest():
    resp = make_response(send_from_directory(app.static_folder, "manifest.webmanifest"))
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@app.route("/")
def index():
    counts = {}
    tag_counts = {}
    for doc in ARTICLES.select(["category_id", "tags"]).stream():
        d = doc.to_dict()
        cid = d.get("category_id")
        counts[cid] = counts.get(cid, 0) + 1
        for t in (d.get("tags") or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    cats = []
    for doc in CATEGORIES.stream():
        data = doc.to_dict()
        cats.append({"id": doc.id, "name": data.get("name", ""), "n": counts.get(doc.id, 0)})
    cats.sort(key=lambda c: c["name"].lower())
    tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0].lower()))
    return render_template("index.html", categories=cats, tags=tags)


@app.route("/categories", methods=["POST"])
def create_category():
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("index"))
    existing = list(
        CATEGORIES.where(filter=FieldFilter("name", "==", name)).limit(1).stream()
    )
    if not existing:
        CATEGORIES.add({"name": name, "created_at": now_iso()})
    return redirect(url_for("index"))


@app.route("/categories/<cid>/delete", methods=["POST"])
def delete_category(cid):
    for doc in ARTICLES.where(filter=FieldFilter("category_id", "==", cid)).stream():
        doc.reference.delete()
    CATEGORIES.document(cid).delete()
    return redirect(url_for("index"))


@app.route("/categories/<cid>")
def view_category(cid):
    cat = get_category(cid)
    if not cat:
        return redirect(url_for("index"))
    articles = []
    total_hl = 0
    for doc in ARTICLES.where(filter=FieldFilter("category_id", "==", cid)).stream():
        data = doc.to_dict()
        hl = len(extract_highlights(data.get("content_html", "")))
        total_hl += hl
        articles.append(
            {
                "id": doc.id,
                "title": data.get("title", ""),
                "url": data.get("url", ""),
                "created_at": data.get("created_at", ""),
                "tags": data.get("tags") or [],
                "hl": hl,
            }
        )
    articles.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    return render_template("category.html", category=cat, articles=articles, total_hl=total_hl)


@app.route("/categories/<cid>/articles", methods=["POST"])
def add_article(cid):
    url = (request.form.get("url") or "").strip()
    custom_title = (request.form.get("title") or "").strip()
    if not url or not host_allowed(url):
        return redirect(url_for("view_category", cid=cid) + "?err=host")
    try:
        title, html = fetch_article(url)
    except Exception as e:
        return redirect(url_for("view_category", cid=cid) + f"?err=fetch:{type(e).__name__}")
    if custom_title:
        title = custom_title
    ARTICLES.add(
        {
            "category_id": cid,
            "title": title,
            "url": url,
            "content_html": html,
            "created_at": now_iso(),
        }
    )
    return redirect(url_for("view_category", cid=cid))


@app.route("/articles/<aid>/delete", methods=["POST"])
def delete_article(aid):
    art = get_article(aid)
    ARTICLES.document(aid).delete()
    if art:
        return redirect(url_for("view_category", cid=art["category_id"]))
    return redirect(url_for("index"))


@app.route("/articles/<aid>")
def view_article(aid):
    art = get_article(aid)
    if not art:
        return redirect(url_for("index"))
    cat = get_category(art.get("category_id"))
    art["category_name"] = cat["name"] if cat else ""
    return render_template("article.html", article=art, print_mode=False)


@app.route("/articles/<aid>/print")
def print_article(aid):
    art = get_article(aid)
    if not art:
        return redirect(url_for("index"))
    cat = get_category(art.get("category_id"))
    art["category_name"] = cat["name"] if cat else ""
    return render_template("article.html", article=art, print_mode=True)


@app.route("/articles/<aid>/save", methods=["POST"])
def save_article_html(aid):
    data = request.get_json(silent=True) or {}
    html = data.get("html")
    if html is None:
        return jsonify({"ok": False, "error": "missing html"}), 400
    ARTICLES.document(aid).update({"content_html": html})
    return jsonify({"ok": True})


@app.route("/articles/<aid>/refresh", methods=["POST"])
def refresh_article(aid):
    art = get_article(aid)
    if not art:
        return redirect(url_for("index"))
    try:
        _, html = fetch_article(art["url"])
        ARTICLES.document(aid).update({"content_html": html})
    except Exception:
        pass
    return redirect(url_for("view_article", aid=aid))


@app.route("/categories/<cid>/study")
def study_category(cid):
    cat = get_category(cid)
    if not cat:
        return redirect(url_for("index"))
    print_mode = request.args.get("print") == "1"
    arts = []
    for doc in ARTICLES.where(filter=FieldFilter("category_id", "==", cid)).stream():
        d = doc.to_dict()
        d["id"] = doc.id
        arts.append(d)
    arts.sort(key=lambda a: a.get("created_at", ""))
    items = []
    total = 0
    for a in arts:
        hl = extract_highlights(a.get("content_html", ""))
        if hl:
            items.append(
                {"id": a["id"], "title": a.get("title", ""), "url": a.get("url", ""), "highlights": hl}
            )
            total += len(hl)
    return render_template(
        "study.html", category=cat, items=items, total=total, print_mode=print_mode
    )


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        cat_names = category_name_map()
        ql = q.lower()
        for doc in ARTICLES.stream():
            d = doc.to_dict()
            title = d.get("title", "")
            tags = d.get("tags") or []
            text = html_to_text(d.get("content_html", ""))
            hay = " ".join([title, " ".join(tags), text]).lower()
            if ql in hay:
                in_title = ql in title.lower()
                source = text if ql in text.lower() else title
                results.append(
                    {
                        "id": doc.id,
                        "title": title,
                        "url": d.get("url", ""),
                        "category_id": d.get("category_id"),
                        "category_name": cat_names.get(d.get("category_id"), ""),
                        "tags": tags,
                        "snippet": make_snippet(source, q),
                        "in_title": in_title,
                    }
                )
        results.sort(key=lambda r: (not r["in_title"], r["title"].lower()))
    return render_template("search.html", q=q, results=results, tag=None)


@app.route("/tags/<tag>")
def view_tag(tag):
    cat_names = category_name_map()
    results = []
    for doc in ARTICLES.stream():
        d = doc.to_dict()
        if tag in (d.get("tags") or []):
            results.append(
                {
                    "id": doc.id,
                    "title": d.get("title", ""),
                    "url": d.get("url", ""),
                    "category_id": d.get("category_id"),
                    "category_name": cat_names.get(d.get("category_id"), ""),
                    "tags": d.get("tags") or [],
                    "snippet": "",
                    "in_title": False,
                }
            )
    results.sort(key=lambda r: r["title"].lower())
    return render_template("search.html", q="", results=results, tag=tag)


@app.route("/articles/<aid>/tags", methods=["POST"])
def save_tags(aid):
    raw = request.form.get("tags", "")
    tags = []
    for t in raw.replace("#", "").split(","):
        t = t.strip()
        if t and t not in tags:
            tags.append(t)
    ARTICLES.document(aid).update({"tags": tags})
    return redirect(url_for("view_article", aid=aid))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
