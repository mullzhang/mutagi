"""Install and remove the project-specific macOS LaunchAgent."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from mutagi.runtime.storage import atomic_json, utc


def installation(repo):
    suffix = hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    label = "org.mutagi." + suffix
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    return label, path


def install(repo):
    if sys.platform != "darwin":
        raise RuntimeError("this supervisor requires launchd on macOS")

    label, path = installation(repo)
    log = repo / ".mutagi/runs/service.log"
    log.parent.mkdir(exist_ok=True)
    # Save the executable search path needed after login. The default launchd
    # PATH does not include Homebrew or user-installed uv/apm.
    folders = [
        str(Path(shutil.which(name)).parent)
        for name in ("codex", "uv", "apm", "git")
        if shutil.which(name)
    ]
    folders.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    value = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "mutagi",
            "service",
            "--repo",
            str(repo),
            "tick",
        ],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "StartInterval": 30,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(folders))},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "ProcessType": "Background",
    }
    content = plistlib.dumps(value)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError(
            "the existing LaunchAgent differs; explicitly uninstall it before replacement"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    domain = f"gui/{os.getuid()}"
    existing = subprocess.run(
        ["launchctl", "print", domain + "/" + label], capture_output=True
    )
    if existing.returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)

    subprocess.run(
        ["launchctl", "print", domain + "/" + label],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    atomic_json(
        repo / ".mutagi/runs/service-installation.json",
        {
            "label": label,
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "installed_at": utc(time.time()),
        },
    )
    return {"label": label, "path": str(path)}


def uninstall(repo):
    label, path = installation(repo)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/" + label], check=True)
    path.unlink()
