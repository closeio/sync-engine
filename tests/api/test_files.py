import json
import os
from datetime import datetime
from hashlib import md5
from unittest import mock

import pytest

from inbox.models import Block, Part
from inbox.util.testutils import FILENAMES


@pytest.fixture
def draft(db, default_account):
    return {
        "subject": f"Draft test at {datetime.utcnow()}",
        "body": "<html><body><h2>Sea, birds and sand.</h2></body></html>",
        "to": [
            {
                "name": "The red-haired mermaid",
                "email": default_account.email_address,
            }
        ],
    }


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_file_filtering(api_client, uploaded_file_ids, draft) -> None:
    # Attach the files to a draft and search there
    draft["file_ids"] = uploaded_file_ids
    r = api_client.post_data("/drafts", draft)
    assert r.status_code == 200

    draft_resp = json.loads(r.data)
    assert len(draft_resp["files"]) == len(uploaded_file_ids)
    d_id = draft_resp["id"]

    results = api_client.get_data("/files")
    assert len(results) == len(uploaded_file_ids)

    results = api_client.get_data(f"/files?message_id={d_id}")

    assert all([d_id in f["message_ids"] for f in results])
    assert len(results) == len(uploaded_file_ids)

    results = api_client.get_data(f"/files?message_id={d_id}&limit=1")
    assert len(results) == 1

    results = api_client.get_data(f"/files?message_id={d_id}&offset=2")
    assert len(results) == 3

    results = api_client.get_data("/files?filename=LetMeSendYouEmail.wav")
    assert len(results) == 1

    results = api_client.get_data("/files?content_type=audio%2Fx-wav")
    assert len(results) == 1

    results = api_client.get_data("/files?content_type=image%2Fjpeg")
    assert len(results) == 2

    results = api_client.get_data(
        "/files?content_type=image%2Fjpeg&view=count"
    )
    assert results["count"] == 2

    results = api_client.get_data("/files?content_type=image%2Fjpeg&view=ids")
    assert len(results) == 2


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_attachment_has_same_id(api_client, uploaded_file_ids, draft) -> None:
    attachment_id = uploaded_file_ids.pop()
    draft["file_ids"] = [attachment_id]
    r = api_client.post_data("/drafts", draft)
    assert r.status_code == 200
    draft_resp = json.loads(r.data)
    assert attachment_id in [x["id"] for x in draft_resp["files"]]


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_delete(api_client, uploaded_file_ids, draft) -> None:
    non_attachment_id = uploaded_file_ids.pop()
    attachment_id = uploaded_file_ids.pop()
    draft["file_ids"] = [attachment_id]
    r = api_client.post_data("/drafts", draft)
    assert r.status_code == 200

    # Test that we can delete a non-attachment
    r = api_client.delete(f"/files/{non_attachment_id}")
    assert r.status_code == 200

    data = api_client.get_data(f"/files/{non_attachment_id}")
    assert data["message"].startswith("Couldn't find file")

    # Make sure that we cannot delete attachments
    r = api_client.delete(f"/files/{attachment_id}")
    assert r.status_code == 400

    data = api_client.get_data(f"/files/{attachment_id}")
    assert data["id"] == attachment_id


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
@pytest.mark.parametrize("filename", FILENAMES)
def test_get_with_id(api_client, uploaded_file_ids, filename) -> None:
    # See comment in uploaded_file_ids()
    if filename == "piece-jointe.jpg":
        filename = "pièce-jointe.jpg"
    elif filename == "andra-moi-ennepe.txt":
        filename = "ἄνδρα μοι ἔννεπε"
    elif filename == "long-non-ascii-filename.txt":
        filename = 100 * "μ"
    in_file = api_client.get_data(f"/files?filename={filename}")[0]
    data = api_client.get_data("/files/{}".format(in_file["id"]))
    assert data["filename"] == filename


def test_get_invalid(api_client, uploaded_file_ids) -> None:
    data = api_client.get_data("/files/0000000000000000000000000")
    assert data["message"].startswith("Couldn't find file")
    data = api_client.get_data("/files/!")
    assert data["message"].startswith("Invalid id")

    data = api_client.get_data("/files/0000000000000000000000000/download")
    assert data["message"].startswith("Couldn't find file")
    data = api_client.get_data("/files/!/download")
    assert data["message"].startswith("Invalid id")

    r = api_client.delete("/files/0000000000000000000000000")
    assert r.status_code == 404
    r = api_client.delete("/files/!")
    assert r.status_code == 400


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
@pytest.mark.parametrize("filename", FILENAMES)
def test_download(api_client, uploaded_file_ids, filename) -> None:
    # See comment in uploaded_file_ids()
    original_filename = filename
    if filename == "piece-jointe.jpg":
        filename = "pièce-jointe.jpg"
    elif filename == "andra-moi-ennepe.txt":
        filename = "ἄνδρα μοι ἔννεπε"
    elif filename == "long-non-ascii-filename.txt":
        filename = 100 * "μ"

    in_file = api_client.get_data(f"/files?filename={filename}")[0]
    data = api_client.get_raw("/files/{}/download".format(in_file["id"])).data

    path = os.path.join(  # noqa: PTH118
        os.path.dirname(os.path.abspath(__file__)),  # noqa: PTH100, PTH120
        "..",
        "data",
        original_filename,
    )
    with open(path, "rb") as fp:  # noqa: PTH123
        local_data = fp.read()
    local_md5 = md5(local_data).digest()
    dl_md5 = md5(data).digest()
    assert local_md5 == dl_md5


@pytest.fixture
def fake_attachment(db, default_account, message):
    block = Block()
    namespace_id = default_account.namespace.id
    block.namespace_id = namespace_id
    block.filename = "zambla.txt"
    block.content_type = "text/plain"
    block.size = 32
    # This is sha256 of an attachment in our test email.
    block.data_sha256 = (
        "27dc8e801f962098fd4a741ccbd6ca24d42805df0b8035cfb881ad6e5a1bf4b2"
    )
    p = Part(block=block, message=message)
    db.session.add(p)
    db.session.commit()
    return p


def test_direct_fetching(
    api_client, db, message, fake_attachment, monkeypatch
) -> None:
    # Mark a file as missing and check that we try to
    # fetch it from the remote provider.
    get_mock = mock.Mock(return_value=None)
    monkeypatch.setattr("inbox.util.blockstore.get_from_blockstore", get_mock)

    save_mock = mock.Mock()
    monkeypatch.setattr("inbox.util.blockstore.save_to_blockstore", save_mock)

    # Mock the request to return the contents of an actual email.
    path = os.path.join(  # noqa: PTH118
        os.path.dirname(os.path.abspath(__file__)),  # noqa: PTH100, PTH120
        "..",
        "data",
        "raw_message_with_filename_attachment.txt",
    )
    data = ""
    with open(path, "rb") as fd:  # noqa: PTH123
        data = fd.read()

    raw_mock = mock.Mock(return_value=data)
    monkeypatch.setattr(
        "inbox.s3.backends.gmail.get_gmail_raw_contents", raw_mock
    )

    resp = api_client.get_raw(
        f"/files/{fake_attachment.block.public_id}/download"
    )

    for m in [get_mock, save_mock, raw_mock]:
        assert m.called

    # Check that we got back the right data, with the right headers.
    assert (
        resp.headers["Content-Disposition"]
        == "attachment; filename=zambla.txt"
    )
    assert resp.data.decode("utf8") == "Chuis pas rassur\xe9"
