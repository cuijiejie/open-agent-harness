#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""示例 MCP 服务（streamable_http）：查天气

为什么有这个文件
----------------
本项目的 MCP 客户端只认 streamable_http，而网上的天气 MCP 几乎都是 stdio 小包
（要 npm/pip 起进程）。这个 80 行的服务演示"把一个普通 HTTP 能力包成 MCP"：

    POST /mcp  ← JSON-RPC 2.0（initialize / tools/list / tools/call）
    GET  /health

数据源用 Open-Meteo（免费、无需 key、国内可访问）：
    ① 地名 → 经纬度：https://geocoding-api.open-meteo.com/v1/search
    ② 当前天气：    https://api.open-meteo.com/v1/forecast

跑起来：
    PYTHONPATH=. <env>/bin/python -m uvicorn deploy.mcp_weather.server:app --host 127.0.0.1 --port 5120
然后到「技能与工具 → MCP 服务 → 新增服务」填：
    服务名     weather
    接入地址   http://127.0.0.1:5120/mcp
"""
from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

log = logging.getLogger("mcp-weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

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

TOOLS: list[dict] = [
    {
        "name": "get_weather",
        "description": "查询某个城市当前的天气：气温、体感温度、湿度、风速、天气状况。城市名中英文都行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 北京、上海、Tokyo"},
            },
            "required": ["city"],
        },
    },
]

app = FastAPI(title="MCP 天气示例", docs_url=None, redoc_url=None)


async def _geocode(city: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "zh", "format": "json"})
        r.raise_for_status()
        data = r.json()
    hits = data.get("results") or []
    if not hits:
        raise ValueError(f"没找到城市「{city}」，换个写法试试（如 北京 / Beijing）")
    return hits[0]


async def _weather(city: str) -> str:
    place = await _geocode(city)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(FORECAST_URL, params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": "auto",
        })
        r.raise_for_status()
        cur = (r.json().get("current") or {})
    desc = WMO_ZH.get(int(cur.get("weather_code") or -1), f"未知({cur.get('weather_code')})")
    label = f"{place.get('name')}（{place.get('admin1') or place.get('country') or ''}）"
    return (
        f"{label} 当前天气：{desc}\n"
        f"气温 {cur.get('temperature_2m')}°C（体感 {cur.get('apparent_temperature')}°C）\n"
        f"湿度 {cur.get('relative_humidity_2m')}%，风速 {cur.get('wind_speed_10m')} km/h\n"
        f"观测时间 {cur.get('time')}（当地时间）"
    )


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "mcp-weather", "tools": [t["name"] for t in TOOLS]}


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}}, status_code=400)

    method: str = str(body.get("method") or "")
    req_id = body.get("id")
    params: dict = body.get("params") or {}

    # 通知（没有 id）：只回 202
    if req_id is None:
        return Response(status_code=202)

    if method == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": params.get("protocolVersion") or "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-weather", "version": "1.0"},
        }})

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})

    if method == "tools/call":
        name: str = str(params.get("name") or "")
        args: dict = params.get("arguments") or {}
        if name != "get_weather":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown tool: {name}"}})
        city: str = str(args.get("city") or "").strip()
        if not city:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": "缺少参数 city"}], "isError": True}})
        try:
            text = await _weather(city)
            log.info("天气查询成功: city=%s", city)
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}})
        except Exception as e:
            log.warning("天气查询失败: city=%s err=%s", city, e)
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": f"查询失败：{str(e)[:200]}"}], "isError": True}})

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {
        "code": -32601, "message": f"method not found: {method}"}})
