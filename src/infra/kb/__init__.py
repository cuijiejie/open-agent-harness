"""知识库（RAG）子包

模块分工：
    store    PG + pgvector 的读写（三张表）
    parse    PDF/DOCX/XLSX/PPTX → 带页码的文本块
    chunk    中文友好的定长滑窗切分
    embed    调本地嵌入服务（HTTP，不在本进程加载模型）
    ingest   上传落盘 + 任务队列 + 解析/嵌入/入库
    search   向量 + BM25 混合召回 + RRF + 可选重排
"""
