# 参与贡献

1. Fork → 从 `main` 切分支：`feat/xxx` 或 `fix/xxx`
2. 改动前先建 issue 说明动机，避免做完发现方向不一致
3. 提交前自查：
   ```bash
   python -m compileall -q src config
   cd frontend && npm run build
   python -m unittest discover -s test/unit -t . -q
   ```
4. PR 描述里写清：**做了什么 / 为什么 / 怎么验证的**（贴命令输出，不要只写"测试通过"）
5. 不要提交任何真实口令、内网地址、客户数据；示例一律用占位符

## 代码约定

- 注释解释**为什么**，不复述**做了什么**
- 对外接口的参数校验放在 controller 层；权限判断只认服务端 session
- 新增工具优先用插件（`src/plugins/tools/<name>/`），不要改内核
