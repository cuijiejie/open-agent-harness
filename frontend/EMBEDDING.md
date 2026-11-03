# ChatWidget 通用聊天组件 — 嵌入指南

## 概述

ChatWidget 是一个可嵌入到任意 Web 系统的通用聊天组件，右下角悬浮按钮触发弹窗，无需框架依赖，不污染宿主页面样式。

弹窗尺寸：`460px × 600px`，位于页面右下角。

---

## 架构说明

整个系统涉及**两套独立的服务**，请务必区分清楚：

```
┌─────────────────────────────┐      ┌─────────────────────────────┐
│     业务系统（你要嵌入的系统）    │      │    ChatWidget 后端（open-agent-harness） │
│                              │      │                              │
│  存放 JS/CSS 静态文件         │ HTTP │  处理聊天 AI 请求              │
│  用户访问的页面所在的服务       │─────>│  管理会话数据                  │
│  例如：CRM、OA、官网等         │ 请求  │                              │
│  js/css 文件放在这里！         │      │  apiBase 指向这里！            │
└─────────────────────────────┘      └─────────────────────────────┘
```

---

## 嵌入步骤

### 1. 把 JS/CSS 文件放到业务系统的静态资源目录

将 `dist/assets/` 下的两个文件拷贝到**你要嵌入的目标业务系统**中（如 Spring Boot 的 `static/`、Nginx 的 `html/`、或任意 CDN 目录），放到同一目录下即可：

- `index-DcG27bzn.js`
- `index-D4blQ9uW.css`

> **注意：这两个文件是放到业务系统那边，不是放到 ChatWidget 后端。**

### 2. 在业务系统的页面中引用

在业务系统任意 HTML 页面的 `</body>` 前引入，通过 `window.ChatWidgetConfig` 配置参数：

```html
<link rel="stylesheet" href="/path/assets/index-D4blQ9uW.css">
<script>
window.ChatWidgetConfig = {
  apiBase: 'http://ChatWidget后端地址:8000',
  username: '当前用户',
  sysId: '当前系统标识'
}
</script>
<script type="module" crossorigin src="/path/assets/index-DEFCDslm.js"></script>
```

> 业务系统本身就是 Web 服务，JS/CSS 通过业务系统的 HTTP 加载即可，不需要额外启动任何服务器。

---

## 配置参数

通过 `window.ChatWidgetConfig` 全局 JS 对象配置，需在 JS 文件加载前声明：

```html
<script>
window.ChatWidgetConfig = {
  apiBase: 'https://chat.your-company.com',
  username: '张三',
  sysId: 'crm_system'
}
</script>
<script type="module" crossorigin src="/path/assets/index-DEFCDslm.js"></script>
```

| 属性 | 必填 | 说明 |
|------|------|------|
| `apiBase` | 生产必填 | **ChatWidget 后端**地址（open-agent-harness 服务），如 `https://chat.example.com` |
| `username` | 必填 | 当前用户标识，用于会话隔离 |
| `sysId` | 必填 | 系统标识，用于多系统间隔离 |

> **`apiBase` 是 ChatWidget 自己的后端地址，不是业务系统的后端地址。**
>
> **注意**：`username` 和 `sysId` 无默认值，必须显式传入，否则后端将拒绝请求。

---

## 完整示例

假设你要把聊天组件嵌入到公司 CRM 系统（`crm.your-company.com`），ChatWidget 后端部署在 `chat.your-company.com`：

- JS/CSS 文件放在 **CRM 系统**的 `/static/assets/` 目录下
- `apiBase` 指向 **ChatWidget 后端** `https://chat.your-company.com`

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>CRM 管理系统</title>
  <link rel="stylesheet" href="/static/assets/index-D4blQ9uW.css">
</head>
<body>
  <h1>欢迎使用 CRM 管理系统</h1>
  <p>页面内容...</p>

  <script>
  window.ChatWidgetConfig = {
    apiBase: 'https://chat.your-company.com',
    username: 'admin',
    sysId: 'crm_system'
  }
  </script>
  <script type="module" crossorigin src="/static/assets/index-DEFCDslm.js"></script>
</body>
</html>
```

---

## 注意事项

1. **样式隔离**：组件所有样式限定在 `.app` 范围内，不会影响宿主页面
2. **脚本类型**：必须使用 `type="module"` 和 `crossorigin` 属性
3. **JS/CSS 放哪里**：放到你要嵌入的**业务系统**的静态资源目录下，不是 ChatWidget 后端
4. **apiBase 指什么**：指向 **ChatWidget 后端**（open-agent-harness 服务）的地址，不是业务系统的后端
5. **用户隔离**：`username` + `sysId` 共同决定会话归属，不同用户/系统需传入不同的值。**必须显式配置，否则后端将拒绝请求**
6. **构建更新**：每次重新构建后 JS/CSS 文件名会变化，需同步更新引入路径
