"""Rezeptbuch – Familienrezepte scannen, verwalten und per Sprache abrufen.

Läuft als Home-Assistant-Add-on (Ingress) auf Port 8099.
Daten liegen in /data (SQLite + Bilder) und sind damit Teil der HA-Backups.
"""
import asyncio
import base64
import difflib
import io
import json
import logging
import os
import re
import sqlite3
import uuid
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web
from PIL import Image, ImageOps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rezeptbuch")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
IMG_DIR = DATA_DIR / "bilder"
DB_PATH = DATA_DIR / "rezeptbuch.db"
STATIC_DIR = Path(__file__).parent / "static"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("KI_MODELL", "").strip() or "claude-sonnet-5-5"
PORT = int(os.environ.get("PORT", "8099"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  icon TEXT DEFAULT '📖',
  sort INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recipes(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  servings TEXT DEFAULT '',
  duration TEXT DEFAULT '',
  ingredients TEXT DEFAULT '',
  steps TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  source TEXT DEFAULT '',
  favorite INTEGER DEFAULT 0,
  created TEXT DEFAULT CURRENT_TIMESTAMP,
  updated TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('scan','foto')),
  filename TEXT NOT NULL,
  sort INTEGER DEFAULT 0
);
"""
DEFAULT_CATEGORIES = [("Kochen", "🍲"), ("Grillen", "🔥"), ("Backen", "🧁")]
RECIPE_FIELDS = ("title", "category_id", "servings", "duration", "ingredients",
                 "steps", "notes", "source", "favorite")


# ---------------------------------------------------------------- Datenbank
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)
        if con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            con.executemany("INSERT INTO categories(name, icon, sort) VALUES(?,?,?)",
                            [(n, i, s) for s, (n, i) in enumerate(DEFAULT_CATEGORIES)])


def recipe_dict(con, row, with_images=True) -> dict:
    r = dict(row)
    r["favorite"] = bool(r["favorite"])
    if with_images:
        imgs = con.execute("SELECT id, kind, filename FROM images WHERE recipe_id=? ORDER BY kind DESC, sort, id",
                           (r["id"],)).fetchall()
        r["images"] = [dict(i) for i in imgs]
    return r


def lines(text: str) -> list[str]:
    return [l.strip(" -•\t") for l in (text or "").splitlines() if l.strip(" -•\t")]


# ---------------------------------------------------------------- Bilder
def save_image(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((2400, 2400))
    name = uuid.uuid4().hex
    img.save(IMG_DIR / f"{name}.jpg", "JPEG", quality=85)
    thumb = img.copy()
    thumb.thumbnail((600, 600))
    thumb.save(IMG_DIR / f"{name}_t.jpg", "JPEG", quality=80)
    return f"{name}.jpg"


def image_for_ai(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1568, 1568))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- KI-Erkennung
TRANSCRIBE_PROMPT = """Diese Fotos zeigen Seite(n) aus einem handgeschriebenen oder gedruckten Rezeptbuch einer Mutter.
Übertrage die Rezepte originalgetreu in Text.

Regeln:
- Nichts erfinden und nichts "verbessern". Schreibweise, alte Mengenangaben (Pfund, Tasse, Msp., Pfd., ein Stich Butter) und persönliche Anmerkungen beibehalten.
- Unleserliche Wörter mit [?] markieren, z. B. "2 [?] Zucker". Bist du dir bei einem Wort unsicher, schreibe deine beste Lesart mit [?] dahinter.
- Steht auf den Seiten mehr als ein Rezept, gib jedes einzeln zurück. Geht ein Rezept über mehrere Fotos, fasse es zusammen.
- Randnotizen oder Kommentare (z. B. "Papas Lieblingskuchen") gehören in "notizen".
- Wähle als Kategorie die passendste aus: {kategorien}.

Antworte ausschließlich mit JSON in diesem Format:
{{"rezepte": [{{"titel": "", "kategorie": "", "portionen": "", "dauer": "", "zutaten": ["..."], "schritte": ["..."], "notizen": ""}}]}}"""


async def transcribe(images_b64: list[str], categories: list[str]) -> list[dict]:
    if not API_KEY:
        raise web.HTTPBadRequest(text=json.dumps({"fehler": "Kein Anthropic-API-Schlüssel in den Add-on-Einstellungen hinterlegt."}),
                                 content_type="application/json")
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
               for b in images_b64]
    content.append({"type": "text", "text": TRANSCRIBE_PROMPT.format(kategorien=", ".join(categories))})
    body = {"model": MODEL, "max_tokens": 8000, "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with ClientSession(timeout=ClientTimeout(total=240)) as s:
        async with s.post("https://api.anthropic.com/v1/messages", json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                msg = (data.get("error") or {}).get("message", str(data))
                log.error("KI-Fehler %s: %s", resp.status, msg)
                raise web.HTTPBadGateway(text=json.dumps({"fehler": f"KI-Erkennung fehlgeschlagen: {msg}"}),
                                         content_type="application/json")
    text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise web.HTTPBadGateway(text=json.dumps({"fehler": "Die KI hat kein lesbares Ergebnis geliefert."}),
                                 content_type="application/json")
    parsed = json.loads(m.group(0))
    return parsed.get("rezepte", [])


# ---------------------------------------------------------------- Hilfen
def jerr(status: int, msg: str):
    return web.json_response({"fehler": msg}, status=status)


async def read_json(request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


# ---------------------------------------------------------------- Kategorien
async def list_categories(request):
    with db() as con:
        rows = con.execute("""SELECT c.*, COUNT(r.id) AS anzahl FROM categories c
                              LEFT JOIN recipes r ON r.category_id=c.id
                              GROUP BY c.id ORDER BY c.sort, c.name""").fetchall()
    return web.json_response([dict(r) for r in rows])


async def create_category(request):
    data = await read_json(request)
    name = (data.get("name") or "").strip()
    if not name:
        return jerr(400, "Name fehlt")
    with db() as con:
        try:
            sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM categories").fetchone()[0]
            cur = con.execute("INSERT INTO categories(name, icon, sort) VALUES(?,?,?)",
                              (name, (data.get("icon") or "📖").strip(), sort))
        except sqlite3.IntegrityError:
            return jerr(409, "Diese Kategorie gibt es schon")
        row = con.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone()
    return web.json_response(dict(row), status=201)


async def update_category(request):
    cid = int(request.match_info["id"])
    data = await read_json(request)
    with db() as con:
        for field in ("name", "icon", "sort"):
            if field in data:
                con.execute(f"UPDATE categories SET {field}=? WHERE id=?", (data[field], cid))
        row = con.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    return web.json_response(dict(row)) if row else jerr(404, "Nicht gefunden")


async def delete_category(request):
    cid = int(request.match_info["id"])
    with db() as con:
        con.execute("DELETE FROM categories WHERE id=?", (cid,))
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- Rezepte
async def list_recipes(request):
    q = (request.query.get("q") or "").strip()
    cat = request.query.get("kategorie")
    fav = request.query.get("favoriten")
    sql = """SELECT r.id, r.title, r.category_id, r.favorite, r.servings, r.duration,
             (SELECT filename FROM images i WHERE i.recipe_id=r.id ORDER BY (i.kind='foto') DESC, i.sort, i.id LIMIT 1) AS bild
             FROM recipes r WHERE 1=1"""
    args = []
    if q:
        sql += " AND (r.title LIKE ? OR r.ingredients LIKE ? OR r.notes LIKE ?)"
        args += [f"%{q}%"] * 3
    if cat:
        sql += " AND r.category_id=?"
        args.append(int(cat))
    if fav:
        sql += " AND r.favorite=1"
    sql += " ORDER BY r.title COLLATE NOCASE"
    with db() as con:
        rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["favorite"] = bool(d["favorite"])
        out.append(d)
    return web.json_response(out)


async def get_recipe(request):
    rid = int(request.match_info["id"])
    with db() as con:
        row = con.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
        if not row:
            return jerr(404, "Rezept nicht gefunden")
        return web.json_response(recipe_dict(con, row))


def clean_recipe(data: dict) -> dict:
    out = {}
    for f in RECIPE_FIELDS:
        if f in data:
            v = data[f]
            if f == "favorite":
                v = 1 if v else 0
            elif f == "category_id":
                v = int(v) if v not in (None, "", "null") else None
            else:
                v = (v or "").strip() if isinstance(v, str) else ("\n".join(v) if isinstance(v, list) else str(v or ""))
            out[f] = v
    return out


async def create_recipe(request):
    data = clean_recipe(await read_json(request))
    if not data.get("title"):
        return jerr(400, "Titel fehlt")
    cols = ", ".join(data)
    with db() as con:
        cur = con.execute(f"INSERT INTO recipes({cols}) VALUES({','.join('?' * len(data))})", list(data.values()))
        row = con.execute("SELECT * FROM recipes WHERE id=?", (cur.lastrowid,)).fetchone()
        return web.json_response(recipe_dict(con, row), status=201)


async def update_recipe(request):
    rid = int(request.match_info["id"])
    data = clean_recipe(await read_json(request))
    if "title" in data and not data["title"]:
        return jerr(400, "Titel fehlt")
    with db() as con:
        if data:
            sets = ", ".join(f"{k}=?" for k in data)
            con.execute(f"UPDATE recipes SET {sets}, updated=CURRENT_TIMESTAMP WHERE id=?", [*data.values(), rid])
        row = con.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
        if not row:
            return jerr(404, "Rezept nicht gefunden")
        return web.json_response(recipe_dict(con, row))


async def delete_recipe(request):
    rid = int(request.match_info["id"])
    with db() as con:
        files = [r[0] for r in con.execute("SELECT filename FROM images WHERE recipe_id=?", (rid,))]
        con.execute("DELETE FROM recipes WHERE id=?", (rid,))
    for f in files:
        remove_image_files(f)
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- Bilder-API
def remove_image_files(filename: str) -> None:
    for name in (filename, filename.replace(".jpg", "_t.jpg")):
        try:
            (IMG_DIR / name).unlink()
        except FileNotFoundError:
            pass


async def upload_images(request):
    rid = int(request.match_info["id"])
    kind = request.query.get("art", "foto")
    if kind not in ("scan", "foto"):
        return jerr(400, "art muss scan oder foto sein")
    reader = await request.multipart()
    saved = []
    with db() as con:
        if not con.execute("SELECT 1 FROM recipes WHERE id=?", (rid,)).fetchone():
            return jerr(404, "Rezept nicht gefunden")
        sort = con.execute("SELECT COALESCE(MAX(sort),0) FROM images WHERE recipe_id=?", (rid,)).fetchone()[0]
        async for part in reader:
            if part.name != "dateien":
                continue
            raw = await part.read(decode=False)
            try:
                fname = await asyncio.to_thread(save_image, raw)
            except Exception as e:
                log.warning("Bild konnte nicht gelesen werden: %s", e)
                continue
            sort += 1
            cur = con.execute("INSERT INTO images(recipe_id, kind, filename, sort) VALUES(?,?,?,?)",
                              (rid, kind, fname, sort))
            saved.append({"id": cur.lastrowid, "kind": kind, "filename": fname})
    return web.json_response(saved, status=201)


async def delete_image(request):
    iid = int(request.match_info["id"])
    with db() as con:
        row = con.execute("SELECT filename FROM images WHERE id=?", (iid,)).fetchone()
        if not row:
            return jerr(404, "Bild nicht gefunden")
        con.execute("DELETE FROM images WHERE id=?", (iid,))
    remove_image_files(row["filename"])
    return web.json_response({"ok": True})


async def serve_image(request):
    name = request.match_info["name"]
    if not re.fullmatch(r"[0-9a-f]{32}(_t)?\.jpg", name):
        raise web.HTTPNotFound()
    path = IMG_DIR / name
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def transcribe_handler(request):
    reader = await request.multipart()
    pages = []
    async for part in reader:
        if part.name == "dateien":
            raw = await part.read(decode=False)
            pages.append(await asyncio.to_thread(image_for_ai, raw))
    if not pages:
        return jerr(400, "Keine Fotos übermittelt")
    with db() as con:
        cats = [r[0] for r in con.execute("SELECT name FROM categories ORDER BY sort")]
    rezepte = await transcribe(pages, cats)
    by_name = {}
    with db() as con:
        for r in con.execute("SELECT id, name FROM categories"):
            by_name[r["name"].lower()] = r["id"]
    for r in rezepte:
        r["category_id"] = by_name.get((r.get("kategorie") or "").lower())
    return web.json_response({"rezepte": rezepte})


async def status_handler(request):
    return web.json_response({"ki_bereit": bool(API_KEY), "modell": MODEL})


# ---------------------------------------------------------------- Sprache (Assist)
STOPWORDS = {"das", "die", "der", "den", "dem", "ein", "eine", "einen", "rezept", "rezepte", "für", "fuer",
             "von", "mamas", "mama", "mutters", "omas", "bitte", "mit", "mir"}
session = {"recipe_id": None, "step": -1}


def normalize(text: str) -> str:
    text = text.lower().replace("ß", "ss")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        text = text.replace(a, b)
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in STOPWORDS]
    return " ".join(words)


def find_recipe(con, name: str):
    query = normalize(name)
    if not query:
        return None
    rows = con.execute("SELECT * FROM recipes").fetchall()
    best, best_score = None, 0.0
    qwords = set(query.split())
    for r in rows:
        t = normalize(r["title"])
        if not t:
            continue
        if t == query:
            return r
        score = difflib.SequenceMatcher(None, query, t).ratio()
        overlap = len(qwords & set(t.split())) / max(len(qwords), 1)
        score = max(score, overlap * 0.95)
        if query in t or t in query:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.55 else None


def speak_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " und " + items[-1]


def clean_for_speech(text: str) -> str:
    return text.replace("[?]", "").replace("  ", " ").strip()


def step_answer(r, idx: int) -> dict:
    steps = lines(r["steps"])
    if not steps:
        return {"antwort": f"Für {r['title']} sind keine Arbeitsschritte eingetragen."}
    if idx >= len(steps):
        session["step"] = len(steps)
        return {"antwort": f"Das war der letzte Schritt. Guten Appetit mit {r['title']}!"}
    idx = max(idx, 0)
    session["step"] = idx
    last = " Das ist der letzte Schritt." if idx == len(steps) - 1 else ""
    return {"antwort": f"Schritt {idx + 1} von {len(steps)}: {clean_for_speech(steps[idx])}.{last}",
            "schritt": idx + 1, "schritte_gesamt": len(steps)}


async def voice_handler(request):
    action = request.match_info["aktion"]
    name = (request.query.get("name") or "").strip()
    with db() as con:
        if action in ("start", "zutaten"):
            r = find_recipe(con, name)
            if not r:
                return web.json_response({"gefunden": False,
                                          "antwort": f"Ich habe kein Rezept gefunden, das zu {name or 'deiner Anfrage'} passt."})
            session["recipe_id"], session["step"] = r["id"], -1
            zutaten = [clean_for_speech(z) for z in lines(r["ingredients"])]
            steps = lines(r["steps"])
            portion = f" für {r['servings']}" if r["servings"] else ""
            text = f"{r['title']}{portion}. Du brauchst: {speak_list(zutaten)}." if zutaten else \
                f"Für {r['title']} sind keine Zutaten eingetragen."
            if action == "start" and steps:
                text += f" Das Rezept hat {len(steps)} Schritte. Sag einfach: nächster Schritt."
            return web.json_response({"gefunden": True, "titel": r["title"], "antwort": text})

        if action in ("weiter", "zurueck", "wiederholen", "schritt"):
            rid = session["recipe_id"]
            if name:
                r = find_recipe(con, name)
                if r:
                    rid, session["recipe_id"], session["step"] = r["id"], r["id"], -1
            r = con.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone() if rid else None
            if not r:
                return web.json_response({"antwort": "Es läuft gerade kein Rezept. Sag zum Beispiel: koch mit mir Apfelkuchen."})
            cur = session["step"]
            if action == "weiter":
                idx = cur + 1
            elif action == "zurueck":
                idx = max(cur - 1, 0)
            elif action == "schritt":
                try:
                    idx = int(request.query.get("nr", "1")) - 1
                except ValueError:
                    idx = 0
            else:
                idx = max(cur, 0)
            return web.json_response(step_answer(r, idx))

        if action == "liste":
            kat = (request.query.get("kategorie") or name).strip()
            cats = {normalize(c["name"]): c for c in con.execute("SELECT * FROM categories")}
            match = difflib.get_close_matches(normalize(kat), list(cats), n=1, cutoff=0.5) if kat else []
            if match:
                c = cats[match[0]]
                titles = [r[0] for r in con.execute(
                    "SELECT title FROM recipes WHERE category_id=? ORDER BY favorite DESC, title", (c["id"],))]
                label = c["name"]
            else:
                titles = [r[0] for r in con.execute("SELECT title FROM recipes ORDER BY favorite DESC, title")]
                label = "deinem Rezeptbuch"
            if not titles:
                return web.json_response({"antwort": f"In {label} ist noch kein Rezept."})
            shown = titles[:8]
            more = f" und {len(titles) - 8} weitere" if len(titles) > 8 else ""
            if len(titles) == 1:
                return web.json_response({"antwort": f"In {label} gibt es ein Rezept: {titles[0]}."})
            return web.json_response({"antwort": f"In {label} gibt es {len(titles)} Rezepte: {speak_list(shown)}{more}."})

    return jerr(404, "Unbekannte Aktion")


# ---------------------------------------------------------------- App
async def index(request):
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def make_app() -> web.Application:
    app = web.Application(client_max_size=60 * 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/api/status", status_handler),
        web.get("/api/kategorien", list_categories),
        web.post("/api/kategorien", create_category),
        web.patch("/api/kategorien/{id:\\d+}", update_category),
        web.delete("/api/kategorien/{id:\\d+}", delete_category),
        web.get("/api/rezepte", list_recipes),
        web.post("/api/rezepte", create_recipe),
        web.get("/api/rezepte/{id:\\d+}", get_recipe),
        web.put("/api/rezepte/{id:\\d+}", update_recipe),
        web.delete("/api/rezepte/{id:\\d+}", delete_recipe),
        web.post("/api/rezepte/{id:\\d+}/bilder", upload_images),
        web.delete("/api/bilder/{id:\\d+}", delete_image),
        web.post("/api/erkennen", transcribe_handler),
        web.get("/api/sprache/{aktion}", voice_handler),
        web.get("/bilder/{name}", serve_image),
        web.static("/static", STATIC_DIR),
    ])
    return app


if __name__ == "__main__":
    init_db()
    log.info("Rezeptbuch startet auf Port %s (KI-Modell: %s, KI bereit: %s)", PORT, MODEL, bool(API_KEY))
    web.run_app(make_app(), host="0.0.0.0", port=PORT, access_log=None)
