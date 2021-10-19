import setproctitle


def test_setproctitle_works():
    original_proctitle = setproctitle.getproctitle()

    setproctitle.setproctitle(test_setproctitle_works.__name__)
    assert setproctitle.getproctitle() == test_setproctitle_works.__name__

    setproctitle.setproctitle(original_proctitle)
