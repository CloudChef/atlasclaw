# -*- mode: python ; coding: utf-8 -*-
"""
AtlasClaw PyInstaller Spec File

Build commands:
    Windows: pyinstaller atlasclaw.spec
    Linux:   pyinstaller atlasclaw.spec

Output: dist/atlasclaw.exe (Windows) or dist/atlasclaw (Linux)
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

# Determine platform
is_windows = sys.platform == 'win32'
is_linux = sys.platform.startswith('linux')

# Project paths
project_root = Path(SPECPATH)
app_path = project_root / 'app'
providers_path = project_root / 'atlasclaw_providers'
external_providers = project_root.parent / 'atlasclaw-providers'

# Collect data files
datas = [
    # App package (Python files)
    (str(app_path), 'app'),
    # Frontend assets
    (str(app_path / 'frontend'), 'app/frontend'),
    # Bundled providers (from atlasclaw_providers)
    (str(providers_path / 'providers'), 'atlasclaw_providers/providers') if (providers_path / 'providers').exists() else None,
    (str(providers_path / 'skills'), 'atlasclaw_providers/skills') if (providers_path / 'skills').exists() else None,
    # External providers (if exists and not already bundled)
    (str(external_providers / 'providers'), 'atlasclaw_providers/providers') if (external_providers / 'providers').exists() and not (providers_path / 'providers').exists() else None,
    (str(external_providers / 'skills'), 'atlasclaw_providers/skills') if (external_providers / 'skills').exists() and not (providers_path / 'skills').exists() else None,
    # Config example
    (str(project_root / 'atlasclaw.json.example'), '.'),
]

# Add package metadata for packages that need it
metadata_packages = [
    'genai_prices',
    'pydantic_ai',
    'pydantic_ai_slim',
    'pydantic',
    'fastapi',
    'starlette',
    'opentelemetry-api',
    'opentelemetry-sdk',
    'logfire-api',
]
for pkg in metadata_packages:
    try:
        datas.extend(copy_metadata(pkg))
    except Exception:
        pass  # Package not installed or no metadata

# Filter out None entries
datas = [d for d in datas if d is not None]

# Hidden imports for dynamic loading
hiddenimports = [
    # FastAPI and dependencies
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'starlette',
    'starlette.routing',
    'starlette.middleware',
    'fastapi',
    'fastapi.middleware.cors',
    
    # Pydantic
    'pydantic',
    'pydantic_settings',
    'pydantic_ai',
    
    # HTTP clients
    'httpx',
    'httpcore',
    'h11',
    
    # OpenAI
    'openai',
    
    # SSE
    'sse_starlette',
    
    # Async file operations
    'aiofiles',
    
    # Channel handlers
    'lark_oapi',
    'dingtalk_stream',
    
    # App modules
    'app.atlasclaw',
    'app.atlasclaw.main',
    'app.atlasclaw.cli',
    'app.atlasclaw.agent',
    'app.atlasclaw.api',
    'app.atlasclaw.auth',
    'app.atlasclaw.channels',
    'app.atlasclaw.core',
    'app.atlasclaw.hooks',
    'app.atlasclaw.media',
    'app.atlasclaw.memory',
    'app.atlasclaw.messages',
    'app.atlasclaw.models',
    'app.atlasclaw.session',
    'app.atlasclaw.skills',
    'app.atlasclaw.tools',
    'app.atlasclaw.workflow',
    
    # Providers
    'atlasclaw_providers',
    
    # Encodings
    'encodings',
    'encodings.utf_8',
    'encodings.ascii',
]

# Excluded modules (reduce size)
excludes = [
    'tkinter',
    'matplotlib',
    'numpy',
    'pandas',
    'scipy',
    'PIL',
    'cv2',
    'torch',
    'tensorflow',
]

a = Analysis(
    ['cli_entry.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / 'hooks' / 'runtime_hook.py')],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='atlasclaw',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon path if you have one: 'docs/images/atlasclaw-icon.ico'
)
