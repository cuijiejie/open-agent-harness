#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 天气示例服务：FastMCP（streamable_http）

为什么在 test/ 下
-----------------
1. **测试夹具**：`test/mcp_weather/check.py` 会拉起它（stub 模式，不联网），验证
   "发现工具 → 包装成 mcp_<服务>_<工具> → 调用 → 结果规整"整条链路；
2. **接入示例**：线上把它当一台真服务跑着（systemd `pa-mcp-weather`，127.0.0.1:5120）。

为什么用 FastMCP 而不是手写 JSON-RPC
------------------------------------
手写只能覆盖 `tools/*`，会话、SSE、协议版本协商都要自己补。官方 `mcp` SDK 的
`FastMCP` 把 streamable_http 这几块都实现了，我们只管写工具：
    · 单端点 `/mcp`（POST 收发、GET 推送流、DELETE 结束会话）
    · 会话（`Mcp-Session-Id`）与无状态两种模式（这里 stateless_http=True）
    · SSE 与 JSON 两种响应（这里 json_response=True：工具一问一答够用）

为什么不用 LangChain 工具转一手
--------------------------------
服务端只做一件事：把函数暴露成 MCP 工具。FastMCP 的 `@mcp.tool()` 就是这个
（docstring = 工具说明，类型注解 = inputSchema）。再转一手（LangChain `@tool` →
`to_fastmcp` → 塞进 tool manager）只是多一层没有收益的间接。

LangChain 在本项目里的位置是**客户端**：`langchain-mcp-adapters` 负责把 MCP 工具接到 agent 上。
只有想复用某个**已存在的 LangChain 工具实现**时，才需要 `to_fastmcp` 那个桥。

数据源：Open-Meteo（免费、无需 key）
    https://geocoding-api.open-meteo.com/v1/search   地名 → 经纬度
    https://api.open-meteo.com/v1/forecast           当前天气

跑法
----
    PYTHONPATH=. <env>/bin/python -m uvicorn test.mcp_weather.server:app --host 127.0.0.1 --port 5120
    MCP_WEATHER_STUB=1 ...        # 离线桩，返回固定数据（自检用）

接入：页面「MCP 服务 → 新增服务」填 `http://127.0.0.1:5120/mcp`。
"""
from __future__ import annotations

import logging
import os

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("mcp-weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# 测试/离线：不请求外网，返回固定值
STUB: bool = str(os.getenv("MCP_WEATHER_STUB", "")).strip() in ("1", "true", "yes")

# WMO 天气代码 → 中文
WMO_ZH: dict[int, str] = {
    0: "晴", 1: "基本晴", 2: "少云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "弱雨", 55: "中雨",
    56: "冻毛毛雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}

# stateless_http：每次请求独立，不需要会话；json_response：回 application/json
mcp = FastMCP("mcp-weather", stateless_http=True, json_response=True)


def _geocode(city: str) -> dict:
    r = httpx.get(GEOCODE_URL,
                  params={"name": city, "count": 1, "language": "zh", "format": "json"},
                  timeout=15)
    r.raise_for_status()
    hits = (r.json() or {}).get("results") or []
    if not hits:
        raise ValueError(f"没找到城市「{city}」，换个写法试试（如 北京 / Beijing）")
    return hits[0]


# FastMCP 原生工具：docstring 就是给模型看的说明，参数类型 → inputSchema
@mcp.tool()
def get_weather(city: str) -> str:
    """查询某个城市当前的天气：气温、体感温度、湿度、风速、天气状况。城市名中英文都行。"""
    if STUB:
        return (f"{city}（stub） 当前天气：晴\n气温 20.0°C（体感 20.0°C）\n"
                f"湿度 50%，风速 5.0 km/h\n观测时间 2026-01-01T12:00（当地时间）")
    place = _geocode(city)
    r = httpx.get(FORECAST_URL, params={
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
        "timezone": "auto",
    }, timeout=15)
    r.raise_for_status()
    cur = (r.json() or {}).get("current") or {}
    # 注意：weather_code=0 是"晴"，不能被 `or` 当假值吃掉（曾把成都报成"未知(0)"）
    code = cur.get("weather_code")
    desc = WMO_ZH.get(int(code), f"未知({code})") if code is not None else "未知"
    label = f"{place.get('name')}（{place.get('admin1') or place.get('country') or ''}）"
    return (
        f"{label} 当前天气：{desc}\n"
        f"气温 {cur.get('temperature_2m')}°C（体感 {cur.get('apparent_temperature')}°C）\n"
        f"湿度 {cur.get('relative_humidity_2m')}%，风速 {cur.get('wind_speed_10m')} km/h\n"
        f"观测时间 {cur.get('time')}（当地时间）"
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    try:
        names = [t.name for t in mcp._tool_manager.list_tools()]
    except Exception:
        names = ["get_weather"]
    return JSONResponse({"ok": True, "service": "mcp-weather", "transport": "streamable_http",
                         "stub": STUB, "tools": names})


app = mcp.streamable_http_app()
