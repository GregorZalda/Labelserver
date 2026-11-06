# Labelserver - lokaler Etiketten-Prototyp

Lokaler, offline faehiger Prototyp zur Analyse einzelner PDF-Etiketten.
Analyse- und Katalog-Code liegt unter `pdf_probe/`; die Skripte im
Repo-Wurzelverzeichnis sind aeltere Bestandsskripte.

## Was das Tool aktuell kann

- Rendert eine explizit uebergebene einseitige PDF (300 dpi), liest
  eingebetteten PDF-Text und fuehrt lokale OCR mit RapidOCR (ONNX
  Runtime) aus; Kontaktzeilen werden geparst.
- Vorlagenfreie Suche nach Grafikbereichen (`graphic_candidates.py`,
  Algorithmus `graphic-regions/1`) plus lokale Ausschnitt-OCR pro
  Kandidat. Textbasierte Vorschlaege (z. B. Materialcodes) sind
  unbestaetigt.
- Lokaler Symbol-Katalog (127.0.0.1:8766): Benennungen gelten nur fuer
  exakt pixel-identische Ausschnitte; Varianten/Skalen erfordern eine
  erneute Bestaetigung.

## Grenzen

- Kein trainiertes Symbolmodell und keine allgemeine Symbolerkennung.
- Kandidaten koennen auch Logos, Mengenzeichen, Abbildungen oder
  Textreste sein.
- Keine Rechts- oder Normkonformitaetsaussage; fehlende Kandidaten
  beweisen keine Abwesenheit. Alle Ergebnisse sind manuell zu pruefen.
- Nur einseitige PDFs; die Quelldatei bleibt unveraendert
  (SHA-256 vor und nach der Analyse identisch).

## Setup (Windows)

Voraussetzung: Git und Python 3.12 x64 installiert (py-Launcher).

    git clone https://github.com/GregorZalda/Labelserver.git
    cd Labelserver\pdf_probe
    py -3.12 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt

- Die Erstinstallation per pip braucht Internet; RapidOCR bringt
  seine Modelle mit, die Analyse laeuft danach vollstaendig lokal.
- Katalog starten: `Start_Katalog.cmd` -> http://127.0.0.1:8766/

## Analyse einzelner PDF

    .venv\Scripts\python.exe analyze_label.py --pdf "C:\Etiketten\beispiel.pdf" --no-open

- Nur einseitige PDFs. Berichte und Assets landen unter `artifacts\`.
- Achtung: ohne `--pdf` zeigt der interne Default auf einen
  firmeninternen Label-Pfad (Netzfreigabe). Pro Lauf wird genau eine
  Datei gelesen (--pdf oder Default) - kein implizites Scannen.

## Ein frischer Clone ist leer

- Kein Katalog-Bestand, keine artifacts/-Berichte, keine Pilot-Daten -
  diese werden nie ins Repo hochgeladen (`.gitignore`).
- Katalog-Inhalte: `pdf_probe\catalog.sqlite3` enthaelt Eintraege inkl.
  der Kandidaten-Ausschnitte als BLOBs; die verknuepften Berichte und
  Bild-Assets liegen in den referenzierten Ordnern unter
  `pdf_probe\artifacts\` (der Pfad steht pro Dokument in
  `documents.report_path`).
- Zweitrechner-Transfer: `catalog.sqlite3` plus die zugehoerigen
  artifacts/-Ordner kopieren - nur bei gestopptem Katalog-Server,
  keine laufende DB kopieren. Gespeicherte Berichtspfade koennen noch
  auf den alten Rechner zeigen und muessen beim Umzug geprueft werden.

## Tests

    .venv\Scripts\python.exe -X utf8 -m unittest test_analyze_label test_catalog_rules test_graphic_candidates test_latest_summary -v

- `test_catalog_app` optional: zuerst
  `pip install -r requirements-test.txt`; fuer die Browser-Tests ist
  installierter Edge oder Chrome noetig.
- `test_report_layout` ist kein Clean-Clone-Gate: es misst die reale
  Overlay-Geometrie gegen eingefrorene lokale artifacts/-Berichte, die
  in einem frischen Clone nicht existieren.

## Interner Kontext

Der Code enthaelt interne Standard-Beispielpfade und Servernamen als
Defaults. PDFs, lokale Auswertungen und Zugangsdaten gehoeren nicht
ins Repository.
