import socket
import sys
from code import InteractiveConsole

from inbox.logging import get_logger

log = get_logger()

doc = """
This is the Nylas console - you can use it to interact with mailsync and track memory leaks.

Happy hacking!

"""


class RemoteConsole(InteractiveConsole):
    def __init__(  # type: ignore[no-untyped-def]
        self, socket, locals=None
    ) -> None:
        self.socket = socket
        self.handle = socket.makefile("rw")
        InteractiveConsole.__init__(self, locals=locals)
        self.handle.write(doc)

    def write(self, data) -> None:  # type: ignore[no-untyped-def]
        self.handle.write(data)

    def runcode(self, code) -> None:  # type: ignore[no-untyped-def]
        # preserve stdout/stderr
        oldstdout = sys.stdout
        oldstderr = sys.stderr
        sys.stdout = self.handle
        sys.stderr = self.handle

        InteractiveConsole.runcode(self, code)

        sys.stdout = oldstdout
        sys.stderr = oldstderr

    def interact(  # type: ignore[no-untyped-def, override]
        self, banner=None
    ) -> None:
        """
        Closely emulate the interactive Python console.

        The optional banner argument specify the banner to print
        before the first interaction; by default it prints a banner
        similar to the one printed by the real Python interpreter,
        followed by the current class name in parentheses (so as not
        to confuse this with the real interpreter -- since it's so
        close!).

        """  # noqa: D401
        try:
            sys.ps1  # noqa: B018
        except AttributeError:
            sys.ps1 = ">>> "
        try:
            sys.ps2  # noqa: B018
        except AttributeError:
            sys.ps2 = "... "
        cprt = 'Type "help", "copyright", "credits" or "license" for more information.'
        if banner is None:
            self.write(
                "Python {} on {}\n{}\n({})\n".format(
                    sys.version, sys.platform, cprt, self.__class__.__name__
                )
            )
        else:
            self.write(str(banner) + "\n")
        more = 0
        while True:
            try:
                if more:
                    prompt = sys.ps2
                else:
                    prompt = sys.ps1
                try:
                    line = self.raw_input(prompt)  # type: ignore[arg-type]
                    self.handle.flush()
                    # Can be None if sys.stdin was redefined
                    encoding = getattr(sys.stdin, "encoding", None)
                    if encoding and isinstance(line, bytes):
                        line = line.decode(encoding)
                except EOFError:
                    self.terminate()
                    return
                except OSError:
                    self.terminate()
                    return
                else:
                    more = self.push(line)
            except KeyboardInterrupt:
                self.write("\nKeyboardInterrupt\n")
                self.resetbuffer()
                more = 0

    def terminate(self) -> None:
        try:
            self.handle.close()
            self.socket.close()
        except OSError:
            return

    def raw_input(self, prompt: str = ""):  # type: ignore[no-untyped-def]  # noqa: ANN201
        self.handle.write(prompt)
        self.handle.flush()
        return self.handle.readline()


def break_to_interpreter(  # type: ignore[no-untyped-def]
    host: str = "localhost", port=None
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if port is None:
        # Let the OS pick a port automatically.
        port = 0

    sock.bind((host, port))
    sock.listen(1)
    address = sock.getsockname()
    log.debug("Nylas console waiting", address=address)
    while True:
        (clientsocket, address) = sock.accept()
        console = RemoteConsole(clientsocket, locals())
        console.interact()


# example usage - connect with 'netcat localhost 4444'
if __name__ == "__main__":
    break_to_interpreter(port=4444)
