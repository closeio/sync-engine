import os
from typing import Generator, List

ROOT_PATH = os.path.normpath(
    os.path.join(__file__, os.pardir, os.pardir, os.pardir)
)


def get_data(filename: str) -> bytes:
    """Read contents of a file relative to the project root folder"""
    with open(os.path.join(ROOT_PATH, filename), "rb") as file:
        return file.read()


def iter_module_names(paths: List[str]) -> Generator[str, None, None]:
    """Iterate all Python module names in given paths"""
    for path in paths:
        for name in os.listdir(path):
            isdirectory = os.path.isdir(os.path.join(path, name))
            if not isdirectory and name == "__init__.py":
                continue

            if not isdirectory and name.endswith(".py"):
                yield name[:-3]
            elif isdirectory and os.path.isfile(
                os.path.join(path, name, "__init__.py")
            ):
                yield name
