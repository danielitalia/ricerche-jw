"""
Migrazione una-tantum: copia categorie e articoli da ricerche.db (SQLite) a Firestore.

Uso:
    python3 migrate_to_firestore.py

Richiede il file serviceAccountKey.json nella stessa cartella.
"""
import os
import sqlite3
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ricerche.db")
KEY_PATH = os.path.join(BASE_DIR, "serviceAccountKey.json")


def main():
    if not os.path.exists(KEY_PATH):
        raise SystemExit("Manca serviceAccountKey.json nella cartella del progetto.")
    if not os.path.exists(DB_PATH):
        raise SystemExit("Manca ricerche.db: niente da migrare.")

    firebase_admin.initialize_app(credentials.Certificate(KEY_PATH))
    db = firestore.client()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Evita duplicati: se ci sono gia categorie su Firestore, chiedi conferma
    existing = list(db.collection("categories").limit(1).stream())
    if existing:
        ans = input("Su Firestore ci sono gia dei dati. Continuo lo stesso? (s/N) ")
        if ans.strip().lower() not in ("s", "si", "sì", "y", "yes"):
            raise SystemExit("Migrazione annullata.")

    id_map = {}  # vecchio id sqlite -> nuovo id firestore
    cats = conn.execute("SELECT * FROM categories").fetchall()
    for c in cats:
        ref = db.collection("categories").document()
        ref.set(
            {
                "name": c["name"],
                "created_at": c["created_at"] or datetime.now(timezone.utc).isoformat(),
            }
        )
        id_map[c["id"]] = ref.id
        print(f"Categoria: {c['name']} -> {ref.id}")

    arts = conn.execute("SELECT * FROM articles").fetchall()
    n = 0
    for a in arts:
        new_cat = id_map.get(a["category_id"])
        if not new_cat:
            continue
        db.collection("articles").document().set(
            {
                "category_id": new_cat,
                "title": a["title"],
                "url": a["url"],
                "content_html": a["content_html"] or "",
                "created_at": a["created_at"] or datetime.now(timezone.utc).isoformat(),
            }
        )
        n += 1
    print(f"\nMigrati {len(cats)} categorie e {n} articoli su Firestore.")
    conn.close()


if __name__ == "__main__":
    main()
