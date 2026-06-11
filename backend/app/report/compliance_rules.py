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
