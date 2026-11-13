# 知识库检索冒烟评测（test/eval）

改检索参数（切块、余弦下限 `kb_min_score`、`kb_min_score_ratio`、RRF、重排开关）
或换嵌入模型/池化方式之后，**跑一遍这个再上线**。

```bash
# 部署机上（有管理员会话 /root/tok.txt）
cd /data1/oah-fastapi
/data1/miniforge3/envs/oah/bin/python test/eval/run_eval.py

# 本机对线上跑
python test/eval/run_eval.py --base http://10.10.203.64:5002/open-agent-harness --token <会话cookie> --verbose
```

退出码：`0` 全通过 / `1` 有用例失败 / `2` 环境问题（连不上、缺令牌）。

## 判定口径

| `expect` | 断言 |
|---|---|
| `has_hits` | 至少 1 条，且**第 1 条**来自 `expect_docs` 列出的文档 |
| `no_hits` | 必须 0 条（库里没有相关内容时不许硬凑） |

## 为什么是这两类用例

1. **该命中的没命中**：阈值调过头、换模型后向量分布变了、切块太大丢上下文；
2. **不该命中的硬凑**：2026-11-13 修掉的真实事故——编码器用了均值池化，
   无关中文片段余弦也有 0.70，`红烧肉的做法` 也能返回 5 条"看着像"的结果。
   改成 bge-m3 官方 CLS 池化 + `kb_min_score=0.48` 之后才分开
   （相关 0.54~0.71 / 无关 0.36~0.46）。

## 加用例

编辑 `cases.json`，字段：`id` / `query` / `expect` / `expect_docs` / `note`。
`expect_docs` 写文件名片段即可（用 `in` 匹配，不用写全名）。

## 注意

- 用例依赖线上知识库里**确实存在**的文档（当前是 `Harness-Engineering-Agent工程范式.pptx`
  与 `Open-Agent-Harness-测试报告-2026-11-10.md`）。库变了就同步改用例。
- 这组只测检索，不测生成；回答质量另看对话回归。
