# -*- coding: utf-8 -*-
"""
AtlasClaw Binary Entry Point

This is the entry point for PyInstaller binary builds.
It sets up the correct module path and imports the main CLI.
"""

import os
import sys

# MUST be set before any imports that might trigger logfire
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    os.environ['LOGFIRE_DISABLE'] = '1'
    os.environ['LOGFIRE_SEND_TO_LOGFIRE'] = 'false'

from pathlib import Path


def is_frozen() -> bool:
    """Check if running as a PyInstaller bundle."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def setup_path():
    """Setup Python path for frozen or development mode."""
    if is_frozen():
        # In frozen mode, add the bundle root to path
        bundle_root = Path(sys._MEIPASS)
        if str(bundle_root) not in sys.path:
            sys.path.insert(0, str(bundle_root))
    else:
        # In development mode, add project root
        project_root = Path(__file__).parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))


def main():
    """Main entry point for the binary."""
    setup_path()
    
    # Now import and run the actual CLI
    from app.atlasclaw.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
