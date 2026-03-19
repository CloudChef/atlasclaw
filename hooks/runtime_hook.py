# -*- coding: utf-8 -*-
"""
PyInstaller Runtime Hook for AtlasClaw

This hook runs before any other imports to set up environment variables
needed for frozen mode.
"""

import os
import sys

# Disable logfire integration with pydantic/pydantic-ai in frozen mode
# These MUST be set before any pydantic imports
if getattr(sys, 'frozen', False):
    os.environ['LOGFIRE_DISABLE'] = '1'
    os.environ['LOGFIRE_SEND_TO_LOGFIRE'] = 'false'
    os.environ['PYDANTIC_AI_DISABLE_LOGFIRE'] = '1'
    # Also try to disable via pydantic settings
    os.environ['PYDANTIC_DISABLE_PLUGINS'] = '1'
