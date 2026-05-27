import pytest
from mako import exceptions
from mako.lookup import TemplateLookup


def test_template_rendering_with_variable_substitution(tmp_path):
    """TemplateLookup correctly renders templates with variable substitution."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "hello.html").write_text("Hello ${name}!")

    lookup = TemplateLookup(directories=[str(template_dir)])
    output = lookup.get_template("/hello.html").render(name="world")
    assert "Hello world!" in str(output)


def test_template_rendering_nested_list_comprehension_with_strict_undefined(
    tmp_path,
):
    """Nested list comprehensions don't raise undefined variable errors with strict_undefined=True."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    # Nested comprehension: inner variable 'b' should not be flagged as undefined
    (template_dir / "comp.html").write_text("${[b for a in items for b in a]}")

    lookup = TemplateLookup(
        directories=[str(template_dir)], strict_undefined=True
    )
    output = lookup.get_template("/comp.html").render(items=[[1, 2], [3]])
    assert "[1, 2, 3]" in str(output)


def test_template_lookup_rejects_double_slash_prefix_path_traversal(tmp_path):
    """
    TemplateLookup does not serve files outside the template directory via double-slash URIs.

    A URI like '//absolute/path/to/file' would previously cause posixpath.join to ignore
    the template directory prefix entirely (since the second arg became an absolute path
    after stripping only one leading slash). All leading slashes are now stripped first.
    """
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "safe.html").write_text("safe")

    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")

    lookup = TemplateLookup(directories=[str(template_dir)])

    # Craft a URI that resolves to the secret file's absolute path via double-slash prefix.
    # e.g. if secret is at /tmp/pytest-0/test_xxx/secret.txt, uri becomes
    # "//tmp/pytest-0/test_xxx/secret.txt" which posixpath.join would treat as absolute.
    traversal_uri = "/" + str(secret)

    with pytest.raises(exceptions.TopLevelLookupException):
        lookup.get_template(traversal_uri)


def test_template_lookup_normalizes_backslash_separators_in_uri(tmp_path):
    r"""
    Backslash characters in URIs are normalized to forward slashes before path resolution.

    On Windows, a URI like '\\..\secret.txt' could bypass directory traversal checks
    if backslashes aren't converted to forward slashes first.
    """
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    sub = template_dir / "sub"
    sub.mkdir()
    (sub / "page.html").write_text("page content")

    lookup = TemplateLookup(directories=[str(template_dir)])

    # Backslash path separators should resolve to the correct forward-slash path
    template = lookup.get_template("sub\\page.html")
    output = template.render()
    assert "page content" in str(output)


def test_template_lookup_rejects_backslash_based_path_traversal(tmp_path):
    """Backslash-prefixed URIs that would traverse outside the template directory are rejected."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "safe.html").write_text("safe")

    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")

    lookup = TemplateLookup(directories=[str(template_dir)])

    # A backslash-prefixed absolute path should not bypass the directory check
    traversal_uri = "\\" + str(secret).replace("/", "\\")

    with pytest.raises(exceptions.TopLevelLookupException):
        lookup.get_template(traversal_uri)
