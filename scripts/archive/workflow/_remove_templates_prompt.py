"""A/B 辅助：从代码生成 prompt 移除"代码骨架速查" section（幂等，回滚用）。

用法: python3 _remove_templates_prompt.py <graph.json> > new_graph.json
"""

import json
import re
import sys

CODE_GEN = "1783991079665"
MARKER = "## 代码骨架速查"


def main():
    g = json.load(open(sys.argv[1], encoding="utf-8"))
    for n in g.get("nodes", []):
        if n.get("id") == CODE_GEN:
            for m in n["data"].get("prompt_template", []):
                if m.get("role") == "user" and MARKER in m["text"]:
                    # 删除从 marker 到 prompt 结尾的内容（骨架 section 是最后追加的）
                    m["text"] = m["text"][: m["text"].index(MARKER)].rstrip()
    print(json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
