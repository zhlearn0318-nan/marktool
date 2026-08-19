from app.metadata.job_store import (
    IdempotencyConflictError,
    SQLiteMetadataJobStore,
)
from tests.fixtures import VALID_AIGC


def _job_values(job_id="job_1", key=None, fingerprint="fingerprint"):
    return {
        "job_id": job_id,
        "request_id": f"req_{job_id}",
        "idempotency_key": key,
        "request_fingerprint": fingerprint,
        "status": "queued",
        "stage": "queued",
        "progress": None,
        "created_at": "2026-08-19T00:00:00Z",
        "updated_at": "2026-08-19T00:00:00Z",
        "original_file_name": "source.png",
        "detected_mime_type": "image/png",
        "input_size_bytes": 100,
        "input_sha256": "input-hash",
        "input_path": "/internal/original.png",
        "output_path": "/internal/output.png",
        "output_file_name": "source_labeled.png",
        "request_json": {
            "standard": "GB45438-2025",
            "modality": "image",
            "existing_metadata_policy": "reject",
            "AIGC": VALID_AIGC,
        },
    }


def test_job_store_persists_and_recovers_running_job(tmp_path):
    store = SQLiteMetadataJobStore(str(tmp_path / "jobs.sqlite3"))
    created = store.create(_job_values())
    assert created.created is True
    assert created.record["request"]["AIGC"] == VALID_AIGC

    assert store.claim("job_1", "2026-08-19T00:00:01Z") is True
    recovered = store.recover_incomplete("2026-08-19T00:00:02Z")
    assert recovered == ["job_1"]
    assert store.get("job_1")["status"] == "queued"


def test_job_store_keeps_audit_fields_for_success_and_failure(tmp_path):
    store = SQLiteMetadataJobStore(str(tmp_path / "jobs.sqlite3"))
    store.create(_job_values("job_success"))
    store.claim("job_success", "2026-08-19T00:00:01Z")
    store.succeed(
        "job_success",
        timestamp="2026-08-19T00:00:02Z",
        expires_at="2026-08-20T00:00:02Z",
        output_mime_type="image/png",
        output_size_bytes=120,
        output_sha256="output-hash",
        carrier="xmp-aigc-v1",
        adapter_version="jpeg-png-xmp-exiftool-v1",
        embedded_metadata={"AIGC": VALID_AIGC},
        validation={"schema_valid": True},
    )
    success = store.get("job_success")
    assert success["status"] == "succeeded"
    assert success["adapter_version"] == "jpeg-png-xmp-exiftool-v1"
    assert success["embedded_metadata"]["AIGC"] == VALID_AIGC

    store.create(_job_values("job_failure"))
    store.claim("job_failure", "2026-08-19T00:00:03Z")
    store.update_stage("job_failure", "verifying_metadata", "2026-08-19T00:00:04Z")
    store.fail(
        "job_failure",
        timestamp="2026-08-19T00:00:05Z",
        expires_at="2026-08-20T00:00:05Z",
        code="METADATA_READBACK_FAILED",
        message="回读失败",
        retryable=False,
    )
    failure = store.get("job_failure")
    assert failure["status"] == "failed"
    assert failure["failed_at_stage"] == "verifying_metadata"
    assert failure["error_code"] == "METADATA_READBACK_FAILED"


def test_idempotency_key_is_repeatable_only_for_identical_request(tmp_path):
    store = SQLiteMetadataJobStore(str(tmp_path / "jobs.sqlite3"))
    first = store.create(_job_values("job_1", key="retry-key"))
    repeated = store.create(_job_values("job_2", key="retry-key"))
    assert first.created is True
    assert repeated.created is False
    assert repeated.record["job_id"] == "job_1"

    try:
        store.create(
            _job_values("job_3", key="retry-key", fingerprint="different")
        )
    except IdempotencyConflictError:
        pass
    else:
        raise AssertionError("不同请求不得复用同一个 Idempotency-Key")
