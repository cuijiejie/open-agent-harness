"""单元测试包：导入时先给几个"能占位就行"的环境变量。

目的：单测不依赖外部服务，也不该要求开发者配好真实模型 key。
这些值只在测试进程里生效，不会影响真实运行。
"""
import os

os.environ.setdefault("FLASK_CONFIG", "dev")
os.environ.setdefault("PA_LLM_DEEPSEEK_API_KEY", "sk-placeholder-for-unit-tests")
os.environ.setdefault("PA_LLM_DEEPSEEK_BASE_URL", "http://127.0.0.1:9/v1")
os.environ.setdefault("PA_APP_SECRET_KEY", "unit-test-secret")
