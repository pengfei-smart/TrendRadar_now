import unittest
from unittest.mock import patch

from trendradar.storage.remote import RemoteStorageBackend


class RemoteBatchTests(unittest.TestCase):
    def make_backend(self):
        with patch(
            "trendradar.storage.remote.HAS_BOTO3", True
        ), patch(
            "trendradar.storage.remote.boto3.client"
        ):
            backend = RemoteStorageBackend(
                bucket_name="test",
                access_key_id="test",
                secret_access_key="test",
                endpoint_url="https://example.com",
                temp_dir="test_remote_batch_tmp",
            )
        return backend

    def test_success_clears_dirty_queue(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend._batch_dirty.add(task)
        backend._upload_sqlite = lambda *args: True

        backend.end_batch()

        self.assertEqual(backend._batch_dirty, set())
        self.assertFalse(backend._batch_mode)

    def test_failed_upload_keeps_dirty_task(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend._batch_dirty.add(task)
        backend._upload_sqlite = lambda *args: False

        with self.assertRaises(RuntimeError):
            backend.end_batch()

        self.assertIn(task, backend._batch_dirty)

    def test_partial_success_keeps_only_failed_task(self):
        backend = self.make_backend()
        success = ("2026-11-26", "news")
        failure = ("2026-11-26", "rss")
        backend._batch_dirty.update({success, failure})

        def upload(date, db_type):
            return db_type == "news"

        backend._upload_sqlite = upload
        with self.assertRaises(RuntimeError):
            backend.end_batch()

        self.assertEqual(backend._batch_dirty, {failure})

    def test_failed_task_can_be_retried(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend._batch_dirty.add(task)
        attempts = {"count": 0}

        def upload(*args):
            attempts["count"] += 1
            return attempts["count"] > 1

        backend._upload_sqlite = upload
        with self.assertRaises(RuntimeError):
            backend.end_batch()
        backend.end_batch()

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(backend._batch_dirty, set())

    def test_begin_batch_preserves_previous_failed_tasks(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend._batch_dirty.add(task)

        backend.begin_batch()

        self.assertIn(task, backend._batch_dirty)

    def test_one_exception_does_not_skip_other_tasks(self):
        backend = self.make_backend()
        first = ("2026-11-26", "news")
        second = ("2026-11-26", "rss")
        backend._batch_dirty.update({first, second})
        attempted = []

        def upload(date, db_type):
            attempted.append(db_type)
            if db_type == "news":
                raise OSError("simulated upload failure")
            return True

        backend._upload_sqlite = upload

        with self.assertRaises(Exception):
            backend.end_batch()

        self.assertEqual(set(attempted), {"news", "rss"})



    def test_repeated_begin_preserves_queued_tasks(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend.begin_batch()
        backend._batch_dirty.add(task)

        backend.begin_batch()

        self.assertIn(task, backend._batch_dirty)

    def test_old_failed_and_new_tasks_can_upload_together(self):
        backend = self.make_backend()
        old_task = ("2026-11-26", "news")
        new_task = ("2026-11-27", "rss")
        backend._batch_dirty.add(old_task)
        backend._upload_sqlite = lambda *args: False

        with self.assertRaises(RuntimeError):
            backend.end_batch()

        backend.begin_batch()
        backend._batch_dirty.add(new_task)
        uploaded = []
        backend._upload_sqlite = lambda date, db_type: (
            uploaded.append((date, db_type)) or True
        )

        backend.end_batch()

        self.assertEqual(set(uploaded), {old_task, new_task})
        self.assertEqual(backend._batch_dirty, set())

    def test_all_failed_tasks_are_retained(self):
        backend = self.make_backend()
        tasks = {
            ("2026-11-26", "news"),
            ("2026-11-26", "rss"),
        }
        backend._batch_dirty.update(tasks)
        backend._upload_sqlite = lambda *args: False

        with self.assertRaises(RuntimeError):
            backend.end_batch()

        self.assertEqual(backend._batch_dirty, tasks)


    def test_cleanup_preserves_temp_dir_when_dirty(self):
        backend = self.make_backend()
        task = ("2026-11-26", "news")
        backend._batch_dirty.add(task)

        marker = backend.temp_dir / "pending.db"
        marker.write_bytes(b"pending database")

        with self.assertRaisesRegex(RuntimeError, "仍有待上传任务"):
            backend.cleanup()

        self.assertTrue(backend.temp_dir.exists())
        self.assertTrue(marker.exists())
        self.assertIn(task, backend._batch_dirty)

        # 测试结束前主动解除待上传状态，再正常清理。
        backend._batch_dirty.clear()
        backend.cleanup()
        self.assertFalse(backend.temp_dir.exists())

    def test_cleanup_removes_temp_dir_when_queue_is_empty(self):
        backend = self.make_backend()
        marker = backend.temp_dir / "clean.db"
        marker.write_bytes(b"clean database")

        backend.cleanup()

        self.assertFalse(backend.temp_dir.exists())
        self.assertEqual(backend._batch_dirty, set())


if __name__ == "__main__":
    unittest.main()
