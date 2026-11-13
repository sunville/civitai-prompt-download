"""异常类型。"""
from __future__ import annotations


class AppError(Exception):
    """带人话提示的应用异常。"""

    def __init__(self, message: str, detail: str = "", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint

    def friendly(self) -> str:
        text = self.message
        if self.hint:
            text += f"（建议：{self.hint}）"
        return text


class ApiError(AppError):
    """HTTP / 解析错误。"""

    def __init__(self, message: str, status: int | None = None, url: str = "",
                 body: str = "", hint: str = ""):
        detail = f"HTTP {status} {url}\n{body}".strip()
        super().__init__(message, detail=detail, hint=hint)
        self.status = status
        self.url = url
        self.body = body


class AuthError(ApiError):
    """API Key 无效/权限不足。"""


class CancelledError(AppError):
    """任务被用户取消。"""

    def __init__(self) -> None:
        super().__init__("任务已取消")
