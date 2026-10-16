"""PDF 元数据适配器（GB 45438-2025 隐式标识）。

- 载体：ExifTool 写 ``XMP-aigc:AIGC`` 进 PDF 的 XMP 元数据包（Metadata 流），
  与图片/视频同一套写法，故读写直接复用 ``BaseAdapter``（已实测通过：
  写入恰好一条、重复写入仍一条、页数与 %%EOF 存活，见
  markdown-pdf-carrier-validation.md，在桌面）。
- 旧载体 ``PDF:AIGC_JSON``（Document Info 字典键）由 ``remove_aigc`` 一并清除。
- **写入前预检**：加密或已签名的 PDF 一律拒绝。ExifTool 是整体重写文件，任何
  字节改动都会让签名失效——而保护签名正是国标附录 E 预留字段想做的事，
  工具不该反过来把它毁掉；加密文档则根本无法安全重写。
- 媒体完整性（§9.4）：页数 / %%EOF / 加密状态 / 体积对比（``core.pdfstruct``）。
"""
from __future__ import annotations

from pathlib import Path

from ..core import pdfstruct
from ..core.jobs import MODALITY_TEXT
from ..core.reader import AIGCRecord, ReaderError, exiftool_tags, read_aigc_records
from .base import BaseAdapter, FileContentError, MediaReport, fingerprint_of


class PdfAdapter(BaseAdapter):
    carrier_id = "pdf-xmp-aigc-v1"
    modality = MODALITY_TEXT
    supported_mimes = ("application/pdf",)

    # ---- 已有标识检测（与检测器共用 reader）----
    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        return read_aigc_records(str(path), exiftool=self.exiftool,
                                 config=self.exiftool_config,
                                 mime="application/pdf")

    # ---- 写入前预检 ----
    def preflight(self, path: str | Path) -> pdfstruct.PdfProbe:
        """拒绝无法安全写入的 PDF；返回探测结果供调用方复用。"""
        try:
            probe = pdfstruct.scan(path)
        except OSError as e:
            raise FileContentError(f"无法读取 PDF: {e}") from None
        if probe.encrypted:
            raise FileContentError("PDF 已加密，无法安全写入标识（请先解密后重试）")
        if probe.signed:
            raise FileContentError("PDF 已含数字签名，写入标识会使签名失效，已拒绝")
        if not probe.has_eof:
            raise FileContentError("PDF 缺少 %%EOF 结束标记，文件可能损坏或被截断")
        return probe

    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        # 预检放在这里而不只放在 API 层：即便任务是通过别的入口创建的，
        # 也不会绕过"不破坏签名"的保证。
        self.preflight(src)
        super().write_metadata(src, dst, aigc_obj)

    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        self.preflight(src)
        super().remove_aigc(src, dst)

    # ---- 媒体完整性 ----
    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        """写入前后结构签名对比。任一探测失败或差异即判失败。"""
        try:
            before = self._probe(src)
            after = self._probe(dst)
        except OSError as e:
            return MediaReport(passed=False, reason=f"PDF 不可读: {e}")

        problems = pdfstruct.integrity_problems(before, after)
        return MediaReport(passed=not problems,
                           reason="; ".join(problems) if problems else None,
                           before=before.to_dict(), after=after.to_dict())

    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        """内容流载荷哈希作为内容指纹（编号登记库用，§6.1 b）。

        早先这里是"页数 + ``%%EOF``"，是三种模态里最弱的一个——两份页数相同的
        不同文档算出来一模一样，登记库核对形同虚设。现在改成所有**内容流**载荷
        的 sha256（跳过 XMP 元数据流），原理与边界见 ``pdfstruct.content_digest``。

        为什么不直接用文件哈希：ExifTool 写 PDF 是整体重写，对象偏移与
        ``startxref`` 全变，文件哈希跨写入必变。流的**载荷字节**则原样复制，
        所以既能区分内容、又跨写入稳定。

        仍然比图片的像素哈希弱一档：它证明"内容流的字节没变"，不做渲染，
        因此证明不了"渲染出来还是同一页"。对"是不是同一份文档"这个用途够用。
        """
        probe = self._probe(path)
        return pdfstruct.content_digest(path, page_count=probe.page_count,
                                        has_eof=probe.has_eof), "content"

    def _probe(self, path: str | Path) -> pdfstruct.PdfProbe:
        probe = pdfstruct.scan(path)
        probe.page_count = self._page_count(path)
        return probe

    def _page_count(self, path: str | Path) -> int | None:
        """页数由 ExifTool 报告；读不出（如加密/损坏）返回 None，不视为致命。"""
        try:
            tags = exiftool_tags(str(path), self.exiftool, self.exiftool_config)
        except (ReaderError, OSError):
            return None
        return pdfstruct.page_count_from_tags(tags)
