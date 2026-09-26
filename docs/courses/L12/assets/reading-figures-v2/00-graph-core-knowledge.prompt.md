# L12 Graph 核心知识图 ImageGen 提示词

- 生成方式：Codex 内置 ImageGen
- 用途：`实践操作手册.md` 开篇，解释可执行 Graph 的核心知识
- 采用文件：`00-graph-core-knowledge.png`

## 初次生成提示词

```text
Use case: scientific-educational
Asset type: Chinese beginner course opening infographic explaining the CORE KNOWLEDGE of an executable state Graph
Primary request: Create a precise teaching diagram, not a business process poster. Explain what a Graph is made of and how it runs. The procurement case is only a tiny example label, never the visual mainline.
Scene/backdrop: clean white academic infographic, flat 2D, strong hierarchy
Subject: Two-level composition.
TOP band titled "Graph 由五部分组成" with five clearly separated visual cards connected by subtle lines: "节点：现在做什么"; "边：允许去哪里"; "条件：什么时候能走"; "共享状态：一路带着哪些事实"; "执行器：真正执行并保存结果".
CENTER: a real executable state graph with rounded nodes and directed arrows. Exact nodes: "修改" -> "检查" -> "等待人工审核" -> "完成". Conditional arrows must be labeled exactly: from 检查 back to 修改, "失败且还有轮次"; from 检查 to 等待人工审核, "通过"; self-loop or pause badge at 等待人工审核, "无人决定：继续等待"; from 等待人工审核 back to 修改, "打回"; from 等待人工审核 to 完成, "批准"; from any execution path to "停止并留痕", label "异常或预算耗尽".
BOTTOM band titled "每走一步都留下证据" showing a shared-state record strip with exact labels: "当前节点"; "候选版本"; "Eval 报告"; "剩余轮次"; "审核决定"; "错误原因". Beside it, a small highlighted distinction: "Graph = 所有允许路径" and "Trace = 本次实际走过的路径".
Style/medium: polished flat vector-like educational illustration, rigorous information design, readable to a complete beginner, professional Chinese university course material
Composition/framing: 16:9 landscape, generous spacing, arrows cannot cross labels, central state graph is largest element, top cards secondary, bottom evidence strip clear
Color palette: blue nodes and main path, red return path, amber waiting node, green completed node, gray stopped node
Constraints: every Chinese label must be rendered exactly; clearly distinguish node, edge, condition, shared state, executor; clearly show branching, rollback, waiting, completion, abnormal stop, persistence, and Graph versus Trace; no procurement inventory numbers; no people portraits; no decorative story scenes; no unexplained English except Graph, Trace, Eval; no watermark
Avoid: linear-only flow, warehouse imagery, purchase workflow poster, merging Graph with business approval, dense code, tiny text, 3D, decorative cartoons
```

## 最终修订要求

```text
Preserve the three-band information architecture and all core concepts. Rebuild the middle state-graph area on a completely solid white background. Remove every black, transparent, speckled, shadowy, or noisy patch. Use clean flat 2D vector-like lines and ample white space. Make every arrow and label unambiguous and non-overlapping. Keep the top five cards, center states and conditions, bottom evidence fields, and Graph versus Trace distinction. No texture, transparency, decorative illustrations, people, warehouse imagery, or watermark.
```
