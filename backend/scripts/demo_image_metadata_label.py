"""演示 JPEG/PNG 文件元数据隐式标识的完整业务闭环。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import httpx


# 使用 ``python scripts/demo_image_metadata_label.py`` 直接运行时，Python 默认只把
# scripts 目录加入模块搜索路径。显式加入 backend 根目录，确保演示后半段能够导入
# app.metadata.exiftool_client；这不会修改系统级 PYTHONPATH。
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


_MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_AIGC_FIELDS = {
    "Label", "ContentProducer", "ProduceID", "ReservedCode1",
    "ContentPropagator", "PropagateID", "ReservedCode2",
}


class DemoError(RuntimeError):
    """可直接展示给演示人员的预期错误。"""


@dataclass(frozen=True)
class DemoConfig:
    image_path: Path
    output_dir: Path
    existing_image_path: Optional[Path] = None
    base_url: str = "http://127.0.0.1:8000"
    producer: str = "ORG_DEMO_001"
    label: str = "1"
    replacement_label: str = "2"
    reserved_code1: str = ""
    reserved_code2: str = ""
    operator_label: str = "演示操作人"
    request_timeout_seconds: float = 30.0
    poll_timeout_seconds: float = 60.0
    poll_interval_seconds: float = 0.5


@dataclass(frozen=True)
class DemoResult:
    produce_id: str
    label_job_id: str
    labeled_path: Path
    replacement_produce_id: str
    replace_job_id: str
    replaced_path: Path
    repair_plan_id: str
    repair_job_id: str
    repaired_path: Path
    aigc_metadata: dict[str, Any]
    compliance_conclusion: str


FixtureBuilder = Callable[[Path, Any], None]


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise DemoError("服务器返回的不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise DemoError("服务器返回的 JSON 顶层不是对象")
    return payload


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        text = response.text.strip()
        return text[:500] if text else "服务器没有返回可读的错误信息"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return f"{error.get('code', 'UNKNOWN_ERROR')}: {error.get('message', '请求失败')}"
    return json.dumps(payload, ensure_ascii=False)[:500]


def _expect_status(response: httpx.Response, expected: int, action: str) -> dict[str, Any]:
    if response.status_code != expected:
        raise DemoError(
            f"{action}失败（HTTP {response.status_code}）：{_response_error(response)}"
        )
    return _response_payload(response)


def _expect_error(response: httpx.Response, status: int, code: str, action: str) -> None:
    if response.status_code != status:
        raise DemoError(
            f"{action}没有返回预期的 HTTP {status}："
            f"HTTP {response.status_code}，{_response_error(response)}"
        )
    actual = (_response_payload(response).get("error") or {}).get("code")
    if actual != code:
        raise DemoError(f"{action}错误码应为 {code}，实际为 {actual}")


def _safe_output_path(output_dir: Path, server_name: str, unique_id: str) -> Path:
    name = Path(server_name).name or "result-image"
    candidate = output_dir / name
    if not candidate.exists():
        return candidate
    return candidate.with_name(f"{candidate.stem}_{unique_id[-8:]}{candidate.suffix}")


def _validate_source(config: DemoConfig) -> tuple[Path, str, bytes]:
    source = config.image_path.expanduser().resolve()
    if not source.is_file():
        raise DemoError(f"找不到演示图片：{source}")
    mime_type = _MIME_TYPES.get(source.suffix.lower())
    if mime_type is None:
        raise DemoError("演示脚本只接受 .jpg、.jpeg 或 .png 文件")
    if not config.producer.strip():
        raise DemoError("producer 不能为空")
    if not config.operator_label.strip():
        raise DemoError("operator-label 不能为空")
    if config.label not in {"1", "2", "3"}:
        raise DemoError("label 只能是 1、2 或 3")
    if config.replacement_label not in {"1", "2", "3"}:
        raise DemoError("replacement-label 只能是 1、2 或 3")
    return source, mime_type, source.read_bytes()


def _load_optional_existing(config: DemoConfig) -> Optional[tuple[Path, str, bytes]]:
    if config.existing_image_path is None:
        return None
    path = config.existing_image_path.expanduser().resolve()
    if not path.is_file():
        raise DemoError(f"找不到已打标对照图片：{path}")
    mime_type = _MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        raise DemoError("已打标对照图片只接受 .jpg、.jpeg 或 .png 文件")
    return path, mime_type, path.read_bytes()


def _detect(
    client: httpx.Client,
    base_url: str,
    filename: str,
    data: bytes,
    mime_type: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    payload = _expect_status(
        client.post(
            _url(base_url, "/api/detect"),
            files={"file": (filename, data, mime_type)},
            data={"target_regulation": "CN_GB45438"},
        ),
        200,
        "检测图片标识",
    )
    detection = payload.get("detection") or {}
    return detection.get("metadata_compliance") or {}, detection.get("aigc_metadata")


def _generate_id(client: httpx.Client, base_url: str) -> str:
    payload = _expect_status(
        client.post(_url(base_url, "/api/v1/metadata-label-identifiers")),
        201,
        "生成 ProduceID",
    )
    produce_id = str(payload.get("produce_id", ""))
    if not produce_id:
        raise DemoError("后端没有返回 ProduceID")
    return produce_id


def _aigc_document(config: DemoConfig, produce_id: str, label: str) -> dict[str, str]:
    producer = config.producer.strip()
    return {
        "Label": label,
        "ContentProducer": producer,
        "ProduceID": produce_id,
        "ReservedCode1": config.reserved_code1,
        "ContentPropagator": producer,
        "PropagateID": produce_id,
        "ReservedCode2": config.reserved_code2,
    }


def _submit_label(
    client: httpx.Client,
    base_url: str,
    filename: str,
    data: bytes,
    mime_type: str,
    aigc: dict[str, Any],
    policy: str,
) -> httpx.Response:
    request = {
        "standard": "GB45438-2025",
        "modality": "image",
        "existing_metadata_policy": policy,
        "AIGC": aigc,
    }
    return client.post(
        _url(base_url, "/api/v1/metadata-label-jobs"),
        files={
            "file": (filename, data, mime_type),
            "request": (
                "request.json",
                json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                "application/json",
            ),
        },
    )


def _wait_job(
    client: httpx.Client,
    base_url: str,
    path_prefix: str,
    job_id: str,
    config: DemoConfig,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    action: str,
) -> dict[str, Any]:
    deadline = monotonic() + config.poll_timeout_seconds
    last_state: tuple[Any, Any] | None = None
    while True:
        job = _expect_status(
            client.get(_url(base_url, f"{path_prefix}/{job_id}")), 200, action
        )
        state = (job.get("status"), job.get("stage"))
        if state != last_state:
            print(f"      当前状态：{state[0]} / {state[1]}")
            last_state = state
        if job.get("status") == "succeeded":
            return job
        if job.get("status") == "failed":
            raise DemoError(f"{action}失败：" + json.dumps(job.get("error"), ensure_ascii=False))
        if monotonic() >= deadline:
            raise DemoError(f"等待{action}超时")
        sleep(config.poll_interval_seconds)


def _download(
    client: httpx.Client,
    base_url: str,
    output: dict[str, Any],
    output_dir: Path,
    unique_id: str,
) -> tuple[Path, bytes]:
    download_url = output.get("download_url")
    if not download_url:
        raise DemoError("成功任务没有返回结果下载地址")
    response = client.get(_url(base_url, str(download_url)))
    if response.status_code != 200:
        raise DemoError(f"下载结果失败（HTTP {response.status_code}）：{_response_error(response)}")
    data = response.content
    if output.get("sha256") and _sha256(data) != output["sha256"]:
        raise DemoError("下载文件的 SHA-256 与任务记录不一致")
    path = _safe_output_path(output_dir, str(output.get("file_name") or "result-image"), unique_id)
    path.write_bytes(data)
    return path, data


def _validate_label_job(job: dict[str, Any], expected: dict[str, Any], input_hash: str) -> None:
    validation = job.get("validation") or {}
    required = (
        "read_back_succeeded", "schema_valid", "single_aigc_record", "media_integrity_valid"
    )
    failed = [name for name in required if validation.get(name) is not True]
    if failed:
        raise DemoError("打标后的回读验证未全部通过：" + ", ".join(failed))
    if job.get("embedded_metadata", {}).get("AIGC") != expected:
        raise DemoError("任务回读的七字段与提交内容不一致")
    if job.get("input", {}).get("sha256") != input_hash:
        raise DemoError("服务器记录的输入文件哈希与实际上传文件不一致")


def _validate_compliant(
    compliance: dict[str, Any], metadata: Optional[dict[str, Any]], expected: dict[str, Any]
) -> None:
    if compliance.get("conclusion") != "compliant":
        raise DemoError("独立合规结论不是 compliant：" + json.dumps(compliance, ensure_ascii=False))
    if compliance.get("record_count") != 1:
        raise DemoError("图片中不是唯一一份 AIGC 标识")
    if metadata != expected:
        raise DemoError("独立读取的七字段与预期内容不一致")
    if not isinstance(metadata, dict) or set(metadata) != _AIGC_FIELDS:
        raise DemoError("独立读取结果没有且仅有国标规定的七个字段")


def _default_fixture_builder(path: Path, value: Any) -> None:
    """仅用于演示：向独立副本写入指定的 AIGC 测试值。"""
    from app.metadata.exiftool_client import ExifToolClient

    serialized = (
        json.dumps({"AIGC": value}, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, dict)
        else str(value)
    )
    ExifToolClient().write_aigc(
        str(path),
        serialized,
    )


def run_demo(
    config: DemoConfig,
    *,
    client: Optional[httpx.Client] = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    fixture_builder: Optional[FixtureBuilder] = None,
) -> DemoResult:
    """执行检测、打标、替换、不合规检测与修复的完整演示。"""
    source, mime_type, source_bytes = _validate_source(config)
    existing_sample = _load_optional_existing(config)
    source_sha256 = _sha256(source_bytes)
    output_dir = config.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_fixture = fixture_builder or _default_fixture_builder
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=config.request_timeout_seconds, follow_redirects=True
    )
    try:
        print("\n=== JPEG/PNG 文件元数据隐式标识完整流程演示 ===")
        print(f"原始文件：{source}")
        print(f"原始文件 SHA-256：{source_sha256}")

        health = _expect_status(
            active_client.get(_url(config.base_url, "/api/v1/health")), 200, "后端健康检查"
        )
        if health.get("status") != "ok" or not health.get("capabilities", {}).get(mime_type, False):
            raise DemoError(f"后端当前没有启用 {mime_type} 元数据能力")
        print(f"[1/19] 后端连接成功，{mime_type} 与 ExifTool 能力可用")

        if existing_sample is not None:
            existing_path, existing_mime, existing_bytes = existing_sample
            if not health.get("capabilities", {}).get(existing_mime, False):
                raise DemoError(f"后端当前没有启用 {existing_mime} 元数据能力")
            existing_compliance, existing_metadata = _detect(
                active_client,
                config.base_url,
                existing_path.name,
                existing_bytes,
                existing_mime,
            )
            if (
                existing_compliance.get("conclusion") != "compliant"
                or existing_compliance.get("record_count") != 1
                or not isinstance(existing_metadata, dict)
                or set(existing_metadata) != _AIGC_FIELDS
            ):
                raise DemoError(
                    "指定的已打标对照图片没有通过合规检查："
                    + json.dumps(existing_compliance, ensure_ascii=False)
                )
            print("\n--- 已打标图片对照读取 ---")
            print(f"文件：{existing_path}")
            print("检测结论：compliant；标识份数：1；七字段完整：是")
            print(json.dumps(existing_metadata, ensure_ascii=False, indent=2))
            print("--- 对照读取完成，下面处理未打标原图 ---\n")

        before_compliance, before_metadata = _detect(
            active_client, config.base_url, source.name, source_bytes, mime_type
        )
        if (
            before_compliance.get("conclusion") != "not_found"
            or before_compliance.get("record_count") != 0
            or before_metadata is not None
        ):
            raise DemoError(
                "输入图片不是未打标原图；检测结论为 "
                f"{before_compliance.get('conclusion')}，请更换图片"
            )
        print("[2/19] 打标前检测：not_found，标识份数为 0，应进入普通打标流程")

        produce_id = _generate_id(active_client, config.base_url)
        aigc = _aigc_document(config, produce_id, config.label)
        accepted = _expect_status(
            _submit_label(
                active_client, config.base_url, source.name, source_bytes, mime_type, aigc, "reject"
            ),
            202,
            "提交首次打标任务",
        )
        label_job_id = str(accepted.get("job_id", ""))
        if not label_job_id:
            raise DemoError("后端没有返回首次打标任务编号")
        print(f"[3/19] 已生成 ProduceID 并提交异步打标任务：{label_job_id}")

        label_job = _wait_job(
            active_client, config.base_url, "/api/v1/metadata-label-jobs",
            label_job_id, config, sleep, monotonic, "首次打标任务"
        )
        _validate_label_job(label_job, aigc, source_sha256)
        print("[4/19] 首次打标成功：回读、Schema、唯一性和图片完整性均通过")

        labeled_path, labeled_bytes = _download(
            active_client, config.base_url, label_job.get("output") or {}, output_dir, label_job_id
        )
        print(f"[5/19] 已下载打标结果且未覆盖原图：{labeled_path}")

        labeled_compliance, labeled_metadata = _detect(
            active_client, config.base_url, labeled_path.name, labeled_bytes, mime_type
        )
        _validate_compliant(labeled_compliance, labeled_metadata, aigc)
        print("[6/19] 提取与合规检测通过：唯一一份标识、七字段完整、结论 compliant")

        duplicate = _submit_label(
            active_client, config.base_url, labeled_path.name, labeled_bytes, mime_type, aigc, "reject"
        )
        _expect_error(duplicate, 409, "AIGC_METADATA_EXISTS", "已有标识时的重复写入拦截")
        print("[7/19] 重复写入已安全拦截：AIGC_METADATA_EXISTS")

        replacement_produce_id = _generate_id(active_client, config.base_url)
        replacement_aigc = _aigc_document(config, replacement_produce_id, config.replacement_label)
        replace_accepted = _expect_status(
            _submit_label(
                active_client, config.base_url, labeled_path.name, labeled_bytes,
                mime_type, replacement_aigc, "replace"
            ),
            202,
            "提交明确替换任务",
        )
        replace_job_id = str(replace_accepted.get("job_id", ""))
        if not replace_job_id:
            raise DemoError("后端没有返回替换任务编号")
        print(f"[8/19] 模拟用户明确选择 replace，已提交替换任务：{replace_job_id}")

        replace_job = _wait_job(
            active_client, config.base_url, "/api/v1/metadata-label-jobs",
            replace_job_id, config, sleep, monotonic, "替换任务"
        )
        _validate_label_job(replace_job, replacement_aigc, _sha256(labeled_bytes))
        replaced_path, replaced_bytes = _download(
            active_client, config.base_url, replace_job.get("output") or {}, output_dir, replace_job_id
        )
        print(f"[9/19] 整体替换成功并下载新结果：{replaced_path}")

        replaced_compliance, replaced_metadata = _detect(
            active_client, config.base_url, replaced_path.name, replaced_bytes, mime_type
        )
        _validate_compliant(replaced_compliance, replaced_metadata, replacement_aigc)
        if replacement_produce_id == produce_id:
            raise DemoError("替换流程没有生成新的 ProduceID")
        print("[10/19] 替换后复检通过：旧标识被整体替换，文件中仍只有一份新标识")

        fixture_path = _safe_output_path(
            output_dir, f"{replaced_path.stem}_noncompliant_demo{replaced_path.suffix}", replace_job_id
        )
        shutil.copyfile(replaced_path, fixture_path)
        incomplete_aigc = {
            key: value for key, value in replacement_aigc.items()
            if key not in {"ReservedCode1", "ReservedCode2"}
        }
        build_fixture(fixture_path, incomplete_aigc)
        fixture_bytes = fixture_path.read_bytes()
        print(
            "[11/19] 已在独立副本上构造演示样本：故意缺少 ReservedCode1/2，"
            "不会修改原图和正常结果"
        )

        invalid_compliance, _ = _detect(
            active_client, config.base_url, fixture_path.name, fixture_bytes, mime_type
        )
        if invalid_compliance.get("conclusion") != "noncompliant":
            raise DemoError("演示样本没有被正确判定为 noncompliant")
        if "AIGC_SCHEMA_INVALID" not in invalid_compliance.get("reason_codes", []):
            raise DemoError("不合规检测没有指出七字段 Schema 错误")
        print("[12/19] 不合规检测通过：结论 noncompliant，原因 AIGC_SCHEMA_INVALID")

        plan = _expect_status(
            active_client.post(
                _url(config.base_url, "/api/v1/metadata-repair-plans"),
                files={"file": (fixture_path.name, fixture_bytes, mime_type)},
            ),
            201,
            "生成修复计划",
        )
        repair_plan = plan.get("repair_plan") or {}
        if (
            plan.get("inspection", {}).get("conclusion") != "noncompliant"
            or repair_plan.get("repairability") != "confirmable"
            or repair_plan.get("executable") is not True
        ):
            raise DemoError("修复计划不是可确认执行的安全修复：" + json.dumps(repair_plan, ensure_ascii=False))
        repair_plan_id = str(plan.get("plan_id", ""))
        plan_hash = str(plan.get("plan_hash", ""))
        if not repair_plan_id or not plan_hash:
            raise DemoError("修复计划缺少 plan_id 或 plan_hash")
        print(f"[13/19] 已生成安全修复计划：{repair_plan_id}（补全两个保留字段）")

        repair_accepted = _expect_status(
            active_client.post(
                _url(config.base_url, "/api/v1/metadata-repair-jobs"),
                json={
                    "plan_id": repair_plan_id,
                    "plan_hash": plan_hash,
                    "confirmed": True,
                    "operator_label": config.operator_label.strip(),
                },
            ),
            202,
            "确认并创建修复任务",
        )
        repair_job_id = str(repair_accepted.get("job_id", ""))
        if not repair_job_id:
            raise DemoError("后端没有返回修复任务编号")
        print(f"[14/19] 已记录人工确认并提交异步修复任务：{repair_job_id}")

        repair_job = _wait_job(
            active_client, config.base_url, "/api/v1/metadata-repair-jobs",
            repair_job_id, config, sleep, monotonic, "修复任务"
        )
        repair_validation = repair_job.get("validation") or {}
        if repair_validation.get("post_repair_conclusion") != "compliant":
            raise DemoError("修复任务完成，但修复后结论不是 compliant")
        if repair_validation.get("pixel_sha256_unchanged") is not True:
            raise DemoError("修复任务未确认图片像素保持不变")
        repaired_path, repaired_bytes = _download(
            active_client, config.base_url, repair_job.get("output") or {}, output_dir, repair_job_id
        )
        print(f"[15/19] 修复完成：像素未改变，结果已下载至 {repaired_path}")

        repaired_compliance, repaired_metadata = _detect(
            active_client, config.base_url, repaired_path.name, repaired_bytes, mime_type
        )
        expected_repaired = {**replacement_aigc, "ReservedCode1": "", "ReservedCode2": ""}
        _validate_compliant(repaired_compliance, repaired_metadata, expected_repaired)
        if _sha256(source.read_bytes()) != source_sha256:
            raise DemoError("本地原图在演示过程中发生了变化")
        print("[16/19] 修复后复检通过：唯一一份标识、七字段完整、结论 compliant")

        broken_path = _safe_output_path(
            output_dir,
            f"{replaced_path.stem}_broken_json_demo{replaced_path.suffix}",
            repair_job_id,
        )
        shutil.copyfile(replaced_path, broken_path)
        build_fixture(broken_path, "{broken-json")
        broken_bytes = broken_path.read_bytes()
        print("[17/19] 已在另一独立副本上构造损坏 JSON，用于验证禁止盲目修复")

        broken_compliance, _ = _detect(
            active_client, config.base_url, broken_path.name, broken_bytes, mime_type
        )
        if broken_compliance.get("conclusion") != "noncompliant":
            raise DemoError("损坏 JSON 样本没有被正确判定为 noncompliant")
        if "AIGC_JSON_INVALID" not in broken_compliance.get("reason_codes", []):
            raise DemoError("损坏 JSON 检测没有返回 AIGC_JSON_INVALID")
        print("[18/19] 损坏 JSON 已检出：AIGC_JSON_INVALID")

        blocked_plan = _expect_status(
            active_client.post(
                _url(config.base_url, "/api/v1/metadata-repair-plans"),
                files={"file": (broken_path.name, broken_bytes, mime_type)},
            ),
            201,
            "生成损坏 JSON 的修复评估",
        )
        blocked_repair = blocked_plan.get("repair_plan") or {}
        if (
            blocked_repair.get("repairability") != "manual_review"
            or blocked_repair.get("executable") is not False
            or not blocked_repair.get("blocking_reasons")
        ):
            raise DemoError(
                "损坏 JSON 没有被正确阻止自动修复："
                + json.dumps(blocked_repair, ensure_ascii=False)
            )
        print("[19/19] 安全边界通过：损坏 JSON 只能 manual_review，系统没有猜测或执行修复")

        print("\n最终读取出的 AIGC 七字段：")
        print(json.dumps(repaired_metadata, ensure_ascii=False, indent=2))
        print("\n完整演示结论：")
        print("- 未标识检测：not_found / 0 份")
        print("- 首次打标：成功")
        print("- 标识提取：成功，七字段完整")
        print("- 国标文件元数据结构检测：compliant")
        print("- 重复写入保护：已拦截")
        print("- 明确整体替换：成功，仍仅 1 份标识")
        print("- 不合规标识检测：noncompliant")
        print("- 安全修复及复检：成功，compliant")
        print("- 损坏 JSON 安全边界：manual_review，禁止自动修复")
        print("- 图片像素完整性：通过")
        print("- 原始文件未被覆盖：是")
        print(
            "- ExifTool 交叉读取："
            f"{labeled_compliance.get('cross_reader', {}).get('status', '未返回')}"
        )
        print(
            "- 编号来源核验："
            f"{labeled_compliance.get('source_verification', {}).get('status', '未返回')}"
        )
        print(
            "- C2PA 存在性检查："
            f"{labeled_compliance.get('c2pa_presence', {}).get('status', '未返回')}"
            "（当前只检查存在性，不代表签名验证）"
        )
        print("=== 完整演示完成 ===\n")

        return DemoResult(
            produce_id=produce_id,
            label_job_id=label_job_id,
            labeled_path=labeled_path,
            replacement_produce_id=replacement_produce_id,
            replace_job_id=replace_job_id,
            replaced_path=replaced_path,
            repair_plan_id=repair_plan_id,
            repair_job_id=repair_job_id,
            repaired_path=repaired_path,
            aigc_metadata=repaired_metadata or {},
            compliance_conclusion=str(repaired_compliance["conclusion"]),
        )
    finally:
        if owns_client:
            active_client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "演示 JPEG/PNG 的检测、七字段打标、提取、唯一性校验、"
            "重复拦截、整体替换和安全修复"
        ),
    )
    parser.add_argument("image", type=Path, help="未写入 AIGC 标识的 JPEG/PNG 原图")
    parser.add_argument(
        "--existing-image",
        type=Path,
        default=None,
        help="可选：先提取并检查一张已经打标的 JPEG/PNG 对照图片",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="结果目录；默认在原图旁创建 demo-output")
    parser.add_argument("--producer", default="ORG_DEMO_001", help="内容生产者标识")
    parser.add_argument("--label", choices=("1", "2", "3"), default="1")
    parser.add_argument("--replacement-label", choices=("1", "2", "3"), default="2")
    parser.add_argument("--reserved-code1", default="")
    parser.add_argument("--reserved-code2", default="")
    parser.add_argument("--operator-label", default="演示操作人", help="修复确认操作人")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端服务地址")
    parser.add_argument("--poll-timeout", type=float, default=60.0, help="等待异步任务的最长秒数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    image_path = args.image.expanduser()
    output_dir = args.output_dir or image_path.parent / "demo-output"
    config = DemoConfig(
        image_path=image_path,
        output_dir=output_dir,
        existing_image_path=args.existing_image,
        base_url=args.base_url,
        producer=args.producer,
        label=args.label,
        replacement_label=args.replacement_label,
        reserved_code1=args.reserved_code1,
        reserved_code2=args.reserved_code2,
        operator_label=args.operator_label,
        poll_timeout_seconds=args.poll_timeout,
    )
    try:
        run_demo(config)
        return 0
    except DemoError as exc:
        print(f"\n[演示失败] {exc}\n", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(
            "\n[演示失败] 无法连接后端服务："
            f"{exc}\n请先启动 uvicorn，并确认 --base-url 正确。\n",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\n演示已由用户中止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
