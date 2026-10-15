"""Build and validate the minimal Agensi ZIP for WhatsApp Reader."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "whatsapp-reader"
OUTPUT = ROOT / "dist" / "whatsapp-reader.zip"
REQUIRED = {
    "SKILL.md",
    "README.md",
    "INSTALL.md",
    "mcp/claude-code.json",
}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:ghp|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{20,})"),
    re.compile(r"(?i)(?:WUZAPI_(?:ADMIN_)?TOKEN\s*=\s*)(?!YOUR_|<)[A-Za-z0-9_-]{16,}"),
    re.compile(r'(?i)"(?:WUZAPI_(?:ADMIN_)?TOKEN)"\s*:\s*"(?!YOUR_|<)[^"]+"'),
    re.compile(r"(?i)admin123"),
]


def validate_text(path: str, text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ValueError(f"Possible secret in package file: {path}")


def main() -> None:
    files = sorted(path for path in SOURCE.rglob("*") if path.is_file())
    names = {path.relative_to(SOURCE).as_posix() for path in files}
    missing = REQUIRED - names
    if missing:
        raise SystemExit(f"Missing required product files: {sorted(missing)}")
    if names - REQUIRED:
        raise SystemExit(f"Unexpected files in distribution: {sorted(names - REQUIRED)}")

    for path in files:
        try:
            validate_text(path.relative_to(SOURCE).as_posix(), path.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise SystemExit(f"Non-text content not permitted in skill ZIP: {path}") from exc

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"whatsapp-reader/{path.relative_to(SOURCE).as_posix()}")

    with zipfile.ZipFile(OUTPUT) as archive:
        members = set(archive.namelist())
        expected = {f"whatsapp-reader/{name}" for name in REQUIRED}
        if members != expected:
            raise SystemExit(f"ZIP content mismatch: {sorted(members ^ expected)}")
        for name in members:
            validate_text(name, archive.read(name).decode("utf-8"))
    print(f"Validated {OUTPUT} ({OUTPUT.stat().st_size} bytes, {len(REQUIRED)} files)")


if __name__ == "__main__":
    main()
