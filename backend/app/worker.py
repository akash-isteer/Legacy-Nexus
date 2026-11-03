"""Background worker: processes analysis and indexing jobs from Azure Service Bus.

Run with ``python -m app.worker``. On Azure this runs as a Container Apps job (or app) that scales on
queue length, including to zero. Each message is completed only after the job finished; if a worker dies
mid-job, Service Bus redelivers the message to another worker after the lock expires.
"""
from __future__ import annotations

import json
import logging
import os
import signal

from app.config import get_settings
from app.database import init_all
from app.services import jobs

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("legacynexus.worker")

MAX_LOCK_RENEWAL_SECONDS = 6 * 3600  # large estates can take hours to analyse
_stop = False


def _request_stop(*_) -> None:
    global _stop
    _stop = True
    log.info("Stop requested; finishing the current job")


def process(receiver, message) -> None:
    try:
        body = json.loads(str(message))
        log.info("Job %s for project %s", body.get("kind"), body.get("project_id"))
        jobs.handle(body)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("Dead-lettering malformed job: %s", exc)
        receiver.dead_letter_message(message, reason="malformed", error_description=str(exc)[:1000])
        return
    except Exception:  # unexpected failure: let Service Bus retry, then dead-letter after max deliveries
        log.exception("Job failed; returning it to the queue")
        receiver.abandon_message(message)
        return
    receiver.complete_message(message)


def run(once: bool = False) -> None:
    from azure.servicebus import AutoLockRenewer

    settings = get_settings()
    if settings.job_backend.lower() != "servicebus":
        raise SystemExit("The worker is only needed when JOB_BACKEND=servicebus")
    init_all()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    renewer = AutoLockRenewer(max_lock_renewal_duration=MAX_LOCK_RENEWAL_SECONDS)
    log.info("Worker listening on queue %s", settings.servicebus_queue)
    with jobs.servicebus_client() as client, client.get_queue_receiver(
            settings.servicebus_queue, max_wait_time=30, auto_lock_renewer=renewer) as receiver:
        while not _stop:
            batch = receiver.receive_messages(max_message_count=1, max_wait_time=30)
            if not batch and once:
                break
            for message in batch:
                process(receiver, message)
            if once and batch:
                break
    renewer.close()


if __name__ == "__main__":
    run(once=os.getenv("WORKER_RUN_ONCE", "").lower() in {"1", "true", "yes"})
