"""Post-fork Loki wiring lives in create_app; master logs must also be safe."""

from byteforge_mcp_file_friend_core.observability import SafeRecord
from prometheus_client import multiprocess

bind = "0.0.0.0:8081"
workers = 4
timeout = 30
preload_app = False
accesslog = None


def on_starting(server):
    for logger in (server.log.error_log, server.log.access_log):
        for handler in logger.handlers:
            handler.addFilter(SafeRecord())


def child_exit(server, worker):
    multiprocess.mark_process_dead(worker.pid)
