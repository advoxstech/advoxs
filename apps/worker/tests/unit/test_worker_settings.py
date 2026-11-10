from app.tasks.knowledge_base import INGESTION_QUEUE
from app.worker import IngestionWorkerSettings, WorkerSettings


def _names(functions) -> set[str]:
    return {fn.name if hasattr(fn, "name") else fn.__name__ for fn in functions}


def test_worker_functions_registered() -> None:
    assert _names(WorkerSettings.functions) == {
        "process_inbound_message",
        "recover_inbound_message_jobs",
        "deliver_outbound_message",
        "deliver_team_emails",
        "recover_outbound_message_jobs",
        "process_conversation_cleanup",
        "recover_conversation_cleanup_jobs",
        "deliver_follow_up_batch",
        "recover_follow_up_batches",
    }


def test_follow_up_recovery_can_reenqueue_finished_batches():
    deliver = next(
        fn
        for fn in WorkerSettings.functions
        if getattr(fn, "name", None) == "deliver_follow_up_batch"
    )
    assert deliver.keep_result_s == 0
    assert any(
        job.coroutine.__name__ == "recover_follow_up_batches" for job in WorkerSettings.cron_jobs
    )


def test_ingestion_runs_alone_in_its_own_queue():
    assert _names(IngestionWorkerSettings.functions) == {
        "ingest_knowledge_base_file",
        "copy_public_document",
    }
    assert IngestionWorkerSettings.queue_name == INGESTION_QUEUE
    assert IngestionWorkerSettings.max_jobs == 1
    assert IngestionWorkerSettings.job_timeout > WorkerSettings.job_timeout


def test_drive_recovery_can_reenqueue_finished_jobs():
    ingest = IngestionWorkerSettings.functions[0]
    assert ingest.keep_result_s == 0
    assert any(
        job.coroutine.__name__ == "recover_drive_imports" for job in WorkerSettings.cron_jobs
    )
