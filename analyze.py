r"""Analysiert label_tree.txt und erzeugt eine eigenstaendige HTML-GUI.

Verwendung:
    python analyze.py                     # label_tree.txt -> label_analyse.html + Browser
    python analyze.py tree.txt -o out.html --no-open

Die erzeugte HTML-Datei ist komplett eigenstaendig (kein Server noetig)
und kann per Doppelklick im Browser geoeffnet werden.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Ein Prefix-Element ist entweder "│   " oder "    " (je 4 Zeichen),
# danach kommt der Connector "├── " bzw. "└── ".
CONN = re.compile(r"^((?:│   |    )*)[├└]── (.*)$")
SIZE = re.compile(r"^(.*?)\s*\((\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\)\s*$")
UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_tree(path: Path) -> list:
    """Parst die Tree-Datei. Rueckgabe: verschachtelte Liste.
    Datei: [name, groesse_bytes]  |  Ordner: [name, [kinder]]  |  Fehler: [name, -1]
    """
    root = None
    stack: list[tuple[int, list]] = []  # (tiefe, kinder-liste des ordners)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CONN.match(line)
        if not m:
            if root is None and line.strip():
                root = [line.strip().rstrip("/\\"), []]
                stack = [(0, root[1])]
            continue
        if root is None:
            continue
        depth = len(m.group(1)) // 4 + 1
        raw = m.group(2).strip()
        if raw.startswith("[FEHLER"):
            node = [raw, -1]
        elif raw.endswith("/"):
            node = [raw[:-1], []]
        else:
            sm = SIZE.match(raw)
            if sm:
                node = [sm.group(1), int(float(sm.group(2)) * UNITS[sm.group(3)])]
            else:
                node = [raw, 0]
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1].append(node)
        if isinstance(node[1], list):
            stack.append((depth, node[1]))
    if root is None:
        sys.exit("Kein Baum in der Datei gefunden.")
    return root


def count(nodes: list) -> tuple[int, int]:
    d = f = 0
    for n in nodes:
        if isinstance(n[1], list):
            d += 1
            cd, cf = count(n[1])
            d += cd
            f += cf
        else:
            f += 1
    return d, f


HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Label-Tree Analyse</title>
<style>
:root{--bg:#17181c;--bg2:#1f2127;--bg3:#262932;--fg:#e4e6eb;--dim:#9aa0aa;
--acc:#5b9dff;--warn:#e8b339;--bad:#ef5f6b;--ok:#4fc08d;--bd:#33363f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg2);border-bottom:1px solid var(--bd);z-index:10;padding:8px 16px}
h1{font-size:16px;margin:0 0 4px;display:inline-block}
#meta{color:var(--dim);font-size:12px;margin-left:12px}
nav{margin-top:6px;display:flex;gap:4px;flex-wrap:wrap}
nav button{background:var(--bg3);color:var(--fg);border:1px solid var(--bd);border-radius:6px;
padding:5px 14px;cursor:pointer;font-size:13px}
nav button.on{background:var(--acc);color:#0b1220;border-color:var(--acc);font-weight:600}
nav .cnt{background:#0004;border-radius:8px;padding:0 6px;font-size:11px;margin-left:4px}
main{padding:16px;max-width:1400px;margin:0 auto}
section{display:none}section.on{display:block}
input,select{background:var(--bg3);color:var(--fg);border:1px solid var(--bd);border-radius:6px;
padding:6px 10px;font-size:13px}
input:focus,select:focus{outline:none;border-color:var(--acc)}
label.chk{color:var(--dim);margin:0 10px 0 4px;font-size:13px;user-select:none;cursor:pointer}
table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}
th{position:sticky;top:0;background:var(--bg3);text-align:left;padding:6px 8px;border-bottom:2px solid var(--bd);cursor:pointer;user-select:none}
td{padding:4px 8px;border-bottom:1px solid var(--bd);vertical-align:top}
tr:hover td{background:#ffffff08}
td.p{color:var(--dim);font-size:12px;word-break:break-all}
td.n{white-space:nowrap}
.r{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;
padding:0 7px;font-size:11px;margin:1px 2px;color:var(--warn);white-space:nowrap}
.badge.bad{color:var(--bad);border-color:#5a2a30}
.badge.ok{color:var(--ok)}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:12px 16px;margin:0 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:14px}
.stat .v{font-size:24px;font-weight:700}
.stat .l{color:var(--dim);font-size:12px}
.bar{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px}
.bar .k{width:110px;text-align:right;color:var(--dim);overflow:hidden;text-overflow:ellipsis}
.bar .b{height:14px;background:var(--acc);border-radius:3px;min-width:2px}
.bar .c{width:70px;color:var(--dim)}
details{margin:4px 0}
summary{cursor:pointer;padding:5px 8px;border-radius:6px;user-select:none}
summary:hover{background:var(--bg3)}
details[open] summary{background:var(--bg3)}
details>div{margin:4px 0 8px 22px}
.tree{font:13px/1.6 Consolas,monospace}
.tree .row{white-space:nowrap;padding:0 6px;border-radius:4px;cursor:default}
.tree .row:hover{background:var(--bg3)}
.tree .dir{color:var(--acc);cursor:pointer;font-weight:600}
.tree .sz{color:var(--dim);font-size:11px;margin-left:8px}
.tree .hit{background:#3a3f1f}
.tree .kids{margin-left:20px;border-left:1px dotted var(--bd);padding-left:8px}
.flag{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--warn);margin-left:6px}
.flag.bad{background:var(--bad)}
a.open{color:inherit;text-decoration:none;cursor:pointer}
a.open:hover{color:var(--acc);text-decoration:underline}
a.open::after{content:"↗";font-size:10px;color:var(--dim);margin-left:3px}
a.open:hover::after{color:var(--acc)}
.cp{background:none;border:none;color:var(--dim);cursor:pointer;font-size:12px;padding:0 2px}
.cp:hover{color:var(--acc)}
select.st{padding:1px 4px;font-size:11px;max-width:85px}
button.cm{background:none;border:none;color:var(--dim);cursor:pointer;font-size:12px;padding:0 2px}
button.cm:hover{color:var(--acc)}
.cmv{color:var(--dim);font-size:11px;font-style:italic;cursor:pointer;max-width:200px;
display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.cmv:hover{color:var(--fg)}
button.act{background:var(--bg3);color:var(--fg);border:1px solid var(--bd);border-radius:6px;
padding:5px 10px;cursor:pointer;font-size:12px}
button.act:hover{border-color:var(--acc)}
h3{margin:18px 0 8px;font-size:14px}
.mut{color:var(--dim)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
#srch{width:340px}
.note{color:var(--dim);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<header>
<h1>Label-Tree Analyse</h1><span id="meta"></span>
<nav id="nav">
<button data-tab="overview" class="on">Übersicht</button>
<button data-tab="search">Suche</button>
<button data-tab="tree">Baum</button>
<button data-tab="dupes">Duplikate</button>
<button data-tab="problems">Probleme</button>
<button data-tab="marks">Markiert</button>
</nav>
</header>
<main>
<section id="tab-overview" class="on"></section>
<section id="tab-search">
<div class="toolbar">
<input id="srch" placeholder="Suchen… (Name, z.B. 1043 oder 100x40)">
<label class="chk"><input type="checkbox" id="s-regex"> Regex</label>
<label class="chk"><input type="checkbox" id="s-path"> ganzer Pfad</label>
<select id="s-type"><option value="all">Alles</option><option value="f">nur Dateien</option><option value="d">nur Ordner</option></select>
<select id="s-ext"><option value="">alle Endungen</option></select>
<span class="mut" id="s-count"></span>
</div>
<div id="s-res"></div>
</section>
<section id="tab-tree">
<div class="toolbar">
<input id="t-filter" placeholder="Im Baum suchen… (Treffer werden markiert)">
<button id="t-collapse">Alles zuklappen</button>
<span class="mut" id="t-info"></span>
</div>
<div class="tree" id="tree"></div>
</section>
<section id="tab-dupes"></section>
<section id="tab-problems"></section>
<section id="tab-marks">
<div class="toolbar">
<select id="m-status"></select>
<button class="act" id="m-export">Export JSON</button>
<button class="act" id="m-export-del">Löschliste (.txt)</button>
<button class="act" id="m-import-btn">Import JSON</button>
<input type="file" id="m-import" accept=".json" style="display:none">
<button class="act" id="m-clear">Alle zurücksetzen</button>
<span class="mut" id="m-count"></span>
</div>
<div id="m-res"></div>
<div class="note">Markierungen liegen im Browser-Speicher (localStorage) — bleiben erhalten, solange du die
GUI immer auf demselben Weg öffnest (empfohlen: label_analyse.html per Doppelklick). Export/Import zum Sichern oder Weitergeben.</div>
</section>
</main>
<script>
"use strict";
const DATA = __DATA__;
const esc = s => s.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtSize = n => { for (const u of ["B","KB","MB","GB","TB"]) { if (n < 1024 || u === "TB") return u === "B" ? n + " B" : n.toFixed(1) + " " + u; n /= 1024; } };

// ---------- Daten flach machen ----------
const flat = [];
const rootE = (function walk(node, path, parent, depth) {
  const isDir = Array.isArray(node[1]);
  const e = { name: node[0], path, parent, depth, isDir,
              size: isDir ? 0 : Math.max(0, node[1]),
              err: node[1] === -1, children: isDir ? [] : null, _files: 0, _p: null };
  flat.push(e);
  if (isDir) for (const c of node[1]) e.children.push(walk(c, path + "/" + c[0], e, depth + 1));
  return e;
})(DATA.root, DATA.root[0], null, 0);

// Dateien auf Vorfahren zaehlen
for (const e of flat) if (!e.isDir) { let p = e.parent; while (p) { p._files++; p = p.parent; } }

const extOf = e => { const i = e.name.lastIndexOf("."); return i > 0 ? e.name.slice(i + 1).toLowerCase() : ""; };

// ---------- Problemerkennung ----------
const RULES = [
  ["btw_variant", "Veränderte .btw-Endung", "Endung sieht aus wie eine kaputte .btw-Datei (z.B. „.btw alt“, „._btw“, „.1btw“)"],
  ["doubledot", "Doppelter Punkt im Namen", "z.B. „name..jpg“ – meist Tippfehler"],
  ["space", "Leerzeichen im Namen", "Kann in Skripten/Datenbanken Probleme machen"],
  ["multiext", "Doppelte Endung", "z.B. „.btw.pdf“ – ggf. beabsichtigt (Export)"],
  ["junk", "System-/Restdatei", "Thumbs.db, .DS_Store, desktop.ini, *.tmp, *.bak, *.lnk"],
  ["name_vs_dir", "Passt nicht zur Ordnernummer", "Datei liegt in einem Nummern-Ordner, beginnt aber nicht mit dessen Nummer"],
  ["no_btw", "Nummern-Ordner ohne .btw", "Ordner mit Nummernname enthält keine .btw-Datei"],
  ["empty_dir", "Leerer Ordner", "Ordner ohne Inhalt"],
  ["noext", "Keine/ungewöhnliche Endung", "Datei ohne erkennbare Endung oder Endung mit Sonderzeichen"],
];
const JUNK = new Set(["thumbs.db", "desktop.ini", ".ds_store", "ds_store"]);
const JUNK_EXT = new Set(["tmp", "bak", "lnk", "old", "$$$"]);

function findProblems(e) {
  const p = [], nm = e.name, low = nm.toLowerCase();
  if (e.isDir) {
    if (e.children.length === 0) p.push("empty_dir");
    else if (/^\d{3,}$/.test(nm) && !e.children.some(c => !c.isDir && c.name.toLowerCase().endsWith(".btw")))
      p.push("no_btw");
    return p;
  }
  const ext = extOf(e);
  if (/\s/.test(nm)) p.push("space");
  if (/\.\./.test(nm)) p.push("doubledot");
  if (ext.includes("btw") && ext !== "btw") p.push("btw_variant");
  if (!ext || !/^[a-z0-9]{1,6}$/.test(ext)) { if (!ext || !ext.includes("btw")) p.push("noext"); }
  if (/\.[a-z0-9]{1,6}\.[a-z0-9]{1,6}$/.test(low)) p.push("multiext");
  if (JUNK.has(low) || JUNK_EXT.has(ext)) p.push("junk");
  if (e.parent && /^\d{3,}$/.test(e.parent.name) && !p.includes("junk")
      && !low.replace(/^[^a-z0-9]+/, "").startsWith(e.parent.name.toLowerCase()))
    p.push("name_vs_dir");
  return p;
}
for (const e of flat) e._p = findProblems(e);

const probMap = {};
for (const r of RULES) probMap[r[0]] = [];
for (const e of flat) for (const id of e._p) probMap[id].push(e);

// ---------- Duplikate ----------
const byName = new Map(), byNorm = new Map();
const norm = s => s.toLowerCase().replace(/\.{2,}/g, ".").replace(/[\s_\-]+/g, "");
for (const e of flat) {
  if (e.isDir || e.err) continue;
  const k = e.name.toLowerCase();
  (byName.get(k) || byName.set(k, []).get(k)).push(e);
  const nk = norm(e.name);
  (byNorm.get(nk) || byNorm.set(nk, []).get(nk)).push(e);
}
const dupExact = [...byName.values()].filter(g => g.length > 1).sort((a, b) => b.length - a.length);
const dupSimilar = [...byNorm.values()]
  .filter(g => new Set(g.map(e => e.name.toLowerCase())).size > 1)
  .sort((a, b) => b.length - a.length);

// ---------- Markierungen & Kommentare (localStorage) ----------
const STATI = ["", "prüfen", "unklar", "ok", "löschen"];
const STCOL = { "prüfen": "var(--warn)", "unklar": "#c792ea", "ok": "var(--ok)", "löschen": "var(--bad)" };
const EMPTY = { s: "", c: "" };
const store = {
  key: "labelmarks|" + DATA.root[0], data: {},   // Schluessel = Wurzelpfad, stabil bei Dateiumzug
  load() {
    try { this.data = JSON.parse(localStorage.getItem(this.key) || "{}"); } catch { this.data = {}; }
    try {   // Alt-Daten aus frueheren Speicherorten uebernehmen
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("labelmarks|") && k !== this.key) {
          const old = JSON.parse(localStorage.getItem(k) || "{}");
          for (const p in old) if (!(p in this.data)) this.data[p] = old[p];
        }
      }
    } catch {}
  },
  save() { localStorage.setItem(this.key, JSON.stringify(this.data)); },
  get(e) { return this.data[e.path] || EMPTY; },
  set(e, patch) {
    const c = { ...this.data[e.path], ...patch };
    if (!c.s && !c.c) delete this.data[e.path]; else this.data[e.path] = c;
    this.save();
  }
};
store.load();
const byPath = new Map(flat.map(e => [e.path, e]));

// ---------- Helfer ----------
const flagHtml = e => e._p.length
  ? `<span class="flag${e._p.some(x => ["btw_variant","doubledot","noext"].includes(x)) ? " bad" : ""}" title="${esc(e._p.map(id => RULES.find(r => r[0] === id)[1]).join("\n"))}"></span>` : "";
// UNC-Pfad (\\server\share\...) und Explorer-Link fuer einen Eintrag
const uncPath = e => "\\\\" + e.path.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\//g, "\\");
const openLink = e => "labelex:///" + (e.isDir ? "o" : "s") + "/" +
  e.path.replace(/\\/g, "/").replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/");
const nameCell = e => `<a class="open" href="${openLink(e)}" title="${e.isDir ? "Ordner" : "Ordner öffnen und markieren"}: ${esc(uncPath(e))}">${esc(e.name)}</a>` +
  `<button class="cp" data-p="${esc(uncPath(e))}" title="Pfad kopieren">⧉</button>`;
const markCell = e => {
  if (e.err) return "<td></td><td></td>";
  const m = store.get(e);
  return `<td class="n"><select class="st" data-p="${esc(e.path)}" style="color:${STCOL[m.s] || "inherit"}">` +
    STATI.map(s => `<option${s === m.s ? " selected" : ""} value="${s}">${s || "—"}</option>`).join("") +
    `</select><button class="cm" data-p="${esc(e.path)}" title="Kommentar bearbeiten" style="${m.c ? "color:var(--acc)" : ""}">✎</button></td>` +
    `<td><span class="cmv" data-p="${esc(e.path)}" title="${esc(m.c)}">${esc(m.c)}</span></td>`;
};
const rowHtml = (e, showPath) => `<tr><td class="n">${nameCell(e)}${flagHtml(e)}</td>` +
  (showPath ? `<td class="p">${esc(e.parent ? e.parent.path : "")}</td>` : "") +
  `<td class="r n">${e.isDir ? e._files + " Dateien" : fmtSize(e.size)}</td>` +
  `<td class="n mut">${e.isDir ? "Ordner" : "." + (extOf(e) || "—")}</td>` + markCell(e) + `</tr>`;
const table = (rows, showPath = true) => `<table><tr><th>Name</th>${showPath ? "<th>Pfad</th>" : ""}<th class="r">Größe</th><th>Typ</th><th>Status</th><th>Kommentar</th></tr>${rows.join("")}</table>`;

// ---------- Tabs ----------
document.getElementById("nav").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("main section").forEach(s => s.classList.toggle("on", s.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "marks") renderMarks();
});

// ---------- Übersicht ----------
(function () {
  const files = flat.filter(e => !e.isDir), dirs = flat.filter(e => e.isDir);
  const total = files.reduce((a, e) => a + e.size, 0);
  const nProb = flat.filter(e => e._p.length).length;
  const extC = new Map();
  for (const e of files) extC.set(extOf(e) || "(keine)", (extC.get(extOf(e) || "(keine)") || 0) + 1);
  const topExt = [...extC.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25);
  const maxC = topExt[0]?.[1] || 1;
  const big = [...files].sort((a, b) => b.size - a.size).slice(0, 20);
  const fullDirs = [...dirs].sort((a, b) => b._files - a._files).slice(0, 15);
  const junkSize = probMap.junk.reduce((a, e) => a + e.size, 0);

  document.getElementById("tab-overview").innerHTML = `
  <div class="grid">
    <div class="card stat"><div class="v">${dirs.length.toLocaleString("de")}</div><div class="l">Ordner</div></div>
    <div class="card stat"><div class="v">${files.length.toLocaleString("de")}</div><div class="l">Dateien</div></div>
    <div class="card stat"><div class="v">${fmtSize(total)}</div><div class="l">Gesamtgröße</div></div>
    <div class="card stat"><div class="v" style="color:var(--warn)">${nProb.toLocaleString("de")}</div><div class="l">Einträge mit Problemen</div></div>
    <div class="card stat"><div class="v" style="color:var(--warn)">${dupExact.length.toLocaleString("de")}</div><div class="l">Duplikat-Gruppen</div></div>
    <div class="card stat"><div class="v" style="color:var(--acc)">${Object.keys(store.data).length.toLocaleString("de")}</div><div class="l">Markierte Einträge</div></div>
    <div class="card stat"><div class="v">${fmtSize(junkSize)}</div><div class="l">in System-/Restdateien</div></div>
  </div>
  <div class="card"><h3>Dateiendungen (Top 25)</h3>${topExt.map(([k, c]) =>
    `<div class="bar"><span class="k">.${esc(k)}</span><div class="b" style="width:${(c / maxC * 100).toFixed(1)}%"></div><span class="c">${c.toLocaleString("de")}</span></div>`).join("")}</div>
  <div class="card"><h3>Probleme im Überblick</h3>${RULES.map(([id, label]) =>
    probMap[id].length ? `<div class="bar"><span class="k">${esc(label)}</span><div class="b" style="width:${(probMap[id].length / Math.max(...RULES.map(r => probMap[r[0]].length), 1) * 100).toFixed(1)}%;background:var(--warn)"></div><span class="c">${probMap[id].length.toLocaleString("de")}</span></div>` : "").join("") || "<span class=mut>Keine gefunden.</span>"}</div>
  <div class="card"><h3>Größte Dateien</h3>${table(big.map(e => rowHtml(e, true)))}</div>
  <div class="card"><h3>Ordner mit den meisten Dateien</h3>${table(fullDirs.map(e => rowHtml(e, true)))}</div>`;
})();

// ---------- Suche ----------
const srch = document.getElementById("srch");
const sExt = document.getElementById("s-ext");
(function () {
  const exts = [...new Set(flat.filter(e => !e.isDir).map(extOf))].sort();
  sExt.innerHTML = '<option value="">alle Endungen</option>' + exts.map(x => `<option>${esc(x)}</option>`).join("");
})();
let sTimer;
function doSearch() {
  const q = srch.value.trim();
  const useRe = document.getElementById("s-regex").checked;
  const inPath = document.getElementById("s-path").checked;
  const type = document.getElementById("s-type").value;
  const ext = sExt.value;
  const res = document.getElementById("s-res"), cnt = document.getElementById("s-count");
  if (!q && !ext) { res.innerHTML = ""; cnt.textContent = ""; return; }
  let re = null;
  if (q) {
    try { re = useRe ? new RegExp(q, "i") : new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"); }
    catch { res.innerHTML = '<div class="note">Ungültiger Regex.</div>'; return; }
  }
  const out = [];
  for (const e of flat) {
    if (e === rootE || e.err) continue;
    if (type === "f" && e.isDir) continue;
    if (type === "d" && !e.isDir) continue;
    if (ext && (e.isDir || extOf(e) !== ext)) continue;
    if (re && !re.test(inPath ? e.path : e.name)) continue;
    out.push(e);
    if (out.length > 2000) break;
  }
  cnt.textContent = out.length > 2000 ? "> 2000 Treffer (verfeinern!)" : out.length.toLocaleString("de") + " Treffer";
  res.innerHTML = table(out.slice(0, 2000).map(e => rowHtml(e, true)));
}
for (const id of ["s-regex", "s-path", "s-type", "s-ext"])
  document.getElementById(id).addEventListener("input", doSearch);
srch.addEventListener("input", () => { clearTimeout(sTimer); sTimer = setTimeout(doSearch, 120); });

// Pfad-Kopierbuttons (⧉)
document.addEventListener("click", ev => {
  const b = ev.target.closest(".cp"); if (!b) return;
  const done = () => { b.textContent = "✓"; setTimeout(() => b.textContent = "⧉", 900); };
  if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.p).then(done, done);
  else { const t = document.createElement("textarea"); t.value = b.dataset.p;
    document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); done(); }
});

// Status-Dropdowns + Kommentare (delegiert)
document.addEventListener("change", ev => {
  const s = ev.target.closest("select.st"); if (!s) return;
  const e = byPath.get(s.dataset.p); if (!e) return;
  store.set(e, { s: s.value });
  s.style.color = STCOL[s.value] || "inherit";
});
document.addEventListener("click", ev => {
  const b = ev.target.closest("button.cm, .cmv"); if (!b) return;
  const e = byPath.get(b.dataset.p); if (!e) return;
  const v = prompt("Kommentar:\n" + uncPath(e), store.get(e).c);
  if (v === null) return;
  store.set(e, { c: v.trim() });
  const m = store.get(e);
  document.querySelectorAll(".cmv").forEach(x => { if (x.dataset.p === e.path) { x.textContent = m.c; x.title = m.c; } });
  document.querySelectorAll("button.cm").forEach(x => { if (x.dataset.p === e.path) x.style.color = m.c ? "var(--acc)" : ""; });
});

// ---------- Baum ----------
const treeEl = document.getElementById("tree");
const expanded = new Set();
const hitSet = new Set();
function renderTree() {
  const fr = document.createDocumentFragment();
  let rendered = 0;
  const build = (e, host) => {
    if (rendered > 6000) return;
    const row = document.createElement("div");
    row.className = "row" + (e.isDir ? " dir" : "") + (hitSet.has(e) ? " hit" : "");
    row.innerHTML = (e.isDir ? (expanded.has(e) ? "▾ " : "▸ ") : "  ") +
      `<a class="open" href="${openLink(e)}" title="${esc(uncPath(e))}">` +
      esc(e.name) + "</a>" + (e.isDir ? "/" : "") + flagHtml(e) +
      (e.isDir ? `<span class="sz">${e._files} Dateien</span>` : `<span class="sz">${fmtSize(e.size)}</span>`) +
      (e.err ? "" : ` <select class="st" data-p="${esc(e.path)}" style="color:${STCOL[store.get(e).s] || "inherit"}">` +
        STATI.map(s => `<option${s === store.get(e).s ? " selected" : ""}>${s || "—"}</option>`).join("") + `</select>` +
        `<button class="cm" data-p="${esc(e.path)}" title="${esc(store.get(e).c) || "Kommentar"}" style="${store.get(e).c ? "color:var(--acc)" : ""}">✎</button>`);
    if (e.isDir) row.onclick = ev => { if (ev.target.closest("a,select,button")) return;
      expanded.has(e) ? expanded.delete(e) : expanded.add(e); renderTree(); };
    host.appendChild(row); rendered++;
    if (e.isDir && expanded.has(e)) {
      const kids = document.createElement("div");
      kids.className = "kids";
      for (const c of e.children) build(c, kids);
      host.appendChild(kids);
    }
  };
  const rootRow = document.createElement("div");
  rootRow.className = "row dir";
  rootRow.innerHTML = "▾ " + esc(rootE.name) + "/";
  rootRow.onclick = () => { expanded.clear(); renderTree(); };
  fr.appendChild(rootRow);
  const kids = document.createElement("div");
  kids.className = "kids";
  for (const c of rootE.children) build(c, kids);
  fr.appendChild(kids);
  treeEl.innerHTML = ""; treeEl.appendChild(fr);
  document.getElementById("t-info").textContent =
    `${expanded.size} Ordner offen` + (hitSet.size ? ` · ${hitSet.size} Treffer` : "");
}
expanded.add(rootE.children[0]); // ersten Ordner offen
renderTree();
document.getElementById("t-collapse").onclick = () => { expanded.clear(); hitSet.clear(); renderTree(); };
let tTimer;
document.getElementById("t-filter").addEventListener("input", ev => {
  clearTimeout(tTimer);
  tTimer = setTimeout(() => {
    const q = ev.target.value.trim().toLowerCase();
    hitSet.clear();
    if (q.length >= 2) {
      let hits = 0;
      for (const e of flat) {
        if (e.name.toLowerCase().includes(q)) {
          hitSet.add(e); hits++;
          let p = e.parent; while (p && p !== rootE) { expanded.add(p); p = p.parent; }
          if (hits > 400) break;
        }
      }
    }
    renderTree();
    const first = treeEl.querySelector(".hit");
    if (first) first.scrollIntoView({ block: "center" });
  }, 200);
});

// ---------- Duplikate ----------
(function () {
  const el = document.getElementById("tab-dupes");
  const group = (g, i) => `<details><summary><b>${esc(g[0].name)}</b>
    <span class="badge bad">×${g.length}</span>
    ${new Set(g.map(e => e.name)).size > 1 ? `<span class="mut">${esc([...new Set(g.map(e => e.name))].join("  ·  "))}</span>` : ""}</summary>
    <div>${table(g.map(e => rowHtml(e, true)))}</div></details>`;
  el.innerHTML = `
  <div class="card"><h3>Exakte Duplikate — gleicher Dateiname an mehreren Orten (${dupExact.length} Gruppen)</h3>
  ${dupExact.slice(0, 800).map(group).join("") || "<span class=mut>Keine gefunden.</span>"}
  ${dupExact.length > 800 ? `<div class=note>… und ${dupExact.length - 800} weitere (siehe Suche)</div>` : ""}</div>
  <div class="card"><h3>Ähnliche Namen — unterscheiden sich nur in Leerzeichen/Unterstrichen (${dupSimilar.length} Gruppen)</h3>
  <div class=note>Z.B. „999110620_10 _100x40.btw“ vs. „999110620_10_100x40.btw“ — typische Tippfehler-Duplikate.</div>
  ${dupSimilar.slice(0, 800).map(group).join("") || "<span class=mut>Keine gefunden.</span>"}</div>`;
})();

// ---------- Probleme ----------
(function () {
  const el = document.getElementById("tab-problems");
  el.innerHTML = RULES.map(([id, label, desc]) => {
    const list = probMap[id];
    if (!list.length) return "";
    const bad = ["btw_variant", "doubledot", "noext", "name_vs_dir"].includes(id);
    return `<div class="card"><details${bad ? " open" : ""}><summary>
    <span class="badge${bad ? " bad" : ""}">${list.length.toLocaleString("de")}</span> <b>${esc(label)}</b>
    <span class="mut">${esc(desc)}</span></summary>
    <div>${table(list.slice(0, 1000).map(e => rowHtml(e, true)))}
    ${list.length > 1000 ? `<div class=note>… und ${(list.length - 1000).toLocaleString("de")} weitere</div>` : ""}</div></details></div>`;
  }).join("") || '<div class="card">Keine Probleme gefunden. 🎉</div>';
})();

// ---------- Markiert (Arbeitsliste, Export/Import) ----------
const mStatus = document.getElementById("m-status");
mStatus.innerHTML = '<option value="">alle Status</option>' +
  STATI.filter(Boolean).map(s => `<option value="${s}">${s}</option>`).join("");
function renderMarks() {
  const f = mStatus.value;
  const list = flat.filter(e => { const m = store.data[e.path]; return m && (m.s || m.c) && (!f || m.s === f); });
  document.getElementById("m-count").textContent = list.length.toLocaleString("de") + " markierte Einträge";
  document.getElementById("m-res").innerHTML = list.length
    ? table(list.map(e => rowHtml(e, true))) : '<div class="note">Noch nichts markiert.</div>';
}
mStatus.addEventListener("input", renderMarks);
const download = (name, text, type) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};
document.getElementById("m-export").onclick = () =>
  download("label_markierungen.json",
    JSON.stringify({ source: DATA.source, exported: new Date().toISOString(), marks: store.data }, null, 2),
    "application/json");
document.getElementById("m-export-del").onclick = () =>
  download("loeschliste.txt",
    flat.filter(e => store.get(e).s === "löschen").map(uncPath).join("\r\n") || "(keine Eintraege mit Status loeschen)",
    "text/plain");
document.getElementById("m-import-btn").onclick = () => document.getElementById("m-import").click();
document.getElementById("m-import").addEventListener("change", ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { try {
    const j = JSON.parse(r.result);
    if (j && j.marks) { Object.assign(store.data, j.marks); store.save(); renderMarks(); }
    else alert("Keine Markierungen in der Datei gefunden.");
  } catch { alert("Datei konnte nicht gelesen werden."); } };
  r.readAsText(f); ev.target.value = "";
});
document.getElementById("m-clear").onclick = () => {
  if (confirm("Alle Markierungen und Kommentare löschen?")) { store.data = {}; store.save(); renderMarks(); }
};

// ---------- Meta ----------
document.getElementById("meta").textContent =
  `Quelle: ${DATA.source} · Baum vom ${DATA.mtime} · ${flat.length.toLocaleString("de")} Einträge` +
  ` · Klick ↗ öffnet Ordner im Explorer`;
</script>
</body>
</html>
"""


def register_protocol() -> None:
    """Registriert das labelex:-Protokoll unter HKCU (kein Admin noetig),
    damit Klick auf einen Eintrag den Ordner im Explorer oeffnet."""
    import winreg
    handler = Path(__file__).with_name("open_folder.py").resolve()
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    cmd = f'"{exe}" "{handler}" "%1"'
    base = r"Software\Classes\labelex"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:labelex Protocol")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    print(f"Protokoll 'labelex:' registriert ->\n  {cmd}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Erzeugt HTML-Analyse-GUI aus label_tree.txt")
    ap.add_argument("input", nargs="?", default="label_tree.txt")
    ap.add_argument("-o", "--output", default="label_analyse.html")
    ap.add_argument("--no-open", action="store_true", help="Browser nicht oeffnen")
    ap.add_argument("--register", action="store_true",
                    help="labelex:-Protokoll fuer Explorer-Links registrieren (einmalig)")
    args = ap.parse_args()
    if args.register:
        register_protocol()
        return

    src = Path(args.input)
    if not src.is_file():
        sys.exit(f"Datei nicht gefunden: {src}")

    root = parse_tree(src)
    n_dirs, n_files = count(root[1])
    print(f"Gelesen: {n_dirs} Ordner, {n_files} Dateien")

    data = {
        "root": root,
        "source": str(src.resolve()),
        "mtime": datetime.fromtimestamp(src.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = HTML.replace("__DATA__", payload)

    out = Path(args.output)
    out.write_text(html, encoding="utf-8")
    print(f"Geschrieben: {out.resolve()} ({out.stat().st_size / 1e6:.1f} MB)")
    if not args.no_open:
        os.startfile(out)  # noqa: S606 - oeffnet die HTML im Standardbrowser


if __name__ == "__main__":
    main()
