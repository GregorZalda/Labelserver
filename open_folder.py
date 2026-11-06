r"""Handler fuer das labelex:-Protokoll.

Wird von den Links in label_analyse.html aufgerufen:
    labelex:///o/<pfad>  -> Ordner im Explorer oeffnen
    labelex:///s/<pfad>  -> Ordner oeffnen und die Datei darin markieren

Einmalig registrieren mit:  python analyze.py --register
"""

import re
import subprocess
import sys
import urllib.parse

# Sicherheit: nur UNC-Pfade auf diesem Server zulassen.
ALLOWED_PREFIX = "\\\\duramet02"


def main() -> None:
    if len(sys.argv) < 2:
        return
    raw = sys.argv[1].strip().strip('"')
    m = re.match(r"^labelex:/+([os])/(.*)$", raw, re.IGNORECASE)
    if not m:
        return
    mode = m.group(1).lower()
    path = "\\\\" + urllib.parse.unquote(m.group(2)).strip("/").replace("/", "\\")
    if not path.lower().startswith(ALLOWED_PREFIX.lower()):
        return
    if mode == "s":
        subprocess.Popen(f'explorer.exe /select,"{path}"')
    else:
        subprocess.Popen(["explorer.exe", path])


if __name__ == "__main__":
    main()
