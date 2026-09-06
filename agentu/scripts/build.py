"""Create allowlisted static and Lambda artefacts; never package local data."""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "agentu"
OUT = ROOT / ".build"
sys.path.insert(0, str(SITE / "infra"))
from template import template


def build():
    static = OUT / "static" / "agentu"
    static.mkdir(parents=True, exist_ok=True)
    files = [SITE / name for name in ["index.html", "site.css", "site.js", "privacy.html"]]
    files += [p for directory in (SITE / "demo", SITE / "app", SITE / "brand") for p in directory.rglob("*") if p.is_file() and p.suffix in (".html", ".css", ".js", ".json", ".svg", ".txt")]
    for source in files:
        target = static / source.relative_to(SITE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    package = OUT / "lambda.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("domain.py", "storage.py", "handler.py", "worker.py"):
            archive.write(SITE / "backend" / name, name)
        for source in sorted((SITE / "backend" / "platform_core").glob("*.py")):
            archive.write(source, "platform_core/" + source.name)
    (OUT / "template.json").write_text(json.dumps(template(), indent=2), encoding="utf-8")
    manifest = {"static_files": ["agentu/" + p.relative_to(SITE).as_posix() for p in files],
                "lambda_sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "mode": "sandbox-platform", "simulated": True}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Built {len(files)} static files, Lambda package and CloudFormation template.")
    return manifest


if __name__ == "__main__":
    build()
