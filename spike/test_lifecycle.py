"""Crash and cross-process race tests for the lifecycle prototype."""

import hashlib
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path

from byteforge_mcp_file_friend_core.lifecycle import Lifecycle

FILE_ID = "b" * 32
DATA = b"crash-recovery-fixture" * 8192


def local_terminate(root: Path, file_id: str) -> None:
    """Fake only the storage adapter; real tusd DELETE is tested in validate.py."""
    (root / "uploads" / file_id).unlink(missing_ok=True)
    (root / "uploads" / f"{file_id}.info").unlink(missing_ok=True)


def crash_worker(root: str, point: str, operation: str) -> None:
    def fault(actual: str) -> None:
        if point == actual:
            os._exit(73)  # No Python cleanup: locks must be released by the OS.

    path = Path(root)
    store = Lifecycle(path, "https://example.invalid", fault)
    if operation == "verify":
        store.verify(FILE_ID)
    else:
        store.request_delete(FILE_ID)

        def terminate(file_id: str) -> None:
            local_terminate(path, file_id)

        store.drain_delete(FILE_ID, terminate)


def racing_verifier(root: str, hashed: object, release: object) -> None:
    def fault(point: str) -> None:
        if point == "hashed":
            hashed.set()
            if not release.wait(5):
                raise RuntimeError("Parent did not release verifier")

    Lifecycle(Path(root), "https://example.invalid", fault).verify(FILE_ID)


def simultaneous_verifier(root: str) -> None:
    Lifecycle(Path(root), "https://example.invalid").verify(FILE_ID)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ff-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Lifecycle(self.root, "https://example.invalid")
        self.context = multiprocessing.get_context("spawn")
        self.metadata = {"filename": "fixture.bin", "sha256": hashlib.sha256(DATA).hexdigest()}
        (self.root / "uploads" / FILE_ID).write_bytes(DATA)
        (self.root / "uploads" / f"{FILE_ID}.info").write_text(
            json.dumps(
                {
                    "ID": FILE_ID,
                    "MetaData": self.metadata,
                    "Size": len(DATA),
                    "Offset": 0,
                }
            )
        )
        self.store.register(FILE_ID, self.metadata, len(DATA))

    def terminate(self, file_id: str) -> None:
        local_terminate(self.root, file_id)

    def run_crash(self, point: str, operation: str) -> None:
        process = self.context.Process(target=crash_worker, args=(str(self.root), point, operation))
        process.start()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail("Crash worker timed out")
        self.assertEqual(process.exitcode, 73)

    def test_publication_crash_boundaries(self) -> None:
        for point in ("verifying", "hashed", "publishing", "linked", "ready"):
            with self.subTest(point=point):
                # Fresh state for each crash boundary.
                self.store.unpublish(self.store.read(FILE_ID))
                with self.store.lock(FILE_ID):
                    record = self.store.read(FILE_ID)
                    record["state"] = "uploading"
                    self.store.write(record)
                self.run_crash(point, "verify")
                restarted = Lifecycle(self.root, "https://example.invalid")
                restarted.reconcile(self.terminate)
                self.assertEqual(restarted.status(FILE_ID)["state"], "ready")
                self.assertEqual(restarted.published(restarted.read(FILE_ID)).read_bytes(), DATA)

    def test_deletion_crash_boundaries(self) -> None:
        for point in ("deleting", "unpublished", "terminated", "deleted"):
            with self.subTest(point=point):
                (self.root / "uploads" / FILE_ID).write_bytes(DATA)
                with self.store.lock(FILE_ID):
                    record = self.store.read(FILE_ID)
                    record["state"] = "uploading"
                    self.store.write(record)
                self.store.verify(FILE_ID)
                self.run_crash(point, "delete")
                restarted = Lifecycle(self.root, "https://example.invalid")
                restarted.reconcile(self.terminate)
                self.assertEqual(restarted.status(FILE_ID)["state"], "deleted")
                self.assertFalse(restarted.published(restarted.read(FILE_ID)).exists())
                self.assertFalse(restarted.source(FILE_ID).exists())
                restarted.register(FILE_ID, self.metadata, len(DATA))
                restarted.verify(FILE_ID)
                self.assertEqual(restarted.status(FILE_ID)["state"], "deleted")

    def test_delete_wins_against_verifier_in_another_process(self) -> None:
        hashed, release = self.context.Event(), self.context.Event()
        process = self.context.Process(target=racing_verifier, args=(str(self.root), hashed, release))
        process.start()
        try:
            self.assertTrue(hashed.wait(5))
            self.store.request_delete(FILE_ID)
            self.store.drain_delete(FILE_ID, self.terminate)
            release.set()
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(self.store.status(FILE_ID)["state"], "deleted")
            self.assertFalse(self.store.published(self.store.read(FILE_ID)).exists())
        finally:
            release.set()
            if process.is_alive():
                process.kill()
                process.join()

    def test_duplicate_verifiers_are_idempotent(self) -> None:
        processes = [
            self.context.Process(target=simultaneous_verifier, args=(str(self.root),)) for _ in range(3)
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(self.store.status(FILE_ID)["state"], "ready")
            self.assertEqual(self.store.published(self.store.read(FILE_ID)).read_bytes(), DATA)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join()

    def test_reconcile_without_either_notification(self) -> None:
        # Admission committed before creation; both subsequent notifications were lost.
        self.store.reconcile(self.terminate)
        self.assertEqual(self.store.status(FILE_ID)["state"], "ready")

    def test_partial_is_not_published(self) -> None:
        self.store.source(FILE_ID).write_bytes(DATA[:100])
        self.store.reconcile(self.terminate)
        self.assertEqual(self.store.status(FILE_ID)["state"], "uploading")
        self.assertFalse(self.store.published(self.store.read(FILE_ID)).exists())

    def test_hash_mismatch_cleanup_and_failure_survives_restart(self) -> None:
        self.store.source(FILE_ID).write_bytes(b"x" * len(DATA))
        self.store.reconcile(self.terminate)
        restarted = Lifecycle(self.root, "https://example.invalid")
        restarted.reconcile(self.terminate)
        self.assertEqual(
            restarted.status(FILE_ID),
            {
                "id": FILE_ID,
                "state": "failed",
                "error": "hash_mismatch",
            },
        )
        self.assertFalse(self.store.source(FILE_ID).exists())
        self.assertFalse(self.store.published(self.store.read(FILE_ID)).exists())

    def test_sweep_unpublishes_expired_ready_file(self) -> None:
        self.store.verify(FILE_ID)
        with self.store.lock(FILE_ID):
            record = self.store.read(FILE_ID)
            record["expires_at"] = int(time.time()) - 1
            self.store.write(record)
        self.store.reconcile(self.terminate)
        self.assertEqual(self.store.status(FILE_ID)["state"], "expired")
        self.assertFalse(self.store.source(FILE_ID).exists())
        self.assertFalse(self.store.published(self.store.read(FILE_ID)).exists())

    def test_failed_termination_keeps_retryable_tombstone(self) -> None:
        self.store.verify(FILE_ID)
        self.store.request_delete(FILE_ID)

        def unavailable(file_id: str) -> None:
            raise ConnectionError("tusd unavailable")

        with self.assertRaises(ConnectionError):
            self.store.drain_delete(FILE_ID, unavailable)
        self.assertEqual(self.store.status(FILE_ID)["state"], "deleting")
        self.assertFalse(self.store.published(self.store.read(FILE_ID)).exists())
        self.store.reconcile(self.terminate)
        self.assertEqual(self.store.status(FILE_ID)["state"], "deleted")

    def test_unrelated_existing_link_is_not_overwritten(self) -> None:
        path = self.store.published(self.store.read(FILE_ID))
        path.parent.mkdir()
        path.write_bytes(b"unrelated")
        with self.assertRaisesRegex(RuntimeError, "inode"):
            self.store.verify(FILE_ID)
        self.assertEqual(path.read_bytes(), b"unrelated")
        self.assertNotEqual(self.store.status(FILE_ID)["state"], "ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
