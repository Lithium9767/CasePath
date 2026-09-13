"""请求级运行上下文：统一绑定/恢复，避免应用服务直接依赖检索细节。"""

from casepath.adapters.runtime.request_context import p4_request_context

__all__ = ["p4_request_context"]
