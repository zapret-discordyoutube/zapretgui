from __future__ import annotations

import os
import shutil
import subprocess


def _run_text(command: list[str], timeout: float = 3.0) -> str:
    program = shutil.which(command[0])
    if not program:
        return ""
    try:
        done = subprocess.run([program, *command[1:]], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


class LinuxSystemSettings:
    """Questions answered with the standard XDG tools and environment; every answer is "unknown" when a tool is missing."""

    def accent_color(self) -> str | None:
        return None  # the XDG portal accent-color setting is read in the Linux integration phase

    def url_scheme_handler_registered(self, scheme: str) -> bool:
        return bool(_run_text(["xdg-mime", "query", "default", f"x-scheme-handler/{scheme}"]))

    def default_browser_id(self) -> str:
        return _run_text(["xdg-settings", "get", "default-web-browser"])

    def browser_paths(self) -> list[str]:
        found: list[str] = []
        for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            path = shutil.which(name)
            real = os.path.realpath(path) if path else ""
            if real and real not in found:
                found.append(real)
        return found

    def proxy_settings(self) -> tuple[str, str]:
        for name in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY"):
            value = os.environ.get(name, "").strip()
            if value:
                return value, ""
        return "", ""
