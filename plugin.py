from pathlib import Path

from mkdocs.config import Config

DOCS_DIR = Path(__file__).parent
PROJECT_ROOT = DOCS_DIR.parent


def on_pre_build(config: Config) -> None:
    """Before the build starts"""
    add_changelog()


def add_changelog() -> None:
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    new_file = DOCS_DIR / "home" / "CHANGELOG.md"

    # avoid writing file unless the content has changed to avoid infinite build loop
    if not new_file.is_file() or new_file.read_text(encoding="utf-8") != changelog:
        new_file.write_text(changelog, encoding="utf-8")
