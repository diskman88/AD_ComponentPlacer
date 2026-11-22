# AI 协同后处理布局

## 1. 分工原则

ClassPlacer 与 AI/`eda-agent` 不同时操作 Altium Designer。

ClassPlacer 只负责把 Component Class 收拢为可复现的**一次性初始预布局**：中心和锁定件不动、类内连接尽量短、成员不碰撞。它不要求也不尝试将初始布局放进板框；这一步不读取板框、Keepout 和结构限制。

AI + `eda-agent` 在 ClassPlacer 完成并退出后，读取板框、禁放区、结构件和电气规则，把预布局功能块放进整板，并以“尽量不打散预布局”为默认原则做优化。完成初排后，普通的板内移动、对齐、压缩或翻面均归人工与 AI 处理，不应反复重跑插件。

这种分工避免两个 AD 控制者并发，也把机械/电气工程判断留给更适合处理规则上下文的 AI 阶段。

## 2. 严格时序

```text
ClassPlacer 预布局 → AD 自动写回 → .adstatus = APPLIED
                                      ↓
                       AI 读取 handoff + EDA 只读审查
                                      ↓
                       AI/eda-agent 单独执行受限后处理
                                      ↓
                         DRC、人工复核、保存
```

在 `.adstatus` 不是 `APPLIED` 时，禁止 `eda-agent` 对该 PCB 进行读取、弹窗、移动、保存或改规则。ClassPlacer 在写回前会重验快照；任何并发 AD 操作都可能使批次失败或让 UI 卡住。

预布局成功后，任务目录会包含：

- `<job>.snapshot.ai-handoff.json`：给 AI 的功能块交接清单；
- `<job>.snapshot.report.json`：各 Class 的 HPWL 改善和搜索信息；
- `<job>.snapshot.rooms.json`：Class 包围盒审计信息；
- `<job>.snapshot.adstatus`：唯一的“可开始 AI 后处理”信号。

## 3. AI 的输入与权限边界

AI 应读取以下事实，而不是猜测：

1. handoff 中每个功能块的成员、中心、包围盒、相对坐标和锁定状态；
2. PCB 同目录的 `ClassPlacerRules.json`，其中的 `hard` 引脚近距规则不可由 AI 后处理突破；
3. AD 中的真实 Board Shape、Keepout、机械孔、外壳开口、接口与高度限制；
4. 网络、差分/长度匹配规则、组件类别和已布线状态；
5. 用户提供的电源、高速、模拟、射频、热、安规和装配要求。

AI 可以通过 `eda-agent` 在后处理阶段读取或修改 AD；但每轮只允许一个控制者。运行 ClassPlacer、打开其等待窗口或出现未完成任务状态时，AI 必须停止所有 AD 调用。

AI 不应直接“自由拖动”器件。它先形成结构化 brief，再按以下权限层级执行：

1. **刚性平移**：功能块全部成员使用同一个 `dx/dy`，不旋转、不改变相对坐标；这是默认操作。
2. **受限局部调整**：仅在 brief 明确给出范围内，调整少数外围件，以满足板边、Keepout 或电气引脚邻近性。
3. **重新展开功能块**：会破坏预布局，只能在明确冲突和用户批准后进行。

锁定器件、结构件、中心器件以及明确标为不可移动的器件永远不可由 AI 移动。

## 4. 给 AI 的约束和优先级

使用 [AI_POSTLAYOUT_BRIEF.example.json](../AI_POSTLAYOUT_BRIEF.example.json) 复制成每板的 brief。规则应描述结果和边界，不应要求 AI 用不透明的坐标猜测。

AI 的决策优先级固定为：

1. **硬约束**：板框内、Keepout 外、锁定件不动、间距/高度/层面/装配约束合法；
2. **电气拓扑**：电源高电流回路、去耦至电源脚、晶振、高速、差分、模拟/RF、隔离边界；
3. **连接长度**：在拓扑不变差的前提下缩短关键连接；
4. **板面利用率**：功能块靠近可用区域、消除大空洞、为走线和测试保留通道；
5. **视觉整理**：对齐、行列、丝印可读性。

高优先级绝不能为了低优先级退化。例如，不能为了器件对齐而拉大 DC/DC 高电流环路，也不能为了压缩面积突破 Keepout。

## 5. 规则写法

每个功能块至少给出：

- `class`：与 handoff 中完全相同的 Component Class 名称；
- `operation`：`translate_rigid`、`translate_then_bounded_adjust` 或等待人工批准；
- `preferred_region`：结构语义，如“靠近 DC 输入连接器”“数字核心区”“远离天线”；
- `allow_member_delta_mil`：允许局部成员偏离预布局相对位置的最大值；刚性块为 `0`。

电气规则使用器件集合和明确意图。例如：

- `switching_power_loop`：输入电容、开关器件、电感与输出电容构成紧凑环路；
- `decoupling`：去耦电容贴近对应 IC 的电源脚，不以“靠近芯片中心”替代；
- `connector_edge`：连接器由机械决定，位置不可改变；
- `keepout_boundary`：功能块只能平移到指定区域，不能通过拆散成员绕开禁放区；
- `isolation_boundary`：禁止跨越强弱电、模拟数字、RF 或安规隔离边界。

避免只写“布局更好看”“尽量短”这类不可验证的指令；必须同时写明评价指标或不可违反的边界。

## 6. AI/EDA 后处理操作清单

1. 确认 `.adstatus` 以 `APPLIED` 开头，且 ClassPlacer 已退出。
2. 读取 handoff、brief、板框、Keepout、锁定件、关键网络和当前 DRC。
3. 列出每个功能块的候选区域、冲突和拟议操作；先报告，不写入。
4. 先执行刚性平移；每次变更后检查是否仍在板框内、未进入禁放区、未碰撞锁定件。
5. 仅在必要时执行 brief 允许的局部调整，并记录被移动的成员、偏移量和规则理由。
6. 运行 DRC，复核关键回路、高速/差分、模拟/RF、热与安规边界。
7. 输出变更报告，等待人工检查后再保存 PCB。

如果没有可行移动方案，AI 必须保留预布局、报告冲突并请求设计决策；不能自行拆散模块或移动锁定结构件。

仅当 Component Class 成员、中心器件，或晶振/去耦/反馈等局部电气拓扑发生变化时，才重新导出快照并运行插件。

## 7. 回退

ClassPlacer 的 `RestoreClassPlacement` 用于回到该次预布局应用前的位置。AI 后处理完成后，应在修改前额外保存一个 PCB 版本或 Git/AD 版本控制节点；不要把 AI 后处理与 ClassPlacer 的恢复点混为同一个回退层级。
