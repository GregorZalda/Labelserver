r"""Erstellt einen Filetree eines Ordners (z.B. des Label-Laufwerks).

Verwendung:
    python filetree.py                          # aktueller Ordner
    python filetree.py \\duramet02\Label        # Label-Laufwerk
    python filetree.py \\duramet02\Label -d 2   # nur 2 Ebenen tief
    python filetree.py \\duramet02\Label -o tree.txt --sizes
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def build_tree(root: Path, max_depth: int | None, show_sizes: bool,
               dirs_only: bool,
               ext_counter: Counter | None = None) -> tuple[list[str], int, int]:
    lines: list[str] = [str(root)]
    n_dirs = n_files = 0

    def walk(dir_path: Path, prefix: str, depth: int) -> None:
        nonlocal n_dirs, n_files
        if max_depth is not None and depth > max_depth:
            return
        try:
            entries = sorted(dir_path.iterdir(),
                             key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, OSError) as e:
            lines.append(f"{prefix}└── [FEHLER: {e}]")
            return
        if dirs_only:
            entries = [e for e in entries if e.is_dir()]

        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                n_dirs += 1
                walk(entry, prefix + ("    " if last else "│   "), depth + 1)
            else:
                n_files += 1
                if ext_counter is not None:
                    ext_counter[entry.suffix.lower() or "(keine)"] += 1
                size = ""
                if show_sizes:
                    try:
                        size = f"  ({fmt_size(entry.stat().st_size)})"
                    except OSError:
                        size = "  (?)"
                lines.append(f"{prefix}{connector}{entry.name}{size}")

    walk(root, "", 1)
    return lines, n_dirs, n_files


def main() -> None:
    ap = argparse.ArgumentParser(description="Filetree fuer einen Ordner")
    ap.add_argument("path", nargs="?", default=".",
                    help="Startordner (Standard: aktueller Ordner)")
    ap.add_argument("-d", "--depth", type=int, default=None,
                    help="maximale Tiefe")
    ap.add_argument("-o", "--output", default=None,
                    help="Ausgabedatei (statt Konsole)")
    ap.add_argument("--sizes", action="store_true",
                    help="Dateigroessen anzeigen")
    ap.add_argument("--dirs-only", action="store_true",
                    help="nur Ordner anzeigen")
    ap.add_argument("--stats", action="store_true",
                    help="Statistik der Dateiendungen am Ende anzeigen")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        sys.exit(f"Kein Ordner: {root}")

    ext_counter: Counter | None = Counter() if args.stats else None
    lines, n_dirs, n_files = build_tree(
        root, args.depth, args.sizes, args.dirs_only, ext_counter)
    lines.append("")
    lines.append(f"{n_dirs} Ordner, {n_files} Dateien")
    if ext_counter:
        lines.append("")
        lines.append("Dateitypen:")
        for ext, count in ext_counter.most_common():
            lines.append(f"  {ext:>10}  {count}")

    if args.output:
        Path(args.output).write_text("\n".join(lines), encoding="utf-8")
        print(f"Baum geschrieben nach {args.output} "
              f"({n_dirs} Ordner, {n_files} Dateien)")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
