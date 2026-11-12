import importlib
import os
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parent.parent / "mcp-server"


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Import mcp-server/server.py with its logs, token and screenshots in a temp home."""
    home = tmp_path_factory.mktemp("home")
    for var in ("HOME", "USERPROFILE"):
        os.environ[var] = str(home)
    os.environ["CLAUDE_BROWSER_SCREENSHOTS_DIR"] = str(home / "screenshots")
    sys.path.insert(0, str(MCP_DIR))
    return importlib.import_module("server")
