import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, g, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR permette di puntare il database a un disco persistente (es. su Render).
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "ricerche.db")

ALLOWED_HOSTS = {"jw.org", "www.jw.org", "wol.jw.org"}

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            content_html TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    conn.close()


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


@app.route("/")
def index():
    db = get_db()
    cats = db.execute(
        """
        SELECT c.id, c.name,
               (SELECT COUNT(*) FROM articles a WHERE a.category_id = c.id) AS n
        FROM categories c
        ORDER BY c.name COLLATE NOCASE
        """
    ).fetchall()
    return render_template("index.html", categories=cats)


@app.route("/categories", methods=["POST"])
def create_category():
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("index"))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO categories (name, created_at) VALUES (?, ?)",
            (name, datetime.utcnow().isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        pass
    return redirect(url_for("index"))


@app.route("/categories/<int:cid>/delete", methods=["POST"])
def delete_category(cid):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id = ?", (cid,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/categories/<int:cid>")
def view_category(cid):
    db = get_db()
    cat = db.execute("SELECT * FROM categories WHERE id = ?", (cid,)).fetchone()
    if not cat:
        return redirect(url_for("index"))
    articles = db.execute(
        "SELECT id, title, url, created_at FROM articles WHERE category_id = ? ORDER BY id DESC",
        (cid,),
    ).fetchall()
    return render_template("category.html", category=cat, articles=articles)


@app.route("/categories/<int:cid>/articles", methods=["POST"])
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
    db = get_db()
    db.execute(
        """
        INSERT INTO articles (category_id, title, url, content_html, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cid, title, url, html, datetime.utcnow().isoformat()),
    )
    db.commit()
    return redirect(url_for("view_category", cid=cid))


@app.route("/articles/<int:aid>/delete", methods=["POST"])
def delete_article(aid):
    db = get_db()
    art = db.execute("SELECT category_id FROM articles WHERE id = ?", (aid,)).fetchone()
    db.execute("DELETE FROM articles WHERE id = ?", (aid,))
    db.commit()
    if art:
        return redirect(url_for("view_category", cid=art["category_id"]))
    return redirect(url_for("index"))


@app.route("/articles/<int:aid>")
def view_article(aid):
    db = get_db()
    art = db.execute(
        """
        SELECT a.*, c.name AS category_name
        FROM articles a JOIN categories c ON c.id = a.category_id
        WHERE a.id = ?
        """,
        (aid,),
    ).fetchone()
    if not art:
        return redirect(url_for("index"))
    return render_template("article.html", article=art, print_mode=False)


@app.route("/articles/<int:aid>/print")
def print_article(aid):
    db = get_db()
    art = db.execute(
        """
        SELECT a.*, c.name AS category_name
        FROM articles a JOIN categories c ON c.id = a.category_id
        WHERE a.id = ?
        """,
        (aid,),
    ).fetchone()
    if not art:
        return redirect(url_for("index"))
    return render_template("article.html", article=art, print_mode=True)


@app.route("/articles/<int:aid>/save", methods=["POST"])
def save_article_html(aid):
    data = request.get_json(silent=True) or {}
    html = data.get("html")
    if html is None:
        return jsonify({"ok": False, "error": "missing html"}), 400
    db = get_db()
    db.execute("UPDATE articles SET content_html = ? WHERE id = ?", (html, aid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/articles/<int:aid>/refresh", methods=["POST"])
def refresh_article(aid):
    db = get_db()
    art = db.execute("SELECT url, category_id FROM articles WHERE id = ?", (aid,)).fetchone()
    if not art:
        return redirect(url_for("index"))
    try:
        _, html = fetch_article(art["url"])
        db.execute("UPDATE articles SET content_html = ? WHERE id = ?", (html, aid))
        db.commit()
    except Exception:
        pass
    return redirect(url_for("view_article", aid=aid))


# Inizializza il database all'avvio (necessario quando l'app è servita da gunicorn)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
