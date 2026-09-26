# L09 自学图解版 imagegen 提示词

模式：内置 image_gen。以下图片用于教学方法示意，不能作为实际执行截图。最终图片保存在本目录，PPT 内已嵌入。

## mainline.png

Use case: infographic-diagram. Asset type: Chinese self-study engineering course slide illustration. Create one polished 16:9 wide educational infographic. Warm ivory background #FAF9F6, deep navy #18283D, muted gold #A67B40, restrained slate blue. Flat editorial technical illustration, clear large Chinese typography, airy layout, no faux UI, no 3D, no robots, no watermark, no page numbers, no extra title. This is a conceptual method illustration, never simulated execution evidence. 横向主线图，4个依次连接的阶段。精确文字仅为：『失败分诊』『Repair Task』『Codex 修改』『Harness 回判』。每阶段下方小字分别为『调查与取舍』『目标与范围』『一次受控修改』『新反馈与决定』。使用细线单向连接，表现一次从左至右的过程，终点停止，不画返回循环。以文档、工作单、代码差异、结果对照为视觉对象。 All text large and legible. Reserve modest outer margins.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-0395c615-f553-4014-90de-12c2474521ea.png

## inventory.png

Use case: infographic-diagram. Asset type: Chinese self-study engineering course slide illustration. Create one polished 16:9 wide educational infographic. Warm ivory background #FAF9F6, deep navy #18283D, muted gold #A67B40, restrained slate blue. Flat editorial technical illustration, clear large Chinese typography, airy layout, no faux UI, no 3D, no robots, no watermark, no page numbers, no extra title. This is a conceptual method illustration, never simulated execution evidence. 左右对照的订单预占归属图。左侧标题『取消后仍占用』，右侧标题『本次修复目标』。每侧有同样的两个订单对象 TARGET 与 OTHER。左侧 TARGET 显示已取消，却用橙色系连线连接它的库存份额。OTHER 用蓝色连线连接另一份库存。右侧 TARGET 的连线解除，原份额变为可用，OTHER 连线保持。底部精确文字『释放 TARGET · 保留 OTHER · 在库量不变』。不画数字。不同订单份额必须清楚分开，不画实物库存凭空增加。 All text large and legible. Reserve modest outer margins.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-7be09b5c-0da6-41e3-a322-8709ab36616c.png

## triage.png

Use case: infographic-diagram. Asset type: Chinese self-study engineering course slide illustration. Create one polished 16:9 wide educational infographic. Warm ivory background #FAF9F6, deep navy #18283D, muted gold #A67B40, restrained slate blue. Flat editorial technical illustration, clear large Chinese typography, airy layout, no faux UI, no 3D, no robots, no watermark, no page numbers, no extra title. This is a conceptual method illustration, never simulated execution evidence. 三行观察汇入一次共同调查，再分成三种工作安排的流程图。左侧三张报告短标签分别为『预占未释放』『首次取消失败』『释放路径未进入』，细线汇到中心『调查取消路径』。中心向右三个分支分别为『共同原因：合并任务』『独立原因：拆分任务』『证据不足：继续调查』。中心下方有小字『用发现作决定』。明确三项失败只是调查线索，并未直接证明共同根因。不要画模型自动批准。 All text large and legible. Reserve modest outer margins.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-50266521-4d80-45cd-88c4-b586cf42aebc.png

## mapper.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. 映射器机制图。左侧输入材料三层短标签『可信失败报告』『原任务与候选』『已确认的分诊决定』，向中心『修复任务映射器』汇入，中心下方小字『筛选阻断项，保留证据，组装字段』。右侧三个互斥输出分支『材料足够：任务草案』『无阻断失败：无需修复』『材料不足：列出缺项』。必须有从人类审核者到任务草案的独立签字标记，标签『确认范围后分派』。无自动根因判断，无假绿色结果。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

最终修订指令：仅修正两处语义文字，保持布局、颜色、其他内容完全不变：1）『原任务与候选』下的小字替换为『原始需求、候选目录与版本』，不要把候选解释成修复方向。2）右上输出标题『材料足够：任务草案』替换为『有效且有阻断失败：任务草案』，与无阻断失败分支互斥。

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-a45978a8-f6e8-41f0-99dd-0688b24e81f1.png

## ai4ai.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. AI4AI 的建设与使用关系图。左半部分标签『建设工作台』：一个学生侧影指导代码助手，代码助手旁标签『Codex』，共同完善一个文档组装工具，工具标签『任务生成器』。右半部分标签『工作台组织交付』：同一个任务生成器产出『Repair Task』，将任务交给另一阶段的『Codex』，最终输出『FlowERP 修复候选』。左右之间细线单向连接。顶部小字『AI for AI』，底部小字『改进代码与工作流程，模型权重保持不变』。不画模型脑网络、不画训练、不画无人批准。突出先建设，再使用，两个不同任务。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-f7f89822-6c4c-4adf-a0ba-e5fde89b189b.png

## execution.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. 一次受控修改的工程流程图，清晰横向路线：『确认后的任务』传入『候选 V0』，然后进入『Codex 范围内修改』，然后到『候选 V1 + Diff』。在 Codex 节点周围细虚线边界，标签『具体允许文件』。从此节点向下分支到『需要越界』，最后『保存现场，交回负责人』。底部正向路线终点标签『交给已有 Harness』。图中的候选只代表版本，无真实成功数字。保持横向主线、下方异常分支，不画自动重试闭环。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-45cfe640-198b-4ead-b42e-fb210cbd07a1.png

## verdict.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. 修复结果的三种结束方式流程图。左侧『V1 + 原任务』到中间『已有 Harness』。中间向右上『目标恢复、检查通过、范围符合』到『负责人接受』，向右中『仍有阻断失败』到『退回』，向右下『目标冲突或需越界』到『转人工』。底部独立贯穿注记『每个出口都保留候选、改动、最后报告和未决问题』。三路分别克制深蓝、金色、灰蓝，不使用醒目绿色勾号误导为实际执行结果。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

最终修订指令：保持布局、配色和全部主标签，仅精确修正四处小字：1）V1到Harness箭头上文字改为『根据原任务检查 V1』。2）已有 Harness 下方解释改为『运行既有检查，输出结果』。3）负责人接受下方改为『记录接受理由与候选版本』，删除合并进入主线相关文字。4）退回下方改为『记录剩余失败，保留本轮结果』，不要出现自动再次尝试。

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-b5ba6111-6c85-4ae2-bb53-d5a5b3de2ac1.png

## chain.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. 一轮修复的可追溯材料链。横向五个清楚的实体文件，依次用细线连接。精确标签『原报告』『分诊决定』『确认任务』『V1 与 Diff』『新报告与决定』。每个对象的下方分别小字『来自候选 V0』『为何合并或拆分』『目标与允许文件』『本轮实际改动』『接受、退回或转人工』。下方一条统一的虚线关联带标注『同一事项，关联任务版本与候选身份』。表现文件之间的来源关系，没有锁链装饰、没有假编号、不画截图。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-5e3b2f30-a6dc-4b07-9215-d7c95b658cce.png

## next.png

Use case: infographic-diagram. Asset type: conceptual method figure for a Chinese self-study engineering PPT. One wide 16:9 image, warm ivory #FAF9F6, deep navy #18283D, muted gold #A67B40, slate blue, consistent flat editorial technical illustration. Large accurate simplified Chinese typography. No page numbers, no watermark, no faux app UI, no 3D robots, no decorative card grid, no extra headline. L09 到 L10 的课程承接图。左侧一个完整且独立的阶段『L09 一轮修复』包含两个短标签『任务交接』『修改与回判』。向右通往『L10 有界 Loop』，内部有顺序明确的 V0、V1、V2 三张代码版本文件，不画无限循环。V2 下方有指回 V1 的细线标签『保留较好候选』。右侧有明确出口标签『达标即停』『无进展停止』『预算耗尽交回』。底部准确小字『比较已尝试候选，不保证全局最优』。所有箭头方向清楚，足够留白。 Large labels, clear connections and ample margins. This is explicitly a teaching schematic, not evidence of executed work.

生成输出：C:\Users\Administrator\.codex\generated_images\01a08f07-73a1-7410-85b6-f4deb1e880a5\exec-4d263fb9-73ee-4d7c-a0b5-dd876d01e2d6.png
