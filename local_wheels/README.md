Rebuilding ARM64 wheels locally
-------------------------------

Building ARM64 wheels on x86_64 Github runners is slow due to emulation. At the time of writing Gevent did not provide
aarch64 wheels for Python 3.8. Gevent takes ~30 minutes to build. We can instead build it locally and commit it into the repository to speed up the builds.

First make sure you have `cibuildwheel` installed

```bash
pipx install cibuildwheel
```

Download sdist (source distribution) for the verion of gevent you want to use. You can find the link by browsing PyPI.

```bash
wget https://files.pythonhosted.org/packages/27/24/a3a7b713acfcf1177207f49ec25c665123f8972f42bee641bcc9f32961f4/gevent-24.2.1.tar.gz
```

Finally build it

```
CIBW_BUILD='cp38-manylinux_aarch64' pipx run cibuildwheel --platform linux --output-dir local_wheels gevent-24.2.1.tar.gz
```

Now you can commit that file and also make sure to make changes to `requirements/prod.txt` with the new version.
