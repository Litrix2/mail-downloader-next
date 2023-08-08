import pathlib
import sys

tmp = pathlib.Path(__file__).parent.parent
sys.path.append(str(tmp.joinpath(tmp.name)))
