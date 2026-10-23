"""Runs the ETL in a background thread after an upload, so the page returns at once.

Fine for a single-server portfolio app. In production you would hand this
to a task queue such as Celery or Django-Q instead of a thread.
"""
import logging
import shutil
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .audit import log_action
from .models import AuditLog, DataUpload

logger = logging.getLogger("core")
_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)


def is_running():
    """True while a load is in progress. Loads stuck for 30+ min are marked failed."""
    DataUpload.objects.filter(status=DataUpload.RUNNING,
                              created_at__lt=timezone.now() - STALE_AFTER).update(
        status=DataUpload.FAILED, message="Interrupted: the server stopped during the load.",
        finished_at=timezone.now())
    return DataUpload.objects.filter(status=DataUpload.RUNNING).exists()


def store_upload(uploaded_file, spec):
    """Save the upload over the dataset's file in DATA_DIR, archiving the old one."""
    data_dir, archive_dir = settings.DATA_DIR, settings.ARCHIVE_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / spec.filename
    if target.exists():
        stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(target, archive_dir / f"{stamp}_{spec.filename}")
    uploaded_file.seek(0)
    with open(target, "wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return target


def start(upload_id):
    thread = threading.Thread(target=_run, args=(upload_id,), daemon=True, name=f"etl-{upload_id}")
    thread.start()
    return thread


def _run(upload_id):
    from etl.etl_pipeline import run_etl   # imported here: pandas is slow to import

    upload = DataUpload.objects.select_related("user").get(pk=upload_id)
    who = upload.user.get_username() if upload.user else "web"
    try:
        with _lock:
            summary = run_etl(data_dir=settings.DATA_DIR, triggered_by=who,
                              source=f"upload: {upload.dataset} ({upload.original_name})")
        upload.status = DataUpload.SUCCESS
        upload.etl_run_id = summary.get("run_id")
        upload.rows_loaded = summary.get("rows_loaded")
        upload.message = (f"Loaded {summary.get('rows_loaded', 0):,} new or changed rows in "
                          f"{summary.get('duration_sec', 0)} s.")
        log_action(upload.user, AuditLog.ETL_SUCCESS, upload.message, obj=upload)
    except Exception as exc:   # noqa: BLE001 - we want every failure in the log
        logger.exception("ETL failed for upload %s", upload_id)
        upload.status = DataUpload.FAILED
        upload.message = f"ETL failed: {exc}"[:2000]
        log_action(upload.user, AuditLog.ETL_FAILED, upload.message[:500], obj=upload)
    finally:
        upload.finished_at = timezone.now()
        upload.save()
        connection.close()
