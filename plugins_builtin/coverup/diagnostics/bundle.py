# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
""""Export diagnostics bundle": a zip with recent logs, redacted settings and environment info."""

from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from .logging_setup import RedactingFilter

MAX_LOG_BYTES = 20 * 1024 * 1024


def create(dest: str | Path, log_folder: str | Path, settings_redacted: dict, versions: dict, secrets: list[str], extra: dict | None = None) -> Path:
    """Write the bundle to ``dest`` and return its path. Log text is redacted again on the way out."""
    dest = Path(dest)
    redactor = RedactingFilter(secrets)
    info = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version,
        "platform": platform.platform(),
        **versions,
    }
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("environment.json", json.dumps(info, indent=2))
        z.writestr("settings.json", json.dumps(settings_redacted, indent=2, default=str))
        if extra:
            z.writestr("extra.json", json.dumps(extra, indent=2, default=str))
        total = 0
        folder = Path(log_folder)
        logs = sorted(folder.glob("coverup*.log*"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
        for p in logs:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            total += len(text)
            if total > MAX_LOG_BYTES:
                break
            z.writestr(f"logs/{p.name}", "\n".join(redactor.redact(line) for line in text.splitlines()))
    return dest
