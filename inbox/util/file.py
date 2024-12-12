import os
from collections.abc import Generator

ROOT_PATH = os.path.normpath(
    os.path.join(__file__, os.pardir, os.pardir, os.pardir)  # noqa: PTH118
)


def get_data(filename: str) -> bytes:
    """Read contents of a file relative to the project root folder"""
    with open(  # noqa: PTH123
        os.path.join(ROOT_PATH, filename), "rb"  # noqa: PTH118
    ) as file:
        return file.read()


def iter_module_names(paths: list[str]) -> Generator[str, None, None]:
    """Iterate all Python module names in given paths"""
    for path in paths:
        for name in os.listdir(path):
            isdirectory = os.path.isdir(  # noqa: PTH112
                os.path.join(path, name)  # noqa: PTH118
            )
            if not isdirectory and name == "__init__.py":
                continue

            if not isdirectory and name.endswith(".py"):
                yield name[:-3]
            elif isdirectory and os.path.isfile(  # noqa: PTH113
                os.path.join(path, name, "__init__.py")  # noqa: PTH118
            ):
                yield name
