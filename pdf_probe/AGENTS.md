# pdf_probe — eigenständiger Ein-PDF-Etiketten-Prototyp

Scope: lokaler Prototyp, analysiert genau eine explizit übergebene einseitige
PDF (Standard: `\\duramet02\Label\8000\8241\824101_1_70x100_112026.pdf`,
read-only, SHA-256 vor/nach Lauf geprüft). OCR- und Kontakt-Parsing-Regeln
liegen fixiert in `analysis_rules.py` — nicht ändern, nur ausführen.
Symbolerkennung nutzt vorlagenfreie Grafikbereichssuche
(`SYMBOL_DETECTION_STATUS = "graphic_candidates"`, `graphic_candidates.py`,
Lead-autorisiert, nicht modifizieren) mit lokaler Ausschnitt-OCR; die alten
9 Bildvorlagen sind entfernt. Kandidaten können auch Logos, Mengenzeichen,
Abbildungen oder Textreste sein; Textvorschläge sind unbestätigt, fehlende
Kandidaten beweisen keine Abwesenheit. Keine Sprach-/Barcode-Erkennung.

## Dateien
- `analyze_label.py` — CLI: Analyse + `--render-json` (Bericht neu rendern ohne PDF/OCR/Matching)
- `make_contact_sheet.py` — Review-Kontaktabzug PNG aus eingefrorenem result.json (nur Darstellung)
- `analysis_rules.py` — fixierte Regeln (Lead-autorisiert, nicht modifizieren)
- `offline_guard.py` — führt Analyse mit blockierten Sockets aus (Offline-Nachweis)
- `test_analyze_label.py` — unittest (Parsing/Sicherheit/Plumbing, keine Score-Asserts)
- `inspect_pdf.py` — Struktur-Dump aus dem Setup-Schritt
- `artifacts/legacy-reference/symbol-reference.png` — ehemalige Nutzerreferenz (historisch, nicht mehr aktiv)
- `requirements.txt` — direkte Abhängigkeiten, gepinnt
- `.venv/` — isolierte Python-3.12-Umgebung
- `artifacts/` — Ausgaben (Renderings, `result/result.json`, Bericht)

## Verifizierte Befehle (Windows, aus diesem Verzeichnis)
- Analyse (neuer Lauf, öffnet Bericht): `.venv\Scripts\python.exe analyze_label.py [--pdf PATH] [--output DIR] [--no-open]`
  - Ohne `--output`: schreibt nach `artifacts\result` falls leer, sonst eindeutig nach `artifacts\result-YYYYMMDD-HHMMSS-<uuid8>` (Doppelklick-sicher).
  - Explizites `--output` verweigert nicht-leere Verzeichnisse.
- Offline-geprüft: `.venv\Scripts\python.exe offline_guard.py --no-open`
- Bericht erneut öffnen/rendern aus eingefrorenem Ergebnis: `.venv\Scripts\python.exe analyze_label.py --render-json artifacts\result\result.json [--output NEUES_DIR]`
  - `--render-json` liest weder PDF noch OCR/Matching neu ein; bei neuem `--output` wird `result.json` dorthin kopiert, das Original bleibt unverändert.
- Kontaktabzug: `.venv\Scripts\python.exe make_contact_sheet.py` → `artifacts\review-contact-sheet.png`
- Tests: `.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py" -v` (Protokoll: `artifacts\verification-tests.log`)
- Berichtsvorschau (läuft bereits): `.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory artifacts\result`
- `Start_Test.cmd` — Doppelklick-Launcher, neue Analyse (nutzt `.venv`, kein pip/Netzwerk)

## Symbol-Katalog (v1, lokal)
- Start: `Start_Katalog.cmd` oder `.venv\Scripts\python.exe -X utf8 catalog_server.py [--port 8766] [--db catalog.sqlite3]`
  - Belegter Port mit gleicher `/api/health`-Identität wird wiederverwendet (kein Kill).
  - Start öffnet nur den vorhandenen Bestand; gespeicherte Ergebnisse werden ausschließlich
    per Button `Gespeicherte Ergebnisse einlesen` importiert (kein Scan der Freigabe, kein OCR).
- Tests: `.venv\Scripts\python.exe -X utf8 -m unittest test_catalog_app -v`
- Test-Abhängigkeit (Browser): `.venv\Scripts\python.exe -m pip install -r requirements-test.txt`
- Einträge entstehen aus eingefrorenen Kandidaten-Ausschnitten; Namen werden nur bei
  **exakter Bildidentität** geteilt (kein Fuzzy-Matching). Varianten/Skalen erfordern
  ggf. erneute Bestätigung. Neue Grafikkandidaten (`candidate_kind =
  "graphic_region"`) übernehmen den eingefrorenen Vorschlag + Ausschnitt-OCR;
  alte Vorlagentreffer (tool 0.1.0 / Referenz-Provenienz) registrieren nur das
  Dokument und werden nicht übernommen. Keine Metrik-/Konformitätsaussage.
- DB: `catalog.sqlite3` lokal; Berichte unter `/reports/<docid>/`; `/api/*` nur 127.0.0.1.
- Pilot-Übersicht der 3 eingefrorenen Grafikbefunde: `/pilot/` (Dreier-Test, nur lesend aus
  `artifacts/graphics-pilot-v3/`).

Keine Commits/Pushes; Quell-PDFs werden nie verändert oder hochgeladen.
