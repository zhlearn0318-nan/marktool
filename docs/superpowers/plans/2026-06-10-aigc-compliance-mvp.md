# AIGC 标识合规平台 MVP 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端跑通「上传一张图片 → 检测它是否按 GB 45438-2025 标注了 AIGC 标识 → 输出四部分可视化报告」的可演示原型。

**Architecture:** FastAPI 同步后端 + 插件化检测引擎（注册表 + Detector 基类）+ 四部分报告构建器 + React/Ant Design 前端。隐式元数据检测链路真实可跑（仅依赖 Pillow + jsonschema）；OCR、水印、AI 检测走统一插件接口，先占位。

**Tech Stack:** Python 3.11 / FastAPI / Pillow / jsonschema / pytest ；Vite / React / TypeScript / Ant Design。

---

## 文件结构

```
backend/
  app/
    __init__.py
    main.py                       # FastAPI 实例 + CORS + 路由挂载
    api/__init__.py
    api/routes.py                 # /api/health /api/detect /api/report/{id}
    engine/__init__.py
    engine/base.py                # Detector ABC, DetectionContext
    engine/registry.py            # DetectorRegistry + build_default_registry
    engine/detectors/__init__.py
    engine/detectors/metadata_detector.py      # 真实
    engine/detectors/explicit_mark_detector.py # 占位 + 几何/关键词逻辑(真实)
    engine/detectors/watermark_detector.py     # 占位
    metadata/__init__.py
    metadata/xmp_reader.py        # Pillow 读 XMP + 提取 AIGC JSON
    schemas/__init__.py
    schemas/models.py             # Pydantic 模型 + 枚举
    schemas/gb45438_appendix_e.json  # JSON Schema(draft-07)
    schemas/validation.py         # validate_aigc_json
    report/__init__.py
    report/compliance_rules.py    # 评级规则
    report/builder.py             # 组装四部分报告
    storage.py                    # 临时文件 + 内存结果存储
  tests/
    __init__.py
    fixtures.py                   # 生成测试图片(含/不含/缺字段/格式错 XMP)
    test_xmp_reader.py
    test_schema_validation.py
    test_metadata_detector.py
    test_explicit_logic.py
    test_compliance_rules.py
    test_api.py
  requirements.txt
frontend/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/main.tsx
  src/App.tsx
  src/api.ts
  src/types.ts
  src/pages/UploadPage.tsx
  src/pages/ReportPage.tsx
  src/components/CheckList.tsx
  src/components/ComplianceRadar.tsx
  src/components/ProvenanceChain.tsx
README.md
```

**约定（贯穿全计划）：**
- 隐式标识 AIGC JSON 以 UTF-8 JSON 字符串嵌入 XMP，命名空间 `http://aigc-compliance/ns/1.0/`，元素 `<aigc:metadata>...</aigc:metadata>`（JSON 经 XML 转义）。读取端用正则从原始 XMP 提取后 `json.loads`。该约定让 PNG/JPEG 读写对称、纯 Python 可跑。
- 所有后端命令在 `backend/` 目录下运行。

---

## Task 0: 项目脚手架与 git 初始化

**Files:**
- Create: `README.md`
- Create: `backend/requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: 初始化 git 仓库**

当前目录不是 git 仓库。在项目根 `E:\陶气的标识` 执行：
```bash
git init
git config user.name "Claude" 2>/dev/null; true
```

- [ ] **Step 2: 写 `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
venv/
node_modules/
dist/
.pytest_cache/
*.egg-info/
```

- [ ] **Step 3: 写 `backend/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
python-multipart==0.0.9
pillow==11.3.0
jsonschema==4.23.0
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 4: 创建虚拟环境并安装**

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```
Expected: 安装成功，无报错。

- [ ] **Step 5: 写最小 `README.md`**

```markdown
# AIGC 标识合规平台（MVP）

图片模态的 AIGC 标识合规**检测 + 报告**原型。覆盖 GB 45438-2025 隐式元数据标识的真实检测；OCR/水印/AI 检测为可插拔占位。

## 后端
    cd backend
    .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

## 前端
    cd frontend
    npm install && npm run dev

## 测试
    cd backend && .venv/Scripts/python -m pytest -v

> 说明：GB 45438-2025 附录 E 的 AIGC JSON 结构按公开信息近似定义，待对照标准原文校准。
```

- [ ] **Step 6: 提交**

```bash
git add .gitignore backend/requirements.txt README.md
git commit -m "chore: scaffold project, git init, requirements"
```

---

## Task 1: Pydantic 模型与枚举

**Files:**
- Create: `backend/app/__init__.py`（空）
- Create: `backend/app/schemas/__init__.py`（空）
- Create: `backend/app/schemas/models.py`
- Create: `backend/tests/__init__.py`（空）
- Test: `backend/tests/test_models_smoke.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_models_smoke.py`**

```python
from app.schemas.models import CheckItem, CheckStatus, MarkType, Report, ComplianceReport, TamperReport, AIDetectionReport


def test_checkitem_roundtrip():
    item = CheckItem(
        id="metadata.presence",
        title="隐式标识(元数据)",
        status=CheckStatus.PASS,
        detail="ok",
        mark_type=MarkType.IMPLICIT_METADATA,
    )
    assert item.model_dump()["status"] == "pass"


def test_report_builds():
    rep = Report(
        provenance=[],
        compliance=ComplianceReport(items=[], rating="A", suggestions=[]),
        tamper=TamperReport(consistent=None, findings=[]),
        ai_detection=AIDetectionReport(enabled=False, probability=None, note="未启用"),
    )
    assert rep.model_dump()["compliance"]["rating"] == "A"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_models_smoke.py -v`
Expected: FAIL（ModuleNotFoundError: app.schemas.models）。

- [ ] **Step 3: 写 `backend/app/schemas/models.py`**

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class MarkType(str, Enum):
    IMPLICIT_METADATA = "implicit_metadata"
    IMPLICIT_WATERMARK = "implicit_watermark"
    EXPLICIT_TEXT = "explicit_text"


class CheckItem(BaseModel):
    id: str
    title: str
    status: CheckStatus
    detail: str
    suggestion: Optional[str] = None
    mark_type: MarkType


class ProvenanceNode(BaseModel):
    role: str
    name: str
    id: Optional[str] = None


class ComplianceReport(BaseModel):
    items: list[CheckItem]
    rating: str
    suggestions: list[str]


class TamperReport(BaseModel):
    consistent: Optional[bool]
    findings: list[str]


class AIDetectionReport(BaseModel):
    enabled: bool = False
    probability: Optional[float] = None
    note: str


class Report(BaseModel):
    provenance: list[ProvenanceNode]
    compliance: ComplianceReport
    tamper: TamperReport
    ai_detection: AIDetectionReport


class DetectionResult(BaseModel):
    result_id: str
    filename: str
    modality: str
    items: list[CheckItem]
    aigc_metadata: Optional[dict] = None
```

Also create empty `backend/app/__init__.py`, `backend/app/schemas/__init__.py`, `backend/tests/__init__.py`.
Create `backend/pytest.ini`:
```ini
[pytest]
pythonpath = .
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_models_smoke.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/__init__.py backend/app/schemas/ backend/tests/__init__.py backend/tests/test_models_smoke.py backend/pytest.ini
git commit -m "feat: add pydantic models and enums"
```

---

## Task 2: GB 45438 附录 E JSON Schema 与校验

**Files:**
- Create: `backend/app/schemas/gb45438_appendix_e.json`
- Create: `backend/app/schemas/validation.py`
- Test: `backend/tests/test_schema_validation.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_schema_validation.py`**

```python
from app.schemas.validation import validate_aigc_json


def test_valid_metadata_passes():
    data = {"Label": "1", "ContentProducer": "ACME", "ProduceID": "P-001"}
    assert validate_aigc_json(data) == []


def test_missing_required_field_reports_error():
    data = {"Label": "1", "ContentProducer": "ACME"}  # 缺 ProduceID
    errors = validate_aigc_json(data)
    assert any("ProduceID" in e for e in errors)


def test_empty_object_reports_all_required():
    errors = validate_aigc_json({})
    assert len(errors) >= 3
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_schema_validation.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 写 `backend/app/schemas/gb45438_appendix_e.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GB45438-2025 Appendix E AIGC Metadata (approximate)",
  "type": "object",
  "required": ["Label", "ContentProducer", "ProduceID"],
  "properties": {
    "Label": { "type": "string", "minLength": 1 },
    "ContentProducer": { "type": "string", "minLength": 1 },
    "ProduceID": { "type": "string", "minLength": 1 },
    "ContentPropagator": { "type": "string" },
    "PropagateID": { "type": "string" },
    "ReservedCode1": { "type": "string" },
    "ReservedCode2": { "type": "string" }
  },
  "additionalProperties": true
}
```

- [ ] **Step 4: 写 `backend/app/schemas/validation.py`**

```python
import json
from pathlib import Path
from jsonschema import Draft7Validator

_SCHEMA_PATH = Path(__file__).with_name("gb45438_appendix_e.json")
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft7Validator(_SCHEMA)


def validate_aigc_json(data: dict) -> list[str]:
    """返回错误消息列表；空列表表示通过附录E结构校验。"""
    errors = []
    for err in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or (
            err.message.split("'")[1] if "'" in err.message else "root"
        )
        errors.append(f"{field}: {err.message}")
    return errors
```

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_schema_validation.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas/gb45438_appendix_e.json backend/app/schemas/validation.py backend/tests/test_schema_validation.py
git commit -m "feat: add GB45438 appendix E schema and validator"
```

---

## Task 3: 测试夹具（生成内嵌 XMP 的图片）

**Files:**
- Create: `backend/tests/fixtures.py`
- Test: `backend/tests/test_fixtures_smoke.py`

- [ ] **Step 1: 写 `backend/tests/fixtures.py`**

```python
import html
import json
from PIL import Image, PngImagePlugin

AIGC_NS = "http://aigc-compliance/ns/1.0/"

_XMP_TEMPLATE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:aigc="%s">\n'
    "   <aigc:metadata>%s</aigc:metadata>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)

VALID_AIGC = {
    "Label": "1",
    "ContentProducer": "示例生成服务商",
    "ProduceID": "PRD-20260610-0001",
    "ContentPropagator": "示例传播平台",
    "PropagateID": "PRO-20260610-0009",
}

MISSING_FIELD_AIGC = {"Label": "1", "ContentProducer": "示例生成服务商"}  # 缺 ProduceID


def _xmp_for(aigc_dict: dict) -> str:
    body = html.escape(json.dumps(aigc_dict, ensure_ascii=False))
    return _XMP_TEMPLATE % (AIGC_NS, body)


def make_png(path, aigc_dict=None, size=(400, 300), raw_xmp=None):
    """生成 PNG。aigc_dict 非空则嵌入合规结构的 XMP；raw_xmp 用于注入任意(含损坏)XMP。"""
    img = Image.new("RGB", size, (180, 180, 180))
    xmp = raw_xmp if raw_xmp is not None else (_xmp_for(aigc_dict) if aigc_dict else None)
    if xmp is not None:
        meta = PngImagePlugin.PngInfo()
        meta.add_itxt("XML:com.adobe.xmp", xmp)
        img.save(path, "PNG", pnginfo=meta)
    else:
        img.save(path, "PNG")
    return str(path)


def broken_json_xmp() -> str:
    """合法 XMP 包裹但 JSON 本身损坏。"""
    return _XMP_TEMPLATE % (AIGC_NS, html.escape('{"Label": "1", '))
```

- [ ] **Step 2: 写冒烟测试 `backend/tests/test_fixtures_smoke.py`**

```python
from PIL import Image
from tests.fixtures import make_png, VALID_AIGC


def test_make_png_embeds_xmp(tmp_path):
    p = make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC)
    img = Image.open(p)
    img.load()
    assert "XML:com.adobe.xmp" in img.info
    assert "aigc:metadata" in img.info["XML:com.adobe.xmp"]


def test_make_png_without_metadata(tmp_path):
    p = make_png(tmp_path / "b.png", aigc_dict=None)
    img = Image.open(p)
    img.load()
    assert "XML:com.adobe.xmp" not in img.info
```

- [ ] **Step 3: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_fixtures_smoke.py -v`
Expected: PASS（2 passed）。若 itxt 键名不符，检查 Pillow 是否将关键字原样存入 `img.info`。

- [ ] **Step 4: 提交**

```bash
git add backend/tests/fixtures.py backend/tests/test_fixtures_smoke.py
git commit -m "test: add image fixtures with embedded XMP"
```

---

## Task 4: XMP 读取与 AIGC JSON 提取

**Files:**
- Create: `backend/app/metadata/__init__.py`（空）
- Create: `backend/app/metadata/xmp_reader.py`
- Test: `backend/tests/test_xmp_reader.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_xmp_reader.py`**

```python
from PIL import Image
from app.metadata.xmp_reader import extract_aigc_json
from tests.fixtures import make_png, broken_json_xmp, VALID_AIGC


def _open(path):
    img = Image.open(path)
    img.load()
    return img


def test_extracts_valid_aigc_json(tmp_path):
    p = make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC)
    data = extract_aigc_json(_open(p))
    assert data["ProduceID"] == "PRD-20260610-0001"


def test_returns_none_without_metadata(tmp_path):
    p = make_png(tmp_path / "b.png", aigc_dict=None)
    assert extract_aigc_json(_open(p)) is None


def test_broken_json_returns_empty_dict(tmp_path):
    p = make_png(tmp_path / "c.png", raw_xmp=broken_json_xmp())
    assert extract_aigc_json(_open(p)) == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_xmp_reader.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 写 `backend/app/metadata/xmp_reader.py`**

```python
import html
import json
import re
from typing import Optional
from PIL import Image

_AIGC_RE = re.compile(r"<aigc:metadata>(.*?)</aigc:metadata>", re.DOTALL)
_XMP_APP1_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"


def _raw_xmp(image: Image.Image) -> Optional[str]:
    # PNG: iTXt 关键字 "XML:com.adobe.xmp" 进入 image.info
    xmp = image.info.get("XML:com.adobe.xmp")
    if xmp:
        return xmp.decode("utf-8", "ignore") if isinstance(xmp, (bytes, bytearray)) else xmp
    # JPEG: XMP 在 APP1 段
    applist = getattr(image, "applist", None)
    if applist:
        for marker, content in applist:
            if marker == "APP1" and content.startswith(_XMP_APP1_PREFIX):
                return content[len(_XMP_APP1_PREFIX):].decode("utf-8", "ignore")
    return None


def extract_aigc_json(image: Image.Image) -> Optional[dict]:
    """返回 AIGC JSON dict；无 XMP/无该元素返回 None；JSON 损坏返回 {}。"""
    raw = _raw_xmp(image)
    if not raw:
        return None
    m = _AIGC_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return {}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_xmp_reader.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/metadata/ backend/tests/test_xmp_reader.py
git commit -m "feat: add XMP reader and AIGC JSON extraction"
```

---

## Task 5: 检测引擎基类与注册表

**Files:**
- Create: `backend/app/engine/__init__.py`（空）
- Create: `backend/app/engine/base.py`
- Create: `backend/app/engine/registry.py`
- Test: `backend/tests/test_registry.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_registry.py`**

```python
from app.engine.base import Detector, DetectionContext
from app.engine.registry import DetectorRegistry
from app.schemas.models import CheckItem, CheckStatus, MarkType


class _OK(Detector):
    name = "ok"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def applicable(self, ctx):
        return True

    def detect(self, ctx):
        return [CheckItem(id="ok.1", title="ok", status=CheckStatus.PASS,
                          detail="d", mark_type=self.mark_type)]


class _Boom(Detector):
    name = "boom"
    modality = "image"
    mark_type = MarkType.EXPLICIT_TEXT

    def applicable(self, ctx):
        return True

    def detect(self, ctx):
        raise RuntimeError("kaboom")


def test_runs_matching_modality_and_collects_items():
    reg = DetectorRegistry()
    reg.register(_OK())
    ctx = DetectionContext(file_path="x", image=None)
    items = reg.run_all(ctx, modality="image")
    assert [i.id for i in items] == ["ok.1"]


def test_detector_exception_becomes_warn_item():
    reg = DetectorRegistry()
    reg.register(_Boom())
    ctx = DetectionContext(file_path="x", image=None)
    items = reg.run_all(ctx, modality="image")
    assert items[0].status == CheckStatus.WARN
    assert "kaboom" in items[0].detail


def test_skips_other_modality():
    reg = DetectorRegistry()
    d = _OK()
    d.modality = "audio"
    reg.register(d)
    ctx = DetectionContext(file_path="x", image=None)
    assert reg.run_all(ctx, modality="image") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_registry.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 写 `backend/app/engine/base.py`**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from PIL import Image
from app.schemas.models import CheckItem, MarkType


@dataclass
class DetectionContext:
    file_path: str
    image: Optional[Image.Image]
    target_regulation: str = "CN_GB45438"
    cache: dict[str, Any] = field(default_factory=dict)


class Detector(ABC):
    name: str = "base"
    modality: str = "image"
    mark_type: MarkType = MarkType.IMPLICIT_METADATA

    @abstractmethod
    def applicable(self, ctx: DetectionContext) -> bool:
        ...

    @abstractmethod
    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        ...
```

- [ ] **Step 4: 写 `backend/app/engine/registry.py`**

```python
from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus


class DetectorRegistry:
    def __init__(self):
        self._detectors: list[Detector] = []

    def register(self, detector: Detector) -> Detector:
        self._detectors.append(detector)
        return detector

    def run_all(self, ctx: DetectionContext, modality: str) -> list[CheckItem]:
        items: list[CheckItem] = []
        for d in self._detectors:
            if d.modality != modality or not d.applicable(ctx):
                continue
            try:
                items.extend(d.detect(ctx))
            except Exception as exc:  # 单个检测器失败不应中断整体
                items.append(CheckItem(
                    id=f"{d.name}.error",
                    title=f"{d.name} 检测异常",
                    status=CheckStatus.WARN,
                    detail=str(exc),
                    suggestion="检查该检测器输入或实现",
                    mark_type=d.mark_type,
                ))
        return items


def build_default_registry() -> DetectorRegistry:
    """延迟导入避免循环依赖；注册图片模态的三个检测器。"""
    from app.engine.detectors.metadata_detector import MetadataDetector
    from app.engine.detectors.explicit_mark_detector import ExplicitMarkDetector
    from app.engine.detectors.watermark_detector import WatermarkDetector

    reg = DetectorRegistry()
    reg.register(MetadataDetector())
    reg.register(ExplicitMarkDetector())
    reg.register(WatermarkDetector())
    return reg
```

> 注：`build_default_registry` 在本任务可写入，但其引用的 detector 模块在 Task 6/7 创建。本任务测试不调用 `build_default_registry`，故 Task 5 测试可独立通过。

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_registry.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/engine/__init__.py backend/app/engine/base.py backend/app/engine/registry.py backend/tests/test_registry.py
git commit -m "feat: add detector base class and registry"
```

---

## Task 6: MetadataDetector（真实）与占位检测器

**Files:**
- Create: `backend/app/engine/detectors/__init__.py`（空）
- Create: `backend/app/engine/detectors/metadata_detector.py`
- Create: `backend/app/engine/detectors/explicit_mark_detector.py`
- Create: `backend/app/engine/detectors/watermark_detector.py`
- Test: `backend/tests/test_metadata_detector.py`
- Test: `backend/tests/test_explicit_logic.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_metadata_detector.py`**

```python
from PIL import Image
from app.engine.base import DetectionContext
from app.engine.detectors.metadata_detector import MetadataDetector
from app.schemas.models import CheckStatus
from tests.fixtures import make_png, broken_json_xmp, VALID_AIGC, MISSING_FIELD_AIGC


def _ctx(path):
    img = Image.open(path)
    img.load()
    return DetectionContext(file_path=str(path), image=img)


def test_valid_metadata_passes(tmp_path):
    ctx = _ctx(make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.PASS
    assert ctx.cache["aigc_metadata"]["ProduceID"] == "PRD-20260610-0001"


def test_no_metadata_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "b.png", aigc_dict=None))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.FAIL


def test_missing_field_is_warn(tmp_path):
    ctx = _ctx(make_png(tmp_path / "c.png", aigc_dict=MISSING_FIELD_AIGC))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.WARN
    assert "ProduceID" in items[0].detail


def test_broken_json_is_warn(tmp_path):
    ctx = _ctx(make_png(tmp_path / "d.png", raw_xmp=broken_json_xmp()))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.WARN
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_metadata_detector.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 写 `backend/app/engine/detectors/metadata_detector.py`**

```python
from app.engine.base import Detector, DetectionContext
from app.metadata.xmp_reader import extract_aigc_json
from app.schemas.models import CheckItem, CheckStatus, MarkType
from app.schemas.validation import validate_aigc_json


class MetadataDetector(Detector):
    name = "metadata"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        aigc = extract_aigc_json(ctx.image)
        ctx.cache["aigc_metadata"] = aigc
        if aigc is None:
            return [CheckItem(
                id="metadata.presence",
                title="隐式标识(元数据)",
                status=CheckStatus.FAIL,
                detail="未在文件元数据(XMP)中检出 AIGC 标识 JSON",
                suggestion="按 GB 45438 附录E 在 XMP 中写入 AIGC 标识信息(Label/ContentProducer/ProduceID 等)",
                mark_type=self.mark_type,
            )]
        errors = validate_aigc_json(aigc)
        if errors:
            joined = "; ".join(errors)
            return [CheckItem(
                id="metadata.schema",
                title="隐式标识(元数据)格式校验",
                status=CheckStatus.WARN,
                detail=f"检出 AIGC JSON 但不符合附录E结构: {joined}",
                suggestion=f"修正以下问题: {joined}",
                mark_type=self.mark_type,
            )]
        return [CheckItem(
            id="metadata.schema",
            title="隐式标识(元数据)",
            status=CheckStatus.PASS,
            detail="检出并通过附录E结构校验的 AIGC 标识",
            mark_type=self.mark_type,
        )]
```

- [ ] **Step 4: 写 `backend/app/engine/detectors/explicit_mark_detector.py`**

```python
from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus, MarkType

_KW_TECH = ("ai", "人工智能")
_KW_GEN = ("生成", "合成")


def evaluate_explicit_mark(text: str, box: tuple, image_size: tuple) -> dict:
    """对一条 OCR 文字结果做真实判定（关键词 + 字高≥最短边5% + 位于边角）。
    box=(x1,y1,x2,y2)，image_size=(w,h)。供未来 OCR 插件复用。"""
    lower = text.lower()
    has_kw = any(k in lower for k in _KW_TECH) and any(k in text for k in _KW_GEN)
    w, h = image_size
    char_h = box[3] - box[1]
    height_ok = char_h >= 0.05 * min(w, h)
    in_corner = (box[0] <= 0.25 * w or box[2] >= 0.75 * w) and \
                (box[1] <= 0.25 * h or box[3] >= 0.75 * h)
    return {
        "has_keyword": has_kw,
        "height_ok": height_ok,
        "in_corner": in_corner,
        "compliant": has_kw and height_ok and in_corner,
    }


class ExplicitMarkDetector(Detector):
    name = "explicit_text"
    modality = "image"
    mark_type = MarkType.EXPLICIT_TEXT

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        # OCR 后端为可插拔占位：判定逻辑(evaluate_explicit_mark)已就绪，
        # 接入 PaddleOCR 后将其输出逐条喂入即可。
        return [CheckItem(
            id="explicit.ocr",
            title="显式标识(边角文字)",
            status=CheckStatus.WARN,
            detail="显式文字标识检测需 OCR 插件(PaddleOCR)，当前未启用",
            suggestion="启用 OCR 插件以检测边角『AI/人工智能 + 生成/合成』文字及字高是否≥最短边5%",
            mark_type=self.mark_type,
        )]
```

- [ ] **Step 5: 写 `backend/app/engine/detectors/watermark_detector.py`**

```python
from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus, MarkType


class WatermarkDetector(Detector):
    name = "watermark"
    modality = "image"
    mark_type = MarkType.IMPLICIT_WATERMARK

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        return [CheckItem(
            id="watermark.trustmark",
            title="隐式标识(水印)",
            status=CheckStatus.WARN,
            detail="像素水印检测需 TrustMark 插件，当前未启用",
            suggestion="启用 TrustMark 插件以提取并校验抗压缩像素水印",
            mark_type=self.mark_type,
        )]
```

- [ ] **Step 6: 写 `backend/tests/test_explicit_logic.py`**

```python
from app.engine.detectors.explicit_mark_detector import evaluate_explicit_mark


def test_compliant_corner_mark():
    # 1000x1000 图，右下角，字高 80(>50)，含关键词
    r = evaluate_explicit_mark("AI生成", (820, 900, 980, 980), (1000, 1000))
    assert r["compliant"] is True


def test_too_small_height_not_compliant():
    r = evaluate_explicit_mark("AI生成", (820, 950, 980, 980), (1000, 1000))  # 字高30<50
    assert r["height_ok"] is False
    assert r["compliant"] is False


def test_no_keyword_not_compliant():
    r = evaluate_explicit_mark("版权所有", (820, 900, 980, 980), (1000, 1000))
    assert r["has_keyword"] is False
    assert r["compliant"] is False
```

- [ ] **Step 7: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_metadata_detector.py tests/test_explicit_logic.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 8: 提交**

```bash
git add backend/app/engine/detectors/ backend/tests/test_metadata_detector.py backend/tests/test_explicit_logic.py
git commit -m "feat: add metadata detector (real) and explicit/watermark placeholders"
```

---

## Task 7: 合规评级规则与报告构建器

**Files:**
- Create: `backend/app/report/__init__.py`（空）
- Create: `backend/app/report/compliance_rules.py`
- Create: `backend/app/report/builder.py`
- Test: `backend/tests/test_compliance_rules.py`

- [ ] **Step 1: 写失败测试 `backend/tests/test_compliance_rules.py`**

```python
from app.report.compliance_rules import evaluate_compliance
from app.report.builder import build_report
from app.schemas.models import CheckItem, CheckStatus, MarkType


def _item(id_, status, mark=MarkType.IMPLICIT_METADATA, suggestion=None):
    return CheckItem(id=id_, title=id_, status=status, detail="d",
                     suggestion=suggestion, mark_type=mark)


def test_no_metadata_item_is_noncompliant():
    rating, _ = evaluate_compliance([])
    assert rating == "不合规"


def test_metadata_fail_is_noncompliant():
    rating, _ = evaluate_compliance([_item("metadata.presence", CheckStatus.FAIL)])
    assert rating == "不合规"


def test_metadata_warn_is_C():
    rating, _ = evaluate_compliance([_item("metadata.schema", CheckStatus.WARN)])
    assert rating == "C"


def test_metadata_pass_with_unverified_others_is_B():
    items = [
        _item("metadata.schema", CheckStatus.PASS),
        _item("explicit.ocr", CheckStatus.WARN, MarkType.EXPLICIT_TEXT),
    ]
    rating, _ = evaluate_compliance(items)
    assert rating == "B"


def test_all_pass_is_A():
    items = [
        _item("metadata.schema", CheckStatus.PASS),
        _item("explicit.ocr", CheckStatus.PASS, MarkType.EXPLICIT_TEXT),
    ]
    rating, _ = evaluate_compliance(items)
    assert rating == "A"


def test_build_report_provenance_from_metadata():
    items = [_item("metadata.schema", CheckStatus.PASS)]
    aigc = {"ContentProducer": "P", "ProduceID": "X1", "ContentPropagator": "Q", "PropagateID": "Y2"}
    rep = build_report(items, aigc)
    roles = [n.role for n in rep.provenance]
    assert roles == ["ContentProducer", "ContentPropagator"]
    assert rep.ai_detection.enabled is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_compliance_rules.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 写 `backend/app/report/compliance_rules.py`**

```python
from app.schemas.models import CheckItem, CheckStatus


def evaluate_compliance(items: list[CheckItem]) -> tuple[str, list[str]]:
    """返回 (评级, 整改建议列表)。评级: A / B / C / 不合规。"""
    suggestions = [i.suggestion for i in items
                   if i.suggestion and i.status != CheckStatus.PASS]
    meta = next((i for i in items if i.id.startswith("metadata")), None)
    if meta is None or meta.status == CheckStatus.FAIL:
        return "不合规", suggestions
    if meta.status == CheckStatus.WARN:
        return "C", suggestions
    # meta PASS
    all_pass = all(i.status == CheckStatus.PASS for i in items)
    return ("A" if all_pass else "B"), suggestions
```

- [ ] **Step 4: 写 `backend/app/report/builder.py`**

```python
from app.report.compliance_rules import evaluate_compliance
from app.schemas.models import (
    AIDetectionReport, CheckItem, CheckStatus, ComplianceReport,
    MarkType, ProvenanceNode, Report, TamperReport,
)


def _provenance(aigc: dict | None) -> list[ProvenanceNode]:
    nodes: list[ProvenanceNode] = []
    if not aigc:
        return nodes
    if aigc.get("ContentProducer"):
        nodes.append(ProvenanceNode(role="ContentProducer",
                                    name=aigc["ContentProducer"],
                                    id=aigc.get("ProduceID")))
    if aigc.get("ContentPropagator"):
        nodes.append(ProvenanceNode(role="ContentPropagator",
                                    name=aigc["ContentPropagator"],
                                    id=aigc.get("PropagateID")))
    return nodes


def _tamper(items: list[CheckItem]) -> TamperReport:
    findings: list[str] = []
    meta = next((i for i in items if i.id.startswith("metadata")), None)
    wm = next((i for i in items if i.mark_type == MarkType.IMPLICIT_WATERMARK), None)
    if meta and meta.status == CheckStatus.PASS and wm and wm.status == CheckStatus.WARN:
        findings.append("水印检测未启用，无法验证元数据与水印一致性")
    return TamperReport(consistent=None, findings=findings)


def build_report(items: list[CheckItem], aigc_metadata: dict | None) -> Report:
    rating, suggestions = evaluate_compliance(items)
    return Report(
        provenance=_provenance(aigc_metadata),
        compliance=ComplianceReport(items=items, rating=rating, suggestions=suggestions),
        tamper=_tamper(items),
        ai_detection=AIDetectionReport(
            enabled=False, probability=None,
            note="AI 内容概率检测为辅助参考，MVP 未启用",
        ),
    )
```

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_compliance_rules.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/report/ backend/tests/test_compliance_rules.py
git commit -m "feat: add compliance rating rules and report builder"
```

---

## Task 8: 存储、FastAPI 应用与路由

**Files:**
- Create: `backend/app/storage.py`
- Create: `backend/app/api/__init__.py`（空）
- Create: `backend/app/api/routes.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: 写 `backend/app/storage.py`**

```python
import os
import tempfile
import uuid

_RESULTS: dict[str, dict] = {}
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "aigc_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_upload(filename: str, data: bytes) -> tuple[str, str]:
    result_id = uuid.uuid4().hex
    ext = os.path.splitext(filename or "")[1] or ".bin"
    path = os.path.join(UPLOAD_DIR, result_id + ext)
    with open(path, "wb") as f:
        f.write(data)
    return result_id, path


def store_result(result_id: str, payload: dict) -> None:
    _RESULTS[result_id] = payload


def get_result(result_id: str) -> dict | None:
    return _RESULTS.get(result_id)
```

- [ ] **Step 2: 写 `backend/app/api/routes.py`**

```python
import io
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app import storage
from app.engine.base import DetectionContext
from app.engine.registry import build_default_registry
from app.report.builder import build_report
from app.schemas.models import DetectionResult

router = APIRouter(prefix="/api")
_registry = build_default_registry()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/detect")
async def detect(file: UploadFile = File(...),
                 target_regulation: str = Form("CN_GB45438")):
    data = await file.read()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="无法识别的图片文件")

    result_id, path = storage.save_upload(file.filename, data)
    ctx = DetectionContext(file_path=path, image=image,
                           target_regulation=target_regulation)
    items = _registry.run_all(ctx, modality="image")
    aigc = ctx.cache.get("aigc_metadata")
    report = build_report(items, aigc)
    result = DetectionResult(result_id=result_id, filename=file.filename or "image",
                             modality="image", items=items, aigc_metadata=aigc)
    payload = {
        "result_id": result_id,
        "detection": result.model_dump(),
        "report": report.model_dump(),
    }
    storage.store_result(result_id, payload)
    return payload


@router.get("/report/{result_id}")
def get_report(result_id: str):
    payload = storage.get_result(result_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到该检测结果")
    return payload
```

- [ ] **Step 3: 写 `backend/app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(title="AIGC 标识合规平台 (MVP)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
```

- [ ] **Step 4: 写集成测试 `backend/tests/test_api.py`**

```python
import io
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from tests.fixtures import make_png, VALID_AIGC

client = TestClient(app)


def _png_bytes(tmp_path, aigc=None):
    p = make_png(tmp_path / "x.png", aigc_dict=aigc)
    return open(p, "rb").read()


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_detect_compliant_image(tmp_path):
    data = _png_bytes(tmp_path, aigc=VALID_AIGC)
    r = client.post("/api/detect", files={"file": ("x.png", data, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["compliance"]["rating"] in ("A", "B")
    assert body["report"]["provenance"][0]["role"] == "ContentProducer"


def test_detect_unmarked_image(tmp_path):
    data = _png_bytes(tmp_path, aigc=None)
    r = client.post("/api/detect", files={"file": ("x.png", data, "image/png")})
    body = r.json()
    assert body["report"]["compliance"]["rating"] == "不合规"
    assert len(body["report"]["compliance"]["suggestions"]) >= 1


def test_detect_rejects_non_image():
    r = client.post("/api/detect",
                    files={"file": ("a.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_get_report_roundtrip(tmp_path):
    data = _png_bytes(tmp_path, aigc=VALID_AIGC)
    rid = client.post("/api/detect",
                      files={"file": ("x.png", data, "image/png")}).json()["result_id"]
    r = client.get(f"/api/report/{rid}")
    assert r.status_code == 200
    assert r.json()["result_id"] == rid


def test_get_report_404():
    assert client.get("/api/report/nope").status_code == 404
```

- [ ] **Step 5: 运行全部后端测试**

Run: `cd backend && .venv/Scripts/python -m pytest -v`
Expected: 全绿（含此前所有任务的测试）。

- [ ] **Step 6: 手动启动验证**

Run: `cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000`
打开 `http://localhost:8000/docs`，确认 `/api/detect`、`/api/report/{id}`、`/api/health` 可见。Ctrl+C 结束。

- [ ] **Step 7: 提交**

```bash
git add backend/app/storage.py backend/app/api/ backend/app/main.py backend/tests/test_api.py
git commit -m "feat: add storage, FastAPI app, detect/report routes"
```

---

## Task 9: 前端脚手架（Vite + React + TS + Ant Design）

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api.ts`

- [ ] **Step 1: 写 `frontend/package.json`**

```json
{
  "name": "aigc-compliance-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "antd": "^5.21.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.1",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: 写 `frontend/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

- [ ] **Step 3: 写 `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: 写 `frontend/index.html`**

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AIGC 标识合规平台</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: 写 `frontend/src/types.ts`**

```ts
export type CheckStatus = "pass" | "fail" | "warn";

export interface CheckItem {
  id: string;
  title: string;
  status: CheckStatus;
  detail: string;
  suggestion?: string | null;
  mark_type: string;
}

export interface ProvenanceNode {
  role: string;
  name: string;
  id?: string | null;
}

export interface Report {
  provenance: ProvenanceNode[];
  compliance: { items: CheckItem[]; rating: string; suggestions: string[] };
  tamper: { consistent: boolean | null; findings: string[] };
  ai_detection: { enabled: boolean; probability: number | null; note: string };
}

export interface DetectResponse {
  result_id: string;
  detection: {
    result_id: string;
    filename: string;
    modality: string;
    items: CheckItem[];
    aigc_metadata: Record<string, unknown> | null;
  };
  report: Report;
}
```

- [ ] **Step 6: 写 `frontend/src/api.ts`**

```ts
import type { DetectResponse } from "./types";

export async function detectImage(
  file: File,
  targetRegulation = "CN_GB45438"
): Promise<DetectResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("target_regulation", targetRegulation);
  const res = await fetch("/api/detect", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "检测失败");
  }
  return res.json();
}
```

- [ ] **Step 7: 写 `frontend/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import "antd/dist/reset.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 8: 安装依赖**

Run: `cd frontend && npm install`
Expected: 安装成功。（`src/App.tsx` 在 Task 10/11 创建，本步暂不启动 dev。）

- [ ] **Step 9: 提交**

```bash
git add frontend/package.json frontend/vite.config.ts frontend/tsconfig.json frontend/index.html frontend/src/main.tsx frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: scaffold frontend (vite + react + antd)"
```

---

## Task 10: 前端可视化组件

**Files:**
- Create: `frontend/src/components/CheckList.tsx`
- Create: `frontend/src/components/ComplianceRadar.tsx`
- Create: `frontend/src/components/ProvenanceChain.tsx`

- [ ] **Step 1: 写 `frontend/src/components/CheckList.tsx`**

```tsx
import { List, Tag } from "antd";
import type { CheckItem } from "../types";

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  pass: { color: "success", label: "✅ 合规" },
  fail: { color: "error", label: "❌ 缺失" },
  warn: { color: "warning", label: "⚠️ 待验证/格式" },
};

export default function CheckList({ items }: { items: CheckItem[] }) {
  return (
    <List
      itemLayout="vertical"
      dataSource={items}
      renderItem={(it) => {
        const s = STATUS_MAP[it.status] ?? { color: "default", label: it.status };
        return (
          <List.Item key={it.id}>
            <List.Item.Meta
              title={
                <span>
                  {it.title} <Tag color={s.color}>{s.label}</Tag>
                </span>
              }
              description={it.detail}
            />
            {it.suggestion ? (
              <div style={{ color: "#8c8c8c" }}>整改建议：{it.suggestion}</div>
            ) : null}
          </List.Item>
        );
      }}
    />
  );
}
```

- [ ] **Step 2: 写 `frontend/src/components/ComplianceRadar.tsx`**

纯 SVG 雷达图（零额外依赖），三个维度：元数据 / 显式文字 / 水印。状态映射为分值 pass=1, warn=0.5, fail/缺=0。

```tsx
import type { CheckItem } from "../types";

const AXES = [
  { key: "implicit_metadata", label: "隐式元数据" },
  { key: "explicit_text", label: "显式文字" },
  { key: "implicit_watermark", label: "隐式水印" },
];

function scoreFor(items: CheckItem[], markType: string): number {
  const it = items.find((i) => i.mark_type === markType);
  if (!it) return 0;
  if (it.status === "pass") return 1;
  if (it.status === "warn") return 0.5;
  return 0;
}

export default function ComplianceRadar({ items }: { items: CheckItem[] }) {
  const size = 240;
  const c = size / 2;
  const r = 90;
  const pts = AXES.map((ax, i) => {
    const angle = (Math.PI * 2 * i) / AXES.length - Math.PI / 2;
    const score = scoreFor(items, ax.key);
    return {
      ax: { x: c + r * Math.cos(angle), y: c + r * Math.sin(angle) },
      val: { x: c + r * score * Math.cos(angle), y: c + r * score * Math.sin(angle) },
      label: ax.label,
      angle,
    };
  });
  const polygon = pts.map((p) => `${p.val.x},${p.val.y}`).join(" ");
  return (
    <svg width={size} height={size}>
      <polygon
        points={pts.map((p) => `${p.ax.x},${p.ax.y}`).join(" ")}
        fill="none"
        stroke="#d9d9d9"
      />
      {pts.map((p, i) => (
        <line key={i} x1={c} y1={c} x2={p.ax.x} y2={p.ax.y} stroke="#f0f0f0" />
      ))}
      <polygon points={polygon} fill="rgba(24,144,255,0.3)" stroke="#1890ff" />
      {pts.map((p, i) => (
        <text
          key={i}
          x={c + (r + 18) * Math.cos(p.angle)}
          y={c + (r + 18) * Math.sin(p.angle)}
          fontSize="12"
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#595959"
        >
          {p.label}
        </text>
      ))}
    </svg>
  );
}
```

- [ ] **Step 3: 写 `frontend/src/components/ProvenanceChain.tsx`**

```tsx
import { Empty, Tag } from "antd";
import type { ProvenanceNode } from "../types";

export default function ProvenanceChain({ nodes }: { nodes: ProvenanceNode[] }) {
  if (!nodes.length) return <Empty description="无可还原的标识链路" />;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {nodes.map((n, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              border: "1px solid #1890ff",
              borderRadius: 8,
              padding: "8px 12px",
              background: "#e6f4ff",
            }}
          >
            <Tag color="blue">{n.role}</Tag>
            <div style={{ fontWeight: 600 }}>{n.name}</div>
            {n.id ? <div style={{ fontSize: 12, color: "#8c8c8c" }}>编号：{n.id}</div> : null}
          </div>
          {i < nodes.length - 1 ? <span style={{ fontSize: 20 }}>→</span> : null}
        </span>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/
git commit -m "feat: add frontend visualization components"
```

---

## Task 11: 前端页面与应用装配

**Files:**
- Create: `frontend/src/pages/UploadPage.tsx`
- Create: `frontend/src/pages/ReportPage.tsx`
- Create: `frontend/src/App.tsx`

- [ ] **Step 1: 写 `frontend/src/pages/UploadPage.tsx`**

```tsx
import { useState } from "react";
import { Button, Card, Select, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { detectImage } from "../api";
import type { DetectResponse } from "../types";

export default function UploadPage({ onResult }: { onResult: (r: DetectResponse) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [reg, setReg] = useState("CN_GB45438");
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!file) {
      message.warning("请先选择图片");
      return;
    }
    setLoading(true);
    try {
      onResult(await detectImage(file, reg));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="上传图片进行 AIGC 标识合规检测" style={{ maxWidth: 640, margin: "40px auto" }}>
      <Upload.Dragger
        beforeUpload={(f) => {
          setFile(f);
          return false;
        }}
        maxCount={1}
        accept="image/*"
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p>点击或拖拽图片到此处</p>
      </Upload.Dragger>
      <div style={{ marginTop: 16 }}>
        目标法规：
        <Select
          value={reg}
          onChange={setReg}
          style={{ width: 220, marginLeft: 8 }}
          options={[{ value: "CN_GB45438", label: "中国国标 GB 45438-2025" }]}
        />
      </div>
      <Button type="primary" onClick={submit} loading={loading} style={{ marginTop: 16 }}>
        开始检测
      </Button>
    </Card>
  );
}
```

- [ ] **Step 2: 写 `frontend/src/pages/ReportPage.tsx`**

```tsx
import { Alert, Button, Card, Collapse, Descriptions, Space, Tag } from "antd";
import type { DetectResponse } from "../types";
import CheckList from "../components/CheckList";
import ComplianceRadar from "../components/ComplianceRadar";
import ProvenanceChain from "../components/ProvenanceChain";

const RATING_COLOR: Record<string, string> = {
  A: "green",
  B: "blue",
  C: "orange",
  不合规: "red",
};

export default function ReportPage({
  data,
  onBack,
}: {
  data: DetectResponse;
  onBack: () => void;
}) {
  const { report, detection } = data;
  return (
    <div style={{ maxWidth: 900, margin: "24px auto" }}>
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={onBack}>← 重新检测</Button>
        <span>文件：{detection.filename}</span>
      </Space>

      <Card
        title={
          <span>
            合规评级{" "}
            <Tag color={RATING_COLOR[report.compliance.rating] ?? "default"}>
              {report.compliance.rating}
            </Tag>
          </span>
        }
        style={{ marginBottom: 16 }}
      >
        <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
          <ComplianceRadar items={report.compliance.items} />
          <div style={{ flex: 1, minWidth: 300 }}>
            <CheckList items={report.compliance.items} />
            {report.compliance.suggestions.length ? (
              <Alert
                type="info"
                showIcon
                message="整改建议"
                description={
                  <ul>
                    {report.compliance.suggestions.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                }
              />
            ) : null}
          </div>
        </div>
      </Card>

      <Card title="标识溯源" style={{ marginBottom: 16 }}>
        <ProvenanceChain nodes={report.provenance} />
      </Card>

      <Card title="篡改检测" style={{ marginBottom: 16 }}>
        {report.tamper.findings.length ? (
          report.tamper.findings.map((f, i) => (
            <Alert key={i} type="warning" showIcon message={f} style={{ marginBottom: 8 }} />
          ))
        ) : (
          <span>未发现明显篡改迹象（受未启用检测项限制）。</span>
        )}
      </Card>

      <Card title="AI 内容检测" style={{ marginBottom: 16 }}>
        <Alert type="info" showIcon message={report.ai_detection.note} />
      </Card>

      <Collapse
        items={[
          {
            key: "raw",
            label: "原始 AIGC 元数据",
            children: (
              <pre style={{ whiteSpace: "pre-wrap" }}>
                {JSON.stringify(detection.aigc_metadata, null, 2) || "无"}
              </pre>
            ),
          },
        ]}
      />
    </div>
  );
}
```

> 注：`Descriptions` 已 import 但本版未使用，删除该 import 以免 TS 报未使用变量。最终 import 行应为：
> `import { Alert, Button, Card, Collapse, Space, Tag } from "antd";`

- [ ] **Step 3: 写 `frontend/src/App.tsx`**

```tsx
import { useState } from "react";
import { ConfigProvider, Layout, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import UploadPage from "./pages/UploadPage";
import ReportPage from "./pages/ReportPage";
import type { DetectResponse } from "./types";

export default function App() {
  const [result, setResult] = useState<DetectResponse | null>(null);
  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: "100vh" }}>
        <Layout.Header style={{ color: "#fff" }}>
          <Typography.Title level={4} style={{ color: "#fff", lineHeight: "64px", margin: 0 }}>
            AIGC 标识合规平台 · MVP（图片检测）
          </Typography.Title>
        </Layout.Header>
        <Layout.Content style={{ padding: 24, background: "#f5f5f5" }}>
          {result ? (
            <ReportPage data={result} onBack={() => setResult(null)} />
          ) : (
            <UploadPage onResult={setResult} />
          )}
        </Layout.Content>
      </Layout>
    </ConfigProvider>
  );
}
```

- [ ] **Step 4: 安装图标依赖并启动验证**

Run: `cd frontend && npm install @ant-design/icons && npm run dev`
打开 `http://localhost:5173`（需后端在 8000 运行）。验证：
1. 上传一张普通图片 → 评级"不合规"、列出整改建议、溯源为空。
2. 用后端 `tests/fixtures.py` 生成一张含 `VALID_AIGC` 的 PNG 上传 → 评级 A/B、溯源链路有两个节点、原始元数据可展开。
Expected: 两种场景符合预期，控制台无报错。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ frontend/src/App.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat: add upload/report pages and app shell"
```

---

## Task 12: 收尾——完善 README 与全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 全量后端测试**

Run: `cd backend && .venv/Scripts/python -m pytest -v`
Expected: 全绿。

- [ ] **Step 2: 前端构建检查**

Run: `cd frontend && npm run build`
Expected: 构建成功，无 TS 错误。（若 `ReportPage` 残留未使用 import 报错，按 Task 11 Step 2 注释修正。）

- [ ] **Step 3: 补全 `README.md`**

在 README 增补：四板块说明、MVP 范围、生成测试图片的一行命令示例：
```bash
cd backend
.venv/Scripts/python -c "from tests.fixtures import make_png, VALID_AIGC; make_png('sample_marked.png', aigc_dict=VALID_AIGC); make_png('sample_unmarked.png')"
```
并写明：`sample_marked.png` 应判 A/B，`sample_unmarked.png` 应判不合规。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: complete README with run/verify instructions"
```

---

## 自查记录（spec 覆盖）

- 板块二图片检测（显式 OCR / 元数据 / 水印）→ Task 6（元数据真实，OCR/水印占位+逻辑骨架）✅
- 板块三四部分报告（溯源/合规/篡改/AI）→ Task 7 + Task 11 ✅
- GB45438 附录 E schema + jsonschema 校验 → Task 2 ✅
- 插件化引擎（基类+注册表）→ Task 5 ✅
- 简化栈 FastAPI 同步 + React/AntD → Task 8–11 ✅
- 真实可跑、仅 Pillow+jsonschema → Task 2/4/6 + requirements ✅
- 可视化（雷达图/溯源链路/清单/原始元数据）→ Task 10/11 ✅
- 测试夹具四类场景 + pytest → Task 3/6/8 ✅
- 验收标准 1–5 → Task 8 Step 6 / Task 11 Step 4 / Task 12 ✅
