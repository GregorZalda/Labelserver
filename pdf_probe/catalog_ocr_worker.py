"""Single-crop OCR worker for the catalogue, run as an isolated subprocess.

Blocks outbound sockets first (offline guarantee), loads the stored crop,
runs rules.read_crop once, updates proposal + raw OCR only. Never touches
confirmed names or state.
"""

import socket
import sys


def _blocked(*args, **kwargs):
    raise RuntimeError("Netzwerkzugriff blockiert (Ausschnitt-OCR)")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

import argparse
import json
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

import catalog_rules as rules
import catalog_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()

    store = catalog_store.CatalogStore(args.db)
    blob = store.image_bytes(args.key)
    if blob is None:
        print(json.dumps({"ok": False, "error": "entry not found"}))
        return 1

    import analysis_rules as arules

    engine = arules.create_ocr()
    image = Image.open(BytesIO(bytes(blob)))
    ocr = rules.read_crop(engine, image)
    proposal = rules.proposal_from_text(
        [line["text"] for line in ocr["lines"]],
        store.first_template_id(args.key),
        source="Ausschnitt-OCR",
    )
    store.update_proposal(args.key, proposal, json.dumps(ocr, ensure_ascii=False))
    print(json.dumps({"ok": True, "proposal": proposal, "ocr": ocr}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
