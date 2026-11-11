"""知识库（RAG）子包 —— 基于 LlamaIndex

模块分工：
    settings    全局 Settings（切分器 / 嵌入模型），全仓库只在这里设置
    embedding   LlamaIndex Embedding 适配器 + 重排，后端是本地嵌入服务
    readers     文件 → Document（PDF/DOCX/XLSX/PPTX/TXT），带页码与小节
    index       PGVectorStore：建表、写入、按文档删除
    retrieve    向量 + 关键词双路召回 → RRF 融合 → 重排；上下文拼装
    store       业务状态表：文档登记 + 索引任务队列
    ingest      上传落盘 + 任务 + 解析/切分/嵌入
    kick        上传后触发后台 worker
    ocr         扫描件 OCR（走内网视觉网关）

对外只需要三个入口：ingest.save_upload / retrieve.search / retrieve.build_context。
"""
