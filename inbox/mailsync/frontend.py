import random
import threading
import time

import structlog
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from pympler import muppy, summary  # type: ignore[import-untyped]
from werkzeug.serving import WSGIRequestHandler, run_simple

from inbox.instrumentation import ProfileCollector
from inbox.interruptible_threading import InterruptibleThread

log = structlog.get_logger()


class ProfilingHTTPFrontend:
    """
    This is a lightweight embedded HTTP server that runs inside a mailsync
    or syncback process. It allows you to programmatically interact with the
    process: to get profile/memory/load metrics, or to schedule new account
    syncs.
    """  # noqa: D404

    def __init__(self, port, profile) -> None:  # type: ignore[no-untyped-def]
        self.port = port
        self.profiler = ProfileCollector() if profile else None
        # Start reporting as unhealthy after 240-360 minutes to allow
        # this process to be restarted after this time.
        self.report_unhealthy_at = time.monotonic() + random.randint(
            240 * 60, 360 * 60
        )

    def _create_app(self):  # type: ignore[no-untyped-def]
        app = Flask(__name__)
        app.config["JSON_SORT_KEYS"] = False
        self._create_app_impl(app)
        return app

    def start(self) -> None:
        if self.profiler is not None:
            self.profiler.start()

        app = self._create_app()
        threading._start_new_thread(  # type: ignore[attr-defined]
            run_simple,
            ("0.0.0.0", self.port, app),
            {"request_handler": _QuietHandler},
        )

    def _create_app_impl(self, app) -> None:  # type: ignore[no-untyped-def]
        @app.route("/profile")
        def profile():  # type: ignore[no-untyped-def]
            if self.profiler is None:
                return ("Profiling disabled\n", 404)
            resp = self.profiler.stats()
            if request.args.get("reset ") in (1, "true"):
                self.profiler.reset()
            return resp

        @app.route("/load")
        def load() -> str:
            return "Load tracing disabled\n"

        @app.route("/health")
        def health() -> ResponseReturnValue:
            now = time.monotonic()
            threads = [
                thread
                for thread in threading.enumerate()
                if isinstance(thread, InterruptibleThread)
            ]
            threads_count = len(threads)
            threads_delayed_5m_count = sum(
                1
                for thread in threads
                if not thread.last_ping_time
                or now - thread.last_ping_time > 5 * 60
            )
            threads_delayed_20m_count = sum(
                1
                for thread in threads
                if not thread.last_ping_time
                or now - thread.last_ping_time > 20 * 60
            )
            threads_delayed_60m_count = sum(
                1
                for thread in threads
                if not thread.last_ping_time
                or now - thread.last_ping_time > 60 * 60
            )

            longevity_deadline_reached = now >= self.report_unhealthy_at
            service_stuck = (
                # Treat as stuck if there are threads running, and:
                threads_count
                and (
                    # Any of them are delayed by 60m+
                    threads_delayed_60m_count
                    or (
                        # Or there are at least 50 threads, and 10%+ are
                        # delayed by 20m+
                        threads_count >= 50
                        and threads_delayed_20m_count / threads_count >= 0.1
                    )
                    or (
                        # Or there are at least 10 threads, and 40%+ are
                        # delayed by 5m+
                        threads_count >= 10
                        and threads_delayed_5m_count / threads_count >= 0.4
                    )
                )
            )

            is_healthy = not longevity_deadline_reached and not service_stuck
            stats = {
                "threads_delayed_5m_count": threads_delayed_5m_count,
                "threads_delayed_20m_count": threads_delayed_20m_count,
                "threads_delayed_60m_count": threads_delayed_60m_count,
                "max_delay": max(
                    # XXX: Temporary. Remove me if everything's working fine in prod.
                    (
                        (
                            now - thread.last_ping_time
                            if thread.last_ping_time is not None
                            else -1
                        ),
                        thread.__class__.__name__,
                    )
                    for thread in threads
                ),
                "threads_count": threads_count,
                "longevity_deadline_reached": longevity_deadline_reached,
                "is_healthy": is_healthy,
            }

            if service_stuck:
                log.error("The service is stuck", stats=stats)

            response_status = 200 if is_healthy else 503
            return jsonify(stats), response_status

        @app.route("/mem")
        def mem():  # type: ignore[no-untyped-def]
            objs = muppy.get_objects()
            summ = summary.summarize(objs)
            return "\n".join(summary.format_(summ)) + "\n"


class SyncbackHTTPFrontend(ProfilingHTTPFrontend):
    pass


class SyncHTTPFrontend(ProfilingHTTPFrontend):
    def __init__(  # type: ignore[no-untyped-def]
        self, sync_service, port, profile
    ) -> None:
        self.sync_service = sync_service
        super().__init__(port, profile)

    def _create_app_impl(self, app) -> None:  # type: ignore[no-untyped-def]
        super()._create_app_impl(app)

        @app.route("/unassign", methods=["POST"])
        def unassign_account():  # type: ignore[no-untyped-def]
            account_id = request.json["account_id"]  # type: ignore[index]
            ret = self.sync_service.stop_sync(account_id)
            if ret:
                return "OK"
            else:
                return ("Account not assigned to this process", 409)

        @app.route("/build-metadata", methods=["GET"])
        def build_metadata():  # type: ignore[no-untyped-def]
            filename = "/usr/share/python/cloud-core/metadata.txt"
            with open(filename) as f:  # noqa: PTH123
                _, build_id = f.readline().rstrip("\n").split()
                build_id = build_id[
                    1:-1
                ]  # Remove first and last single quotes.
                _, git_commit = f.readline().rstrip("\n").split()
                return jsonify(
                    {"build_id": build_id, "git_commit": git_commit}
                )


class _QuietHandler(WSGIRequestHandler):
    def log_request(  # type: ignore[no-untyped-def]
        self, *args, **kwargs
    ) -> None:
        """Suppress request logging so as not to pollute application logs."""
