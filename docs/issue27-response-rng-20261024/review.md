# PyBERT #27 独立只读自审

固定候选 HEAD `81c4e5a0344ebaee069472e98f90bea823b7a467`，tree `b94eb8b9270b778893d669f8c53f6499a1d7f68a`；基线 F `e222e4094629e1ce9a2f8058ab42439fc21fadd8`。两名审查者均核对非空单 commit diff 与 clean 工作区，独立执行只读审查。根 owner 是唯一写入者。

## Standards

审查者 `/root/issue27_standards_review`：**hard 0 / actionable smell 0**。

生产改动只涉及 `get_responses` 可选 keyword-only RNG、文档和 None 选择；命名、类型、职责、导入与许可证符合仓库约定。58 个修改文件均为 UTF-8 without BOM，`git diff --check` 无输出；新测试/runner 的 Python 3.10 AST 和 119 列检查通过。测试、guard、固定输入、原始输出职责清楚，没有新增抽象需求。证据副本不视为重复实现。

唯一 trailing-whitespace hook 例外精确到首批失败 stderr；原 traceback 确有空格行，保留原件合理。文档如实区分 harness 失败、feature red、17 个软件测试、非零软件调用、真实模型零调用及未知成本。

## Spec

审查者 `/root/issue27_spec_review`：**missing/partial 0、scope creep 0、implemented wrong 0**。

独立 AST 核对：移除新增 rng 参数、两行说明及 None guard 后，整个 model AST 与 F 相同。提供的 Generator 直接使用，旧 positional 参数、默认 factory、两次 probe、响应算法和 B1/C0 均保持。现有 17 pass 覆盖合同要求；37 项原件/receipt/当前文件 hash 关系全部匹配。

01 bootstrap guard 失败、02 真正 API red、03/04 green 均保留。累计生产 `get_responses` 20、Python stub 28、response-key init/post-init 各 16，没有冒称软件调用为零。NumPy 观察范围、mypy 原有三项失败和 UNKNOWN 均有明确说明。远端 Draft PR 属随后的交付阶段，不计固定候选缺项。

## 审查边界及封存

两名审查者仅使用 stdlib/Git 只读检查，未执行产品 import、测试、runner/helper、NumPy、安装或模型。完整 formatter/lint/hooks 未运行；mypy 与基线相同的三项诊断保留，17 pass 不构成实际模型资格。

本报告、README 交付链接与 [verification.json](verification.json) 是后续封存文档增量；生产 source/test/runner/fixtures/environment 的 hash 必须仍与 04 receipt 一致。没有因文档封存重跑测试或重置成本。最终提交 HEAD/tree 和远端 Draft PR 由交付消息固定，避免在自身 manifest 中循环记录提交 hash。提交后停止写入，交 PM fresh review；合并、issue 关闭及 PyBERT SHA 迁移均留给后续授权。
