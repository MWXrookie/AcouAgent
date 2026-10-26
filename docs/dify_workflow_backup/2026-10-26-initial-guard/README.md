# 原始初压声明守卫备份

- `published-before.json`：2026-10-13 原发布图；63 节点/70 边。规范化 SHA `8a530a72281300dd223f6b66cf4b5385271c3aa419167c674b282dc06fb7f64c`。
- `candidate-after.json`：64 节点/71 边候选。SHA `884babb5e4162f7067bc8170cef891ebffee4a2faf9d311b8bef10259a13fda9`。新增共享原始初压校验，并显式选用官方原生 Flash。

候选由 `python -m scripts.workflow.initial_declaration_guard_graph` 生成，输出拒绝覆盖已有文件。SHA 使用 UTF-8、`ensure_ascii=False`、排序键和紧凑 JSON 分隔符。

这是部署图备份，包含原部署的节点与资源引用，不是通用安装配置。完整应用/发布版本/草稿及变量的私有恢复备份另存受控目录，不进入 Git。备份时草稿与发布图一致；应用前须再次检查发布指针和草稿摘要，不覆盖其他人的新编辑。通过 Dify 原生校验与发布服务发布，不伪造登录令牌。旧发布版本保留，可切回原指针恢复。

实际状态以 [本轮验收记录](../../validation/stage7/MODEL_COMPATIBILITY_2026-10-26.md) 为准。
