#!/usr/bin/env python3
"""
AtlasClaw Binary Build Script

Usage:
    python scripts/build_binary.py          # Build for current platform
    python scripts/build_binary.py --clean  # Clean and rebuild

Requirements:
    pip install pyinstaller

Output:
    Windows: dist/atlasclaw.exe
    Linux:   dist/atlasclaw
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent.resolve()


def clean_build_artifacts(project_root: Path) -> None:
    """Remove previous build artifacts."""
    dirs_to_remove = ['build', 'dist']
    files_to_remove = ['atlasclaw.spec.bak']
    
    for dir_name in dirs_to_remove:
        dir_path = project_root / dir_name
        if dir_path.exists():
            print(f"Removing {dir_path}...")
            shutil.rmtree(dir_path)
    
    for file_name in files_to_remove:
        file_path = project_root / file_name
        if file_path.exists():
            print(f"Removing {file_path}...")
            file_path.unlink()


def check_pyinstaller() -> bool:
    """Check if PyInstaller is installed."""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller() -> None:
    """Install PyInstaller."""
    print("Installing PyInstaller...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])


def build_binary(project_root: Path) -> Path:
    """Build the binary using PyInstaller."""
    spec_file = project_root / 'atlasclaw.spec'
    
    if not spec_file.exists():
        raise FileNotFoundError(f"Spec file not found: {spec_file}")
    
    print(f"Building binary from {spec_file}...")
    print(f"Platform: {platform.system()} {platform.machine()}")
    
    # Run PyInstaller
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--clean',
        '--noconfirm',
        str(spec_file)
    ]
    
    subprocess.check_call(cmd, cwd=project_root)
    
    # Determine output path
    if platform.system() == 'Windows':
        binary_path = project_root / 'dist' / 'atlasclaw.exe'
    else:
        binary_path = project_root / 'dist' / 'atlasclaw'
    
    return binary_path


def verify_binary(binary_path: Path) -> bool:
    """Verify the built binary works."""
    if not binary_path.exists():
        print(f"ERROR: Binary not found at {binary_path}")
        return False
    
    print(f"\nVerifying binary: {binary_path}")
    print(f"Size: {binary_path.stat().st_size / 1024 / 1024:.2f} MB")
    
    # Test --version
    try:
        result = subprocess.run(
            [str(binary_path), '--version'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print(f"Version check: {result.stdout.strip()}")
            return True
        else:
            print(f"Version check failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("ERROR: Binary timed out")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Build AtlasClaw binary')
    parser.add_argument('--clean', action='store_true', help='Clean build artifacts before building')
    parser.add_argument('--no-verify', action='store_true', help='Skip binary verification')
    args = parser.parse_args()
    
    project_root = get_project_root()
    print(f"Project root: {project_root}")
    
    # Clean if requested
    if args.clean:
        clean_build_artifacts(project_root)
    
    # Check/install PyInstaller
    if not check_pyinstaller():
        install_pyinstaller()
    
    # Build
    try:
        binary_path = build_binary(project_root)
        print(f"\n{'='*50}")
        print(f"Build successful!")
        print(f"Binary: {binary_path}")
        
        # Verify
        if not args.no_verify:
            if verify_binary(binary_path):
                print(f"\nBinary verification passed!")
            else:
                print(f"\nWARNING: Binary verification failed!")
                return 1
        
        print(f"\nUsage:")
        print(f"  {binary_path} --help")
        print(f"  {binary_path} --port 8000")
        
        return 0
        
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: Build failed with exit code {e.returncode}")
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
