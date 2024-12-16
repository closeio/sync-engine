#!/usr/bin/env python

import random
import signal
import subprocess
import sys
import threading
import time

import click


@click.command()
@click.option(
    "--exit-after",
    default=None,
    help="A colon-separated range in minutes within which the "
    "child will get terminated. For example, if 30:60 is given, a "
    "random time between 30 and 60 minutes is picked after "
    "which the child will get terminated. This can be used to circumenvent "
    "uncontrolled memory leaks in long running children.",
)
@click.option(
    "--terminate-timeout",
    default=30,
    help="The grace period in seconds to wait for the child "
    "to terminate after SIGTERM is sent. If the child is still "
    "running after this period, SIGKILL is sent.",
)
@click.argument("command", nargs=-1, type=str)
def main(exit_after: str, terminate_timeout: int, command: list[str]) -> int:
    if not command:
        print("No command provided", file=sys.stderr)
        return 1

    print(
        "Running child: '"
        + " ".join(command)
        + f"' with grace period for termination: {terminate_timeout} seconds"
    )

    process = subprocess.Popen(command)

    prepare_exit_after(process, exit_after)
    signal.signal(
        signal.SIGTERM, lambda *_: terminate(process, terminate_timeout)
    )
    signal.signal(
        signal.SIGINT, lambda *_: terminate(process, terminate_timeout)
    )

    return_code = process.wait()

    print(f"Child exited with return code {return_code}")

    return return_code


def prepare_exit_after(
    process: "subprocess.Popen[bytes]", exit_after: "str | None"
) -> None:
    """
    Prepare to exit after a random time within the given range.

    Starts a daemon thread that will sleep for a random time within the given range
    and then terminate the process.
    """
    if not exit_after:
        return

    exit_after = exit_after.split(":")  # type: ignore[assignment]
    exit_after_min, exit_after_max = (int(exit_after[0]), int(exit_after[1]))
    exit_after_seconds = random.randint(
        exit_after_min * 60, exit_after_max * 60
    )

    exit_after_thread = threading.Thread(
        target=perform_exit_after,
        args=(process, exit_after_seconds),
        daemon=True,
    )
    exit_after_thread.start()


def perform_exit_after(process: subprocess.Popen[bytes], seconds: int) -> None:
    print(f"Will terminate child process after {seconds} seconds")
    time.sleep(seconds)
    print(f"Terminating child process after {seconds} seconds")
    terminate(process)


def terminate(  # type: ignore[return]
    process: subprocess.Popen[bytes], timeout: int = 30
) -> int:
    """
    Terminate the given process.

    Sends SIGTERM to the process and waits for the given timeout. If the process
    is still running after the timeout, sends SIGKILL to the process.
    """
    process.terminate()
    print("Sent SIGTERM to child")
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"Grace period of {timeout} seconds expired, sending SIGKILL to child"
        )
        process.kill()


if __name__ == "__main__":
    sys.exit(main())
