"""数据传输对象定义"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求体

    注意：不包含 username 字段——用户身份一律从后台登录 session 获取，
    绝不接受前端传入的用户名（防止身份伪造越权）。
    """
    session_id: str = Field(default="", description="前端传入的会话标识")
    message: str = Field(default="", description="用户输入的消息内容")
    resume_value: str | list[str] | None = Field(default=None, description="点击选项后的恢复值（ask_user interrupt 恢复用，多选时为列表）")


class ChatResponse(BaseModel):
    """聊天响应体"""
    session_id: str = Field(description="会话标识")
    content: str = Field(description="Agent 返回的响应内容")
