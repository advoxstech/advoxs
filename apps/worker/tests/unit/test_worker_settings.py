from app.worker import WorkerSettings


def test_worker_functions_registered() -> None:
    names = {fn.__name__ for fn in WorkerSettings.functions}

    assert names == {"ingest_knowledge_base_file", "process_inbound_message"}
