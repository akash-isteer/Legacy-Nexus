"""Submits background work either in-process or to Azure Service Bus.

The API never does long work itself in production: it records the request and puts a small message on a
queue. Worker containers (``python -m app.worker``) pick messages up, so analysis capacity scales
independently of the web tier and costs nothing when idle.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi import BackgroundTasks

from app.config import get_settings

log = logging.getLogger("legacynexus.jobs")

ANALYZE = "analyze"
SEMANTIC_INDEX = "semantic_index"


class JobSubmitError(RuntimeError):
    """The job could not be queued (for example the queue service is unreachable)."""


def _handlers() -> dict[str, Callable[[int], None]]:
    from app.services import analysis_runner, semantic

    return {ANALYZE: analysis_runner.analyze_project, SEMANTIC_INDEX: semantic.build_index}


def submit(kind: str, project_id: int, background: BackgroundTasks | None = None) -> None:
    backend = get_settings().job_backend.lower()
    if kind not in _handlers():
        raise ValueError(f"Unknown job kind: {kind}")
    if backend == "inline":
        if background is None:
            raise ValueError("Inline jobs need FastAPI BackgroundTasks")
        background.add_task(_handlers()[kind], project_id)
    elif backend == "servicebus":
        _send_servicebus({"kind": kind, "project_id": project_id})
    else:
        raise ValueError(f"Unknown JOB_BACKEND '{backend}' (use inline or servicebus)")


def handle(body: dict) -> None:
    """Run one queued job. Used by the worker; unknown messages are rejected rather than retried forever."""
    kind, project_id = body.get("kind"), body.get("project_id")
    handler = _handlers().get(kind)
    if handler is None or not isinstance(project_id, int):
        raise ValueError(f"Malformed job message: {body!r}")
    handler(project_id)


# ----------------------------------------------------------------------------- Azure Service Bus

def servicebus_client():
    """Service Bus client using managed identity (namespace) or a connection string."""
    settings = get_settings()
    try:
        from azure.servicebus import ServiceBusClient
    except ImportError as exc:  # optional dependency, see requirements-azure.txt
        raise JobSubmitError("JOB_BACKEND=servicebus needs the azure-servicebus package") from exc
    if settings.servicebus_connection_string:
        return ServiceBusClient.from_connection_string(settings.servicebus_connection_string)
    if settings.servicebus_namespace:
        from azure.identity import DefaultAzureCredential

        return ServiceBusClient(settings.servicebus_namespace, credential=DefaultAzureCredential())
    raise JobSubmitError("Set SERVICEBUS_NAMESPACE (managed identity) or SERVICEBUS_CONNECTION_STRING")


def _send_servicebus(body: dict) -> None:
    settings = get_settings()
    try:
        from azure.servicebus import ServiceBusMessage

        with servicebus_client() as client, client.get_queue_sender(settings.servicebus_queue) as sender:
            sender.send_messages(ServiceBusMessage(json.dumps(body), content_type="application/json",
                                                   subject=body["kind"]))
    except JobSubmitError:
        raise
    except Exception as exc:  # network, auth or quota problems
        log.exception("Could not queue job %s", body)
        raise JobSubmitError("The job queue is unavailable; try again shortly") from exc
