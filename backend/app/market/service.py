from .providers import ProviderRegistry
from .types import InvalidData, UsagePermission


class ResearchDataService:
    """显式选源，先检查权限；不自动fallback，不发布诊断结果。"""
    def __init__(self,registry:ProviderRegistry):self.registry=registry
    def quotes(self,provider,securities,*,purpose='diagnostic',deadline=None):
        source=self.registry.select(provider,required=('quotes_by_symbol',))
        if purpose not in {'diagnostic','private_research','public_display'}:raise InvalidData('未知数据使用目的')
        if purpose=='public_display' and source.source.usage_permission!=UsagePermission.PUBLIC_DISPLAY:raise InvalidData('尚未确认公开展示授权，已阻止请求与发布')
        if purpose=='private_research' and source.source.usage_permission==UsagePermission.UNKNOWN:raise InvalidData('尚未确认研究使用许可；仅允许独立诊断验证')
        return source.fetch_quotes(securities,deadline=deadline)
