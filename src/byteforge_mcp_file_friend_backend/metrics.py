"""Private, bounded HTTP metrics with Gunicorn multiprocess aggregation."""

import os
import time

from byteforge_mcp_file_friend_core.observability import METHODS, event
from flask import g, request
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_flask_exporter.multiprocess import GunicornInternalPrometheusMetrics


def route_class():
    endpoint = request.endpoint or ""
    if endpoint.startswith("files."):
        return "files"
    if endpoint.startswith("hooks."):
        return "hooks"
    return endpoint if endpoint in ("auth", "health", "ready") else "other"


def instrument(app):
    registry = CollectorRegistry()
    cls = GunicornInternalPrometheusMetrics if "PROMETHEUS_MULTIPROC_DIR" in os.environ else PrometheusMetrics
    metrics = cls(app, path="/metrics", export_defaults=False, registry=registry)
    requests = Counter("ff_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=registry)
    duration = Histogram("ff_http_request_duration_seconds", "HTTP duration", ["method", "route"], registry=registry)
    db_errors = Counter("ff_database_errors_total", "API database unavailable responses", registry=registry)
    app.extensions["ff_database_errors"] = db_errors
    app.extensions["ff_metrics"] = metrics

    @app.before_request
    def started():
        g.ff_started = time.monotonic()

    @app.after_request
    def finished(response):
        if request.path == "/metrics":
            return response
        method = request.method if request.method in METHODS else "OTHER"
        route = route_class()
        elapsed = max(0, time.monotonic() - getattr(g, "ff_started", time.monotonic()))
        requests.labels(method, route, str(response.status_code)).inc()
        duration.labels(method, route).observe(elapsed)
        event("request_completed", method=method, route=route, status=response.status_code, duration_seconds=elapsed)
        return response
