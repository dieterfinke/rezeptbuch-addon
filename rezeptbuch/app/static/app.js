/* Rezeptbuch – Oberfläche (ohne externe Abhängigkeiten, läuft unter HA-Ingress mit relativen Pfaden) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const state = { categories: [], filter: { q: "", kategorie: "", favoriten: false }, pendingScans: [], drafts: [], status: null };

// ---------------------------------------------------------------- Helfer
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function markUnsure(s) { return esc(s).replace(/\[\?\]/g, '<span class="unsure" title="Unsicher gelesen – bitte prüfen">?</span>'); }
function lines(t) { return String(t || "").split("\n").map(l => l.replace(/^[\s\-•]+/, "").trim()).filter(Boolean); }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "Content-Type": "application/json" }; delete o.json; }
  const r = await fetch("api/" + path, o);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.fehler || `Fehler ${r.status}`);
  return data;
}
function catById(id) { return state.categories.find(c => c.id === id); }
function img(name, thumb) { return "bilder/" + (thumb ? name.replace(".jpg", "_t.jpg") : name); }
function lightbox(src) {
  const lb = $("#lightbox"); $("img", lb).src = src; lb.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("click", e => {
  const z = e.target.closest("[data-zoom]");
  if (z) { e.preventDefault(); lightbox(z.dataset.zoom); }
});
async function loadCategories() { state.categories = await api("kategorien"); }

// ---------------------------------------------------------------- Router
const routes = [
  [/^#?\/?$/, showList],
  [/^#\/rezept\/(\d+)$/, m => showRecipe(+m[1])],
  [/^#\/kochen\/(\d+)$/, m => showRecipe(+m[1], true)],
  [/^#\/neu$/, () => showForm(null)],
  [/^#\/bearbeiten\/(\d+)$/, m => showForm(+m[1])],
  [/^#\/scannen$/, showScan],
  [/^#\/kategorien$/, showCategories],
];
async function route() {
  view.className = "";
  releaseWakeLock();
  const h = location.hash || "#/";
  for (const [re, fn] of routes) {
    const m = h.match(re);
    if (m) {
      try { await fn(m); } catch (e) { view.innerHTML = `<div class="empty"><div class="big">⚠︎</div>${esc(e.message)}</div>`; }
      window.scrollTo(0, 0);
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- Liste
async function showList() {
  await loadCategories();
  const f = state.filter;
  const total = state.categories.reduce((a, c) => a + c.anzahl, 0);
  view.innerHTML = `
    <input class="search" type="search" placeholder="Rezept oder Zutat suchen …" value="${esc(f.q)}" id="q">
    <div class="chips" id="chips">
      <button class="chip ${!f.kategorie && !f.favoriten ? "active" : ""}" data-k="">Alle</button>
      <button class="chip ${f.favoriten ? "active" : ""}" data-fav="1">★ Lieblinge</button>
      ${state.categories.map(c => `<button class="chip ${String(f.kategorie) === String(c.id) ? "active" : ""}" data-k="${c.id}">${esc(c.icon)} ${esc(c.name)}<span class="n">${c.anzahl}</span></button>`).join("")}
      <a class="chip" href="#/kategorien">＋ Kategorie</a>
    </div>
    <div id="list"></div>
    <div class="fab-bar">
      <a class="btn primary" href="#/scannen">📷 Rezept scannen</a>
      <a class="btn" href="#/neu">✎ Neu eintippen</a>
    </div>`;
  $("#chips").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    f.favoriten = !!b.dataset.fav; f.kategorie = b.dataset.fav ? "" : b.dataset.k;
    showList();
  });
  let t;
  $("#q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { f.q = e.target.value; renderList(total); }, 200); });
  await renderList(total);
}

async function renderList(total) {
  const f = state.filter, p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.kategorie) p.set("kategorie", f.kategorie);
  if (f.favoriten) p.set("favoriten", "1");
  const rows = await api("rezepte?" + p);
  const el = $("#list"); if (!el) return;
  if (!rows.length) {
    el.innerHTML = total === 0
      ? `<div class="empty"><div class="big">📖</div><h2>Das Rezeptbuch ist noch leer</h2>
         <p>Fotografiere eine Seite aus dem Rezeptheft – die Schrift wird automatisch erkannt.</p>
         <a class="btn primary" href="#/scannen">📷 Erstes Rezept scannen</a></div>`
      : `<div class="empty">Nichts gefunden.</div>`;
    return;
  }
  el.innerHTML = `<div class="grid">${rows.map(r => {
    const c = catById(r.category_id);
    return `<a class="card" href="#/rezept/${r.id}">
      <div class="pic" ${r.bild ? `style="background-image:url('${img(r.bild, true)}')"` : ""}>${r.bild ? "" : esc(c?.icon || "📖")}</div>
      <div class="body"><div class="title">${r.favorite ? "★ " : ""}${esc(r.title)}</div>
      <div class="meta">${esc(c ? c.name : "Ohne Kategorie")}${r.duration ? " · " + esc(r.duration) : ""}</div></div></a>`;
  }).join("")}</div>`;
}

// ---------------------------------------------------------------- Rezept
let wakeLock = null;
async function requestWakeLock() { try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { } }
function releaseWakeLock() { try { wakeLock?.release(); } catch { } wakeLock = null; }

async function showRecipe(id, cook = false) {
  await loadCategories();
  const r = await api("rezepte/" + id);
  const c = catById(r.category_id);
  const fotos = r.images.filter(i => i.kind === "foto"), scans = r.images.filter(i => i.kind === "scan");
  const gallery = [...fotos.map(i => [i, "Foto"]), ...scans.map((i, n) => [i, `Originalseite${scans.length > 1 ? " " + (n + 1) : ""}`])];
  if (cook) { view.className = "cook"; requestWakeLock(); }
  view.innerHTML = `
    ${gallery.length ? `<div class="gallery">${gallery.map(([i, cap]) => `<figure><img src="${img(i.filename, true)}" data-zoom="${img(i.filename)}" alt="${esc(cap)}" loading="lazy"><figcaption>${cap}</figcaption></figure>`).join("")}</div>` : ""}
    <h1>${esc(r.title)}</h1>
    <div class="meta-line">
      <span>${esc(c ? c.icon + " " + c.name : "Ohne Kategorie")}</span>
      ${r.servings ? `<span>🍽 ${esc(r.servings)}</span>` : ""}
      ${r.duration ? `<span>⏱ ${esc(r.duration)}</span>` : ""}
      ${r.source ? `<span>📓 ${esc(r.source)}</span>` : ""}
    </div>
    <div class="row">
      <button class="btn small" id="fav">${r.favorite ? "★ Liebling" : "☆ Als Liebling merken"}</button>
      ${cook ? `<a class="btn small" href="#/rezept/${r.id}">Kochmodus beenden</a>` : `<a class="btn small" href="#/kochen/${r.id}">👩‍🍳 Kochmodus</a>`}
      <a class="btn small" href="#/bearbeiten/${r.id}">✎ Bearbeiten</a>
    </div>
    ${lines(r.ingredients).length ? `<h2>Zutaten</h2><ul class="ingredients">${lines(r.ingredients).map(z => `<li><label><input type="checkbox"><span>${markUnsure(z)}</span></label></li>`).join("")}</ul>` : ""}
    ${lines(r.steps).length ? `<h2>Zubereitung</h2><ol class="steps">${lines(r.steps).map(s => `<li>${markUnsure(s)}</li>`).join("")}</ol>` : ""}
    ${r.notes ? `<h2>Notizen</h2><div class="note">${markUnsure(r.notes)}</div>` : ""}
    ${cook ? "" : `<p style="margin-top:32px"><button class="btn small danger" id="del">Rezept löschen</button></p>`}`;
  $("#fav").onclick = async () => { await api("rezepte/" + id, { method: "PUT", json: { favorite: !r.favorite } }); showRecipe(id, cook); };
  if (cook) {
    view.querySelectorAll(".steps li").forEach(li => li.addEventListener("click", () => {
      view.querySelectorAll(".steps li").forEach(x => x.classList.remove("current")); li.classList.add("current");
    }));
  } else {
    $("#del").onclick = async () => {
      if (!confirm(`„${r.title}" wirklich löschen? Auch die Fotos und Originalseiten werden entfernt.`)) return;
      await api("rezepte/" + id, { method: "DELETE" }); toast("Rezept gelöscht"); location.hash = "#/";
    };
  }
}

// ---------------------------------------------------------------- Formular
async function showForm(id, draft = null) {
  await loadCategories();
  const r = id ? await api("rezepte/" + id) : {
    title: draft?.titel || "", category_id: draft?.category_id ?? (state.filter.kategorie ? +state.filter.kategorie : state.categories[0]?.id),
    servings: draft?.portionen || "", duration: draft?.dauer || "",
    ingredients: (draft?.zutaten || []).join("\n"), steps: (draft?.schritte || []).join("\n"),
    notes: draft?.notizen || "", source: draft?.quelle || "", images: [],
  };
  const pending = id ? [] : state.pendingScans;
  const scanSrcs = [...r.images.filter(i => i.kind === "scan").map(i => img(i.filename)), ...pending.map(f => f._url)];
  view.className = scanSrcs.length ? "wide" : "";
  const unsure = /\[\?\]/.test(r.ingredients + r.steps + r.title + r.notes);

  view.innerHTML = `
    <h1>${id ? "Rezept bearbeiten" : draft ? "Erkanntes Rezept prüfen" : "Neues Rezept"}</h1>
    ${draft && unsure ? `<p class="hint">Stellen mit <b>[?]</b> konnte die Erkennung nicht sicher lesen – bitte mit dem Original vergleichen.</p>` : ""}
    ${state.drafts.length > 1 && draft ? `<p class="hint">Auf den Seiten wurden ${state.drafts.length} Rezepte gefunden. Nach dem Speichern geht es mit dem nächsten weiter.</p>` : ""}
    <div class="side-by-side">
      ${scanSrcs.length ? `<div class="scans panel"><div class="hint" style="margin-bottom:6px">Originalseite – zum Vergrößern antippen</div>${scanSrcs.map(s => `<img src="${s}" data-zoom="${s}" alt="Originalseite">`).join("")}</div>` : ""}
      <form class="edit" id="f">
        <label class="f">Titel<input class="t" name="title" required value="${esc(r.title)}" placeholder="z. B. Mamas Apfelkuchen"></label>
        <div class="two">
          <label class="f">Kategorie<select class="t" name="category_id">
            <option value="">– ohne –</option>
            ${state.categories.map(c => `<option value="${c.id}" ${c.id === r.category_id ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)}</option>`).join("")}
          </select></label>
          <label class="f">Herkunft <small>z. B. „Heft 2, Seite 14"</small><input class="t" name="source" value="${esc(r.source)}"></label>
        </div>
        <div class="two">
          <label class="f">Menge / Portionen<input class="t" name="servings" value="${esc(r.servings)}" placeholder="z. B. 1 Blech"></label>
          <label class="f">Dauer<input class="t" name="duration" value="${esc(r.duration)}" placeholder="z. B. 1 Std."></label>
        </div>
        <label class="f">Zutaten <small>eine Zutat pro Zeile</small><textarea class="t" name="ingredients">${esc(r.ingredients)}</textarea></label>
        <label class="f">Zubereitung <small>ein Schritt pro Zeile</small><textarea class="t" name="steps" style="min-height:220px">${esc(r.steps)}</textarea></label>
        <label class="f">Notizen <small>Randbemerkungen, Erinnerungen, Tipps</small><textarea class="t" name="notes" style="min-height:90px">${esc(r.notes)}</textarea></label>
        <div class="panel">
          <b>Bilder</b>
          <div class="thumbs" id="thumbs" style="margin:10px 0">
            ${r.images.map(i => `<div class="thumb"><img src="${img(i.filename, true)}" data-zoom="${img(i.filename)}" alt=""><button type="button" data-del="${i.id}" title="Bild entfernen">✕</button><div class="hint">${i.kind === "scan" ? "Original" : "Foto"}</div></div>`).join("")}
            ${pending.map(f => `<div class="thumb"><img src="${f._url}" data-zoom="${f._url}" alt=""><div class="hint">Original</div></div>`).join("")}
          </div>
          <div class="row">
            <label class="btn small">📸 Foto vom Gericht<input type="file" accept="image/*" multiple hidden data-kind="foto"></label>
            <label class="btn small">📄 Originalseite<input type="file" accept="image/*" multiple hidden data-kind="scan"></label>
          </div>
          <div id="newimgs" class="thumbs" style="margin-top:8px"></div>
        </div>
        <div class="row">
          <button class="btn primary" type="submit" id="save">Speichern</button>
          <a class="btn" href="${id ? "#/rezept/" + id : "#/"}" id="cancel">Abbrechen</a>
        </div>
      </form>
    </div>`;

  const extra = []; // neu gewählte Bilder: {file, kind, url}
  view.querySelectorAll("input[type=file]").forEach(inp => inp.addEventListener("change", () => {
    for (const file of inp.files) extra.push({ file, kind: inp.dataset.kind, url: URL.createObjectURL(file) });
    $("#newimgs").innerHTML = extra.map(x => `<div class="thumb"><img src="${x.url}" alt=""><div class="hint">${x.kind === "scan" ? "Original" : "Foto"} (neu)</div></div>`).join("");
    inp.value = "";
  }));
  $("#thumbs").addEventListener("click", async e => {
    const b = e.target.closest("[data-del]"); if (!b) return;
    if (!confirm("Bild entfernen?")) return;
    await api("bilder/" + b.dataset.del, { method: "DELETE" }); b.parentElement.remove();
  });
  $("#cancel").onclick = () => { if (draft) { state.drafts = []; state.pendingScans = []; } };

  $("#f").onsubmit = async e => {
    e.preventDefault();
    const btn = $("#save"); btn.disabled = true; btn.textContent = "Speichere …";
    const data = Object.fromEntries(new FormData(e.target));
    try {
      const saved = id ? await api("rezepte/" + id, { method: "PUT", json: data }) : await api("rezepte", { method: "POST", json: data });
      const uploads = [...pending.map(file => ({ file, kind: "scan" })), ...extra];
      for (const kind of ["scan", "foto"]) {
        const files = uploads.filter(u => u.kind === kind);
        if (!files.length) continue;
        const fd = new FormData(); files.forEach(u => fd.append("dateien", u.file));
        await fetch(`api/rezepte/${saved.id}/bilder?art=${kind}`, { method: "POST", body: fd });
      }
      toast("Gespeichert ✓");
      if (draft) {
        state.drafts.shift();
        if (state.drafts.length) { showForm(null, state.drafts[0]); window.scrollTo(0, 0); return; }
        state.pendingScans = [];
      }
      location.hash = "#/rezept/" + saved.id;
    } catch (err) {
      toast(err.message); btn.disabled = false; btn.textContent = "Speichern";
    }
  };
}

// ---------------------------------------------------------------- Scannen
async function showScan() {
  state.status = state.status || await api("status");
  state.pendingScans = []; state.drafts = [];
  const ki = state.status.ki_bereit;
  view.innerHTML = `
    <h1>Rezept scannen</h1>
    <p class="hint">Fotografiere die Seite möglichst gerade, bei Tageslicht und ohne Schatten. Geht ein Rezept über mehrere Seiten, nimm einfach alle Seiten auf.</p>
    ${ki ? "" : `<p class="note">Die automatische Texterkennung ist noch nicht eingerichtet (API-Schlüssel fehlt in den Add-on-Einstellungen). Du kannst die Seite trotzdem als Originalbild speichern und den Text selbst eintippen.</p>`}
    <label class="drop" id="drop">
      <div style="font-size:2.4rem">📷</div>
      <b>Seite fotografieren oder Bilder auswählen</b>
      <div class="hint">Mehrere Seiten möglich</div>
      <input type="file" accept="image/*" capture="environment" multiple hidden id="files">
    </label>
    <p><label class="btn small">🖼 Aus Galerie wählen<input type="file" accept="image/*" multiple hidden id="files2"></label></p>
    <div class="thumbs" id="prev" style="margin:12px 0"></div>
    <div class="row" id="actions" hidden>
      ${ki ? `<button class="btn primary" id="go">✨ Text erkennen</button>` : ""}
      <button class="btn ${ki ? "" : "primary"}" id="manual">Ohne Erkennung weiter</button>
      <button class="btn" id="clear">Neu beginnen</button>
    </div>
    <div id="work"></div>`;

  const add = files => {
    for (const f of files) { f._url = URL.createObjectURL(f); state.pendingScans.push(f); }
    $("#prev").innerHTML = state.pendingScans.map((f, n) => `<div class="thumb"><img src="${f._url}" data-zoom="${f._url}" alt="Seite ${n + 1}"><button type="button" data-rm="${n}">✕</button><div class="hint">Seite ${n + 1}</div></div>`).join("");
    $("#actions").hidden = !state.pendingScans.length;
  };
  $("#files").onchange = e => add(e.target.files);
  $("#files2").onchange = e => add(e.target.files);
  const drop = $("#drop");
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); add(e.dataTransfer.files); });
  $("#prev").addEventListener("click", e => {
    const b = e.target.closest("[data-rm]"); if (!b) return;
    state.pendingScans.splice(+b.dataset.rm, 1); add([]);
  });
  $("#clear").onclick = () => { state.pendingScans = []; add([]); };
  $("#manual").onclick = () => { state.drafts = [{}]; showForm(null, {}); };
  if (ki) $("#go").onclick = async () => {
    $("#actions").hidden = true;
    $("#work").innerHTML = `<div class="panel" style="text-align:center"><div class="spinner"></div>Die Handschrift wird gelesen … das dauert meist 10–40 Sekunden.</div>`;
    try {
      const fd = new FormData(); state.pendingScans.forEach(f => fd.append("dateien", f));
      const r = await fetch("api/erkennen", { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.fehler || "Erkennung fehlgeschlagen");
      if (!data.rezepte?.length) throw new Error("Auf dem Foto wurde kein Rezept gefunden.");
      state.drafts = data.rezepte;
      showForm(null, state.drafts[0]);
    } catch (err) {
      $("#work").innerHTML = `<p class="note">${esc(err.message)}</p>`;
      $("#actions").hidden = false;
    }
  };
}

// ---------------------------------------------------------------- Kategorien
async function showCategories() {
  await loadCategories();
  view.innerHTML = `
    <h1>Kategorien</h1>
    <p class="hint">Symbol und Name lassen sich direkt ändern. Wird eine Kategorie gelöscht, bleiben ihre Rezepte erhalten (dann „ohne Kategorie").</p>
    <div class="panel">
      ${state.categories.map(c => `<div class="cat-row" data-id="${c.id}">
        <input class="t icon" value="${esc(c.icon)}" data-f="icon" aria-label="Symbol">
        <input class="t" value="${esc(c.name)}" data-f="name" aria-label="Name">
        <span class="count">${c.anzahl} Rezepte</span>
        <button class="btn small danger" data-del="${c.id}" title="Löschen">✕</button></div>`).join("")}
      <form class="cat-row" id="new">
        <input class="t icon" name="icon" placeholder="🥗" aria-label="Symbol">
        <input class="t" name="name" placeholder="Neue Kategorie, z. B. Salate" required aria-label="Name">
        <button class="btn small primary">Hinzufügen</button>
      </form>
    </div>
    <p><a class="btn" href="#/">← Zurück zum Rezeptbuch</a></p>`;
  view.querySelectorAll(".cat-row[data-id] input").forEach(inp => inp.addEventListener("change", async () => {
    try { await api("kategorien/" + inp.closest("[data-id]").dataset.id, { method: "PATCH", json: { [inp.dataset.f]: inp.value.trim() } }); toast("Gespeichert ✓"); }
    catch (e) { toast(e.message); }
  }));
  view.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    const c = catById(+b.dataset.del);
    if (!confirm(`Kategorie „${c.name}" löschen?`)) return;
    await api("kategorien/" + c.id, { method: "DELETE" }); showCategories();
  });
  $("#new").onsubmit = async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    try { await api("kategorien", { method: "POST", json: d }); toast("Kategorie angelegt"); showCategories(); }
    catch (err) { toast(err.message); }
  };
}

route();
