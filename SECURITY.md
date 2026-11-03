# 安全策略

## 报告漏洞

请**不要**在公开 issue 里描述安全问题。发邮件到 `<security@example.com>`，说明：

- 影响范围（哪个接口/组件）
- 复现步骤（能稳定复现的最小步骤）
- 你判断的风险等级

我们会在 3 个工作日内确认收到，并在修复后同步公布。

## 部署时的安全清单

1. `[app] test_login_enabled = false` —— 否则任何人输入用户名即可登录
2. `[app] secret_key` 使用随机值（`python -c "import secrets;print(secrets.token_hex(32))"`）
3. 所有口令/密钥走环境变量或 `config/configs.cfg`（已被 .gitignore 忽略）
4. 沙箱策略默认拒绝所有出站，按需最小放行
5. 平台自身的 RBAC 管的是**工具权限**，不是网络准入；生产建议放在内网或加认证网关

## 支持范围

只对最新 release / main 分支的安全问题提供修复。
