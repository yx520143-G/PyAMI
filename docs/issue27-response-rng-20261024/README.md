# PyBERT #27：get_responses 可选私有 RNG

2026-10-24。[live PyBERT #27](https://github.com/yx520143-G/PyBERT/issues/27) 的完整读取结果保存在 [issue-27.json](issue-27.json)。本票仅 PyAMI API 软件候选，不是模型资格，也没有进行下一次 PyBERT SHA 接入。

## 独立来源与最小行为变化

新独立 clone `E:/serial-bus-simulator-dependencies/pyami-response-rng`，分支 `codex/pyami-response-rng`，基线 F `e222e4094629e1ce9a2f8058ab42439fc21fadd8` / tree `b9bd99398a8893f0c3d301e7758eec8f9d3858d0`。从本人 fork HTTPS `clone --no-hardlinks --no-checkout` 后显式 switch，启动 clean；独立 `.git`、无 alternates、objects link count 1。[startup.json](startup.json) 引用不可变的旧保护清单并保存本轮状态；旧 #24/#25、子模块、环境、cache 和原件均不写入或清理。

只在 `AMIModel.get_responses` 新增 keyword-only `rng: Optional[np.random.Generator] = None`。调用方提供 Generator 时直接使用该对象产生 adaptation prefix，按原一次 `integers(...size=Ignore_Bits)` 推进其状态，不转换 seed、替换或重置。未传或显式 None 时，仍在原 GetWave 分支调用不带参数的 `default_rng()`；未进入该分支时不创建或消耗 RNG。Ignore_Bits=0 保留原空 prefix 行为；默认路径此时仍创建 factory 返回的 Generator，但空 draw 不推进状态。

旧五个 positional 参数、默认值、两个 probes 顺序、bits_per_call、oversampling、返回结构与所有 NumPy 响应计算不改。没有解释 PyBERT seed=0、新增 RNG 类型校验、改 B1 prefix/容量或 C0 生命周期，也没有做 C1 probe/main 状态隔离、D/Web/archive、PyBERT源码/依赖/gitlink/lock 或版本变更。

[ast-scope-check.json](ast-scope-check.json) 将候选中唯一 rng keyword 参数、两行文档及 `if rng is None` 恢复为基线形式后，**整个 model AST 与 F 相同**；其余方法、导入、Init/GetWave/Close、deconv/FFT 等不变。

## 预先固定的 seam、输入和 guards

live 合同明确批准 seam：直接调用实际生产 `AMIModel.get_responses(fake_self, ...)` 的 unbound 函数，self 为普通内存对象；仅其 getWave 是纯 Python stub。stub 记录实际输入和 block 设置，按两组手工周期数组返回波形/参数，没有模型/响应算法副本。Init_Returns_Impulse 固定 False，避免执行 deconv_same；没有用 fake constructor 冒充真实模型。

[fixtures.json](fixtures.json) 在首次产品 import/调用前生成并固定：NumPy `2.5.1` 下 seed 123/124/999 的已知位序列与 PCG64 状态，完整 probe 输入、两组手工波形，以及手算 step/difference/四 tap DFT 返回数组。准备阶段是独立的普通 NumPy：3 个 Generator、9 次 size8 draw，产品 import/函数调用 0。该准备的内部 elapsed UNKNOWN；没有用待测 get_responses 产生 oracle，也不在测试中重写响应算法。

[test-plan.json](test-plan.json)、[plan-amendment.json](plan-amendment.json)、[environment.json](environment.json) 及每批 plan/source/test/runner/fixtures 快照在执行前保存。运行入口为 [run_guarded.py](run_guarded.py)：只选 `tests/ami/test_response_rng.py`，禁用 conftest、外部 pytest plugins、cache/capture 和颜色；唯一 run_id 目录拒绝覆盖。

加载顺序明确为：先普通 ctypes/NumPy/pytest preload，再 loader/audit/import/profile guards，随后从本新 clone 导入 model/common，最后封锁真实模型构造/生产 initialize/GetWave/close/helper 并运行定向文件。preload 的通用平台/系统库活动未被完整 trace，不冒充其所有底层调用为 0。实际 module/common 的 `__file__` 与新 clone 严格核对，生产函数体没有复制或替换；module 内四个 response-key 常量真实构造并单独计账。

NumPy 数组/RNG及实际 get_responses 内 diff、convolve、FFT 等只作用于这些固定内存数组，属于明确授权的软件测试。真实模型库/C、AMIModel/Initializer 实例、生产 initialize/GetWave/close、deconv_same/其他原生 helper、grammar parser、引擎、Main/COM/coopt 均禁止。没有运行旧 DLL test_init、全套、#24旧37 tests、CLI、服务/GUI或浏览器，也没有安装依赖或额外下载/编译模型。

## 原始红绿和合同覆盖

| run | 实际结果 |
|---|---|
| 01-seed-red | **harness 启动失败，不是 feature red**。guard 误把现有 `_InitData` TypedDict 类体判为禁用方法，在 pytest 前拦截；常量真实构造4，get_responses/stub/模型0。原 runner/trace/receipt保留 |
| 02-seed-red | 真正 baseline red：1失败，`unexpected keyword argument 'rng'`；参数绑定失败，函数体进入数0，guard无拦截 |
| 03-seed-green | 最小 API/None 选择修改后，tracer用例1通过；生产get_responses2、wave stub4 |
| 04-contract-green | 完整17项通过；生产get_responses18、wave stub24、controlled default factory5，guard无拦截；source/test/runner为本地最终软件版本 |

完整覆盖包括：同seed独立 Generator 的两 probe 输入 bytes 重放；不同seed的已知样例；同一 Generator 两次请求按固定状态推进、下一 draw 连续；未传/None 的可追踪默认 factory；calc_getw=False/GetWave_Exists=False 的三种传参方式均不创建/消耗；Ignore_Bits=0；默认/显式Generator下 NumPy legacy global RNG 不变；旧五个 positional、keyword-only 限制及全部 GetWave 返回数组。GetWave 结果与手算固定数组对照，FFT tolerance 固定 `atol=1e-14, rtol=0`。

Init-response 分支被明确避开，没有执行/验证真实 deconv 算法；默认 fresh-entropy 行为通过未改的 default_rng() 源码与受控 factory 调用验证，不要求熵生成的两次输出相等。17 项是同一套覆盖，不把各轮通过次数相加。三次实际 pytest 批次均记录整个批次的 legacy global RNG 前后不变；01 尚未进入这一检查。

## 调用、费用和静态检查

[cost-ledger.json](cost-ledger.json) 保留四批/静态检查全部结果：累计实际进入生产 get_responses **20**，纯 Python wave stub **28**，controlled default factory **5**。四批模块 response-key 实际构造 **16**、post-init **16**，另有普通 key hash **76**；这些均非 0，也不是 AMI C/模型调用。

NumPy profiler 按 production / wave_stub / controlled_factory / outside_response 分开保存实际观察到的 Python/C entry events；03尚未观察 outside_response，04扩展此观察范围。production 合计可见 convolve14、rfft14、diff28 等，不能将这些事件相加冒充全部低层运算次数；该运行时的 Generator.integers 不提供直接 profiler entry，未被写成“调用0”。prefix draw 的单次推进/零大小行为由原始源码、实际输入和 Generator 状态断言约束。

四批内部 elapsed 合计 `15.395078000001376 s`（包含失败和preload）；两个静态 mypy 内部 elapsed 合计 `9.609895700006746 s`；clone另 `4.267618199999561 s`。这些不是任务 wall 或模型时间；任务总 wall/peak RSS UNKNOWN。01失败与02 red均保留原日志和费用，没有重命名成通过。

Python `3.13.15`、NumPy `2.5.1`、pytest `9.1.1`，复用既有只读解释器、不 sync/install。新测试/runner静态 AST3.10 与119列检查通过。targeted mypy baseline shadow/candidate 均 exit1，只有完全相同的3个原有诊断：缺可选 matplotlib stub、s_init/h_getw 注解；不称全type-check通过。black/isort/ruff/pre-commit工具不可用，未补装或宣称完整 lint/hooks通过。fullsuite/hook自动文案服从本票精确预算；实际无活动Git hooks。

所有 source/test/runner/fixture/stdout/stderr 原件以各批 hash 保持。`.gitattributes` 只对本目录 runs/static 关闭 Git whitespace 解释、保留字节；`.pre-commit-config.yaml` 仅精确排除 01 原始 stderr 的 trailing-whitespace 自动改写，其余 hook/文件规则保持。不存在大文件、EOF或mixed-line-ending的额外豁免。

只向本人 PyAMI fork 新分支提交唯一 Draft PR，明确 Refs yx520143-G/PyBERT#27，不写上游、不自行merge/close。独立自审见 [review.md](review.md)，最终静态封存与 hash 清单见 [verification.json](verification.json)；固定 HEAD/tree/clean 后停写交 PM fresh review；下一次PyBERT SHA迁移由PM另行决定。软件17pass不解除 #10有效clocks/E SOURCE_BLOCKED、#4默认COM预算gate或#1最终数值review。
