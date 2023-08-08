import os
from pathlib import Path

TMP_PATH = 'temp/res.html'  # DEBUG


def set_cwd(path: str):
    root = Path(path)
    root = root.parent
    os.chdir(root)
