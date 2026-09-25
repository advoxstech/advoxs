from app.worker import WorkerSettings


def test_worker_functions_registered() -> None:
    names = {fn.name if hasattr(fn, "name") else fn.__name__ for fn in WorkerSettings.functions}

    assert names == {
        "ingest_knowledge_base_file",
        "process_inbound_message",
        "recover_inbound_message_jobs",
        "deliver_outbound_message",
        "recover_outbound_message_jobs",
    }


def test_drive_recovery_can_reenqueue_finished_jobs():
    ingest = next(
        fn
        for fn in WorkerSettings.functions
        if getattr(fn, "name", None) == "ingest_knowledge_base_file"
    )
    assert ingest.keep_result_s == 0
    assert any(
        job.coroutine.__name__ == "recover_drive_imports" for job in WorkerSettings.cron_jobs
    )
