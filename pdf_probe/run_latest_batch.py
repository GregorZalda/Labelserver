import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import analyze_label as analysis


BASE = Path(__file__).resolve().parent
CACHE_PATHS = [
    BASE / "artifacts" / "result" / "result.json",
    *[
        BASE / "artifacts" / "samples-20261105" / item / "result.json"
        for item in (
            "small_200201", "large_400023", "outlined_200601",
            "strip_old_800119", "strip_new_800319", "control_824105",
        )
    ],
]


def now():
    return datetime.now(timezone.utc).isoformat()


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def cache_for(item, rules):
    path = Path(item["path"])
    reference_hash = analysis.sha256_file(analysis.REFERENCE_PATH)
    for candidate in CACHE_PATHS:
        if not candidate.is_file():
            continue
        result = json.loads(candidate.read_text(encoding="utf-8"))
        method = result.get("method", {})
        if (
            os.path.normcase(result.get("source", {}).get("path", "")) == os.path.normcase(str(path))
            and result.get("schema") == analysis.RESULT_SCHEMA
            and result.get("reference", {}).get("sha256") == reference_hash
            and method.get("render_dpi") == rules.RENDER_DPI
            and method.get("match_threshold") == rules.MATCH_THRESHOLD
            and method.get("match_scales") == list(rules.MATCH_SCALES)
            and result["source"].get("sha256") == analysis.sha256_file(path)
        ):
            return candidate, result
    return None, None


def main(argv=None):
    import analysis_rules as rules

    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    selection_path = Path(args.selection)
    selection_bytes = selection_path.read_bytes()
    manifest = json.loads(selection_bytes)
    selected = manifest["selected"]
    if len(selected) != 100 or len({i["path"].casefold() for i in selected}) != 100:
        raise ValueError("Es werden exakt 100 eindeutige Pfade im Auswahlmanifest benötigt.")
    if [i["rank"] for i in selected] != list(range(1, 101)):
        raise ValueError("Auswahlränge sind nicht lückenlos.")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    frozen_selection = output / "selection.json"
    if frozen_selection.exists() and frozen_selection.read_bytes() != selection_bytes:
        raise ValueError("Dieser Lauf enthält bereits eine andere Auswahl.")
    if not frozen_selection.exists():
        frozen_selection.write_bytes(selection_bytes)
    state_path = output / "batch-state.json"
    script_hash = analysis.sha256_file(Path(__file__))
    rules_hash = analysis.sha256_file(BASE / "analysis_rules.py")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["rules_sha256"] != rules_hash or state["runner_sha256"] != script_hash:
            raise ValueError("Code hat sich seit Laufbeginn geändert; Resume abgebrochen.")
    else:
        state = {
            "schema": "pdf-probe-latest-batch/1",
            "started_at_utc": now(),
            "finished_at_utc": None,
            "selection_sha256": analysis.sha256_file(selection_path),
            "rules_sha256": rules_hash,
            "runner_sha256": script_hash,
            "recognition_scope": "Unveränderter Prototyp: neun Referenzsymboltypen, lokale OCR, bekanntermaßen unvollständiger Kontaktparser. Keine Regelwerks- oder Konformitätsprüfung. Mehrseitige/verschlüsselte/ungültige PDFs werden als Fehler ausgewiesen, nicht stillschweigend ersetzt.",
            "jobs": [{**item, "state": "pending"} for item in selected],
        }
    lock = output / "runner.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        save_state(state_path, state)
        for job in state["jobs"]:
            if job["state"] in {"success", "reused", "failed", "changed_since_selection"}:
                continue
            source = Path(job["path"])
            target = output / job["id"]
            if target.exists() and any(target.iterdir()):
                completed = target / "result.json"
                if completed.is_file():
                    result = json.loads(completed.read_text(encoding="utf-8"))
                    if (
                        result.get("schema") == analysis.RESULT_SCHEMA
                        and result["source"]["path"].casefold() == job["path"].casefold()
                        and result["source"]["sha256"] == analysis.sha256_file(source)
                    ):
                        job.update(state="success", finished_at_utc=now(), resumed_completed_result=True)
                        save_state(state_path, state)
                        continue
                job.update(state="failed", error="Unvollständige Ausgabe eines früheren Versuchs; nicht automatisch überschrieben.")
                save_state(state_path, state)
                continue
            job.update(state="running", started_at_utc=now())
            save_state(state_path, state)
            started = time.monotonic()
            try:
                stat = source.stat()
                if (
                    stat.st_size != job["size_bytes"]
                    or abs(stat.st_birthtime - job["created_timestamp"]) > 0.001
                    or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() != job["modified_utc"]
                ):
                    job.update(state="changed_since_selection", error="Dateimetadaten haben sich seit der Auswahl verändert; keine Ersatzdatei gewählt.")
                    continue
                cached_path, cached_result = cache_for(job, rules)
                if cached_path is not None:
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(cached_path, target / "result.json")
                    analysis.render_report(cached_result, target)
                    job.update(state="reused", reused_from=str(cached_path), source_sha256=cached_result["source"]["sha256"])
                else:
                    log_path = output / f"{job['id']}.log"
                    with log_path.open("w", encoding="utf-8") as log:
                        completed = subprocess.run(
                            [
                                sys.executable, "-X", "utf8", str(BASE / "offline_guard.py"),
                                "--pdf", str(source), "--output", str(target), "--no-open",
                            ],
                            cwd=BASE, stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
                            check=False,
                        )
                    job["exit_code"] = completed.returncode
                    job["log"] = log_path.name
                    if completed.returncode == 0 and (target / "result.json").is_file():
                        result = json.loads((target / "result.json").read_text(encoding="utf-8"))
                        job.update(state="success", source_sha256=result["source"]["sha256"])
                    else:
                        job.update(state="failed", error=log_path.read_text(encoding="utf-8")[-4000:])
            except Exception as error:
                job.update(state="failed", error=f"{type(error).__name__}: {error}")
            finally:
                job["duration_seconds"] = round(time.monotonic() - started, 2)
                job["finished_at_utc"] = now()
                save_state(state_path, state)
                print(f"{job['rank']}/100 {job['state']} {job['filename']}", flush=True)
        state["finished_at_utc"] = now()
        save_state(state_path, state)
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
