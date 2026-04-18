"""assistant-tools package."""


from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

try:
    __version__ = version("assistant-tools")
except PackageNotFoundError:
    __version__ = "0.1.0"