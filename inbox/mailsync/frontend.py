import threading

from flask import Flask, jsonify, request
from pympler import muppy, summary  # type: ignore[import-untyped]
from werkzeug.serving import WSGIRequestHandler, run_simple

from inbox.instrumentation import ProfileCollector


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
