"""SOUL.md is the sole persona source; the marker preserves deliberate deletion."""
import hashlib
import os
import re
import tempfile
from pathlib import Path

START = "<!-- twin-name:start -->"
END = "<!-- twin-name:end -->"
BODY = """_You're not a chatbot. You're becoming someone._

This is your persona, yours to grow into and edit over time. Some starting truths:

- **Be genuinely helpful, not performatively helpful.** Skip "Great question!" and "I'd be happy to help!" Just help.
- **Have opinions.** You're allowed to prefer things, disagree, and find things funny or dull. Personality beats a search engine with extra steps.
- **Be resourceful before asking.** Read the file, check the context, search, try building it using your authorized tools. Come back with answers, not questions, then ask if you're truly stuck.
- **You're a guest in someone's life.** You can see authorized messages, files and memory. Treat that access with care, and never be preachy. This persona never expands your platform or runtime permissions.

If you change this file, tell the owner. It's your soul, and they should know.
"""


def heading(name):
    return f"{START}\n# SOUL.md — {name}\n{END}"


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Soul path must not contain symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def create_once(path, content):
    """Publish complete initial bytes only if no file won the race already."""
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Soul path must not contain symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        sync_directory(path.parent)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
            sync_directory(path.parent)


def initialize(workspace, name, legacy=None):
    workspace = Path(workspace)
    path, marker = workspace / "SOUL.md", workspace / ".soul-initialized"
    if marker.exists():
        return
    if not path.exists():
        create_once(path, heading(name) + "\n\n" + (legacy if legacy else BODY))
    create_once(marker, "1\n")


def read(workspace):
    path = Path(workspace) / "SOUL.md"
    if not path.exists():
        return {"content": "", "version": None}
    if path.is_symlink():
        raise ValueError("Soul file must not be a symlink")
    data = path.read_bytes()
    return {"content": data.decode("utf-8"), "version": hashlib.sha256(data).hexdigest()}


def rename(workspace, name):
    current = read(workspace)
    if current["version"] is None:
        return
    block = re.escape(START) + r".*?" + re.escape(END)
    content = re.sub(block, lambda _: heading(name), current["content"], count=1, flags=re.DOTALL)
    if content == current["content"] and not re.search(block, current["content"], flags=re.DOTALL):
        content = heading(name) + "\n\n" + current["content"]
    if content != current["content"]:
        atomic_write(Path(workspace) / "SOUL.md", content)
