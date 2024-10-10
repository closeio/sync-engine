import threading


def enumerate():
    for thread in threading.enumerate():
        if thread.daemon or thread is threading.main_thread():
            continue

        yield thread
