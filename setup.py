import glob
import os
import re

from setuptools import find_packages, setup

# approach stolen from sqlalchemy
with open(
    os.path.join(os.path.dirname(__file__), "inbox", "__init__.py")
) as version_file:
    VERSION = (
        re.compile(r""".*VERSION = ["'](.*?)['"]""", re.S)
        .match(version_file.read())
        .group(1)
    )


setup(
    name="inbox-sync",
    version=VERSION,
    packages=find_packages(),
    install_requires=[],
    include_package_data=True,
    package_data={
        # "inbox-sync": ["alembic.ini"],
        # If any package contains *.txt or *.rst files, include them:
        # '': ['*.txt', '*.rst'],
        # And include any *.msg files found in the 'hello' package, too:
        # 'hello': ['*.msg'],
    },
    data_files=[
        ("sync-engine-test-config", glob.glob("etc/*test*")),
        ("alembic-inbox-sync", ["alembic.ini"]),
        (
            "alembic-inbox-sync/migrations",
            filter(os.path.isfile, glob.glob("migrations/*")),
        ),
        (
            "alembic-inbox-sync/migrations/versions",
            filter(os.path.isfile, glob.glob("migrations/versions/*")),
        ),
    ],
    scripts=[
        "bin/inbox-start",
        "bin/inbox-api",
        "bin/syncback-service",
        "bin/purge-transaction-log",
        "bin/delete-marked-accounts",
    ],
    # See:
    # https://pythonhosted.org/setuptools/setuptools.html#dynamic-discovery-of-services-and-plugins
    # https://pythonhosted.org/setuptools/pkg_resources.html#entry-points
    zip_safe=False,
    author="Nylas Team",
    author_email="support@nylas.com",
    description="The Nylas Sync Engine",
    license="AGPLv3",
    keywords="nylas",
    url="https://www.nylas.com",
)
