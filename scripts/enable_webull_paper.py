#!/usr/bin/env python3
"""Install the pinned optional Webull SDK in a Hub-owned virtual environment."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[1]
ENV=ROOT/'.venv-webull'
VERSION='2.0.19'

def executable():
    return ENV/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def main():
    if not executable().is_file():
        print('Creating optional Webull environment at .venv-webull')
        venv.EnvBuilder(with_pip=True,clear=False,symlinks=os.name!='nt').create(ENV)
    print(f'Installing webull-openapi-python-sdk=={VERSION} from the package index')
    subprocess.run([str(executable()),'-m','pip','install','--disable-pip-version-check',f'webull-openapi-python-sdk=={VERSION}'],check=True)
    subprocess.run([str(executable()),'-c',f"import importlib.metadata as m; assert m.version('webull-openapi-python-sdk') == '{VERSION}'"],check=True)
    print('Webull optional dependency is ready. Add no credentials to this script.')
    return 0

if __name__=='__main__': raise SystemExit(main())
