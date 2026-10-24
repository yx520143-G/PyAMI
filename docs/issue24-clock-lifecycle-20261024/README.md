# PyBERT #24：独立 PyAMI clocks / lifecycle 本地候选

2026-10-24。合同为 [live #24](https://github.com/yx520143-G/PyBERT/issues/24)，读取时正文/时间保存于 [issue-24.json](issue-24.json)。仅本地候选，不代表依赖已集成、发行或真实模型资格；父 #10 有效 clocks 与 E 资源仍 BLOCKED。

## 来源与范围

- 独立 clone：`E:/serial-bus-simulator-dependencies/pyami-clock-lifecycle`，分支 `codex/pyami-clock-lifecycle`。
- 基线 HEAD `5270d713e40bac2be5747bef09b92df79df9085b`，tree `35b09659b5be907204823a82f95afdc9ec63d0a8`，启动 clean。使用本地只读旧 PyAMI repo、`clone --no-hardlinks --no-checkout` 后显式 switch；独立 `.git`，无 alternates。未改旧 repo 的配置/ref/index/worktree。
- 产品修改只在 `src/pyibisami/ami/model.py`；新增定向 `tests/ami/test_clock_lifecycle.py` 及本证据目录。原作者/许可保留；不改版本号、依赖、锁、PyBERT gitlink/源码或封存设计目录。
- 本候选默认 strict 和公开 close() 是 #24 明确批准的局部分支行为。get_responses/rng、probe隔离、D/Web/归档、真实模型、fork/push/安装迁移均未实施；get_responses 与基线 AST 相同。

## 对外合同与兼容影响

`getWave(wave,bits_per_call=0)` 仍返回 `(wave, valid_edge_clocks, params_list)`；波形/params 原值及顺序、非零分块设置和0继承已有分块保持。每个 C boundary 前整 clock buffer 填 NaN；只接收首个精确 -1 前的 finite、非负、严格递增 ticks，0合法。sentinel之后即使是NaN/Inf/负值也不属于prefix。合法无clocks返回空数组；数量不按waveform UI截断、补齐、排序或筛正值；短末块仍按实际wave_size调用。

last tick跨多次Python getWave属于同一Init epoch，合法空prefix和空wave都不清它。C GetWave返回码只接受1。错误/缺sentinel/重复/倒退/非有限prefix或callback异常后epoch禁用，不能继续或发布部分成功三元组；需要显式成功initialize才能恢复。失败不自动重试或隐式C Close，调用方仍负责finally close。

**这是所有getWave调用的兼容变化**，没有use_clocks例外；Tx、response probes以及Init helper内部的GW probes也会strict失败，即便调用方丢弃clocks。不新增waveform-only容忍。此候选返回的clocks不再带padding/sentinel，原错误也可能变为 `AMIModelError(RuntimeError)`；原OSError等在 `__cause__` 保留，旧 `except OSError` 不会直接捕获新外层。当前PyBERT没有切换到此候选。

`B+8` 仅保留历史容量兼容值，**不是任意DLL的容量上界或内存安全保证**。事后prefix检查无法撤销真实DLL已造成的越界；未知容量的未来fixture仍不得执行真预检。ABI ctypes/opaque handle类型、非整数UI/ts、分块默认与DSP算法不在本票内。

## Init / Close / 析构

`close()` 先撤销wrapper handle和ready标记，再用旧handle调用C Close；重入/重复close和析构不会再次释放。无handle不调用C Close，不擦掉先前close失败原件。Close异常抛AMIModelError，原异常在cause；记录returned=false/release_status=unknown，不自动重试或声称资源已释放。返回成功也只记录函数已返回（not_verified），不证明DLL内部资源状态。

initialize先通过同一close处理旧handle，再准备/能力检查和调用Init。准备失败不留下旧handle；Init非1或callback异常（包括产生非空handle后抛异常）清理新handle一次，保持不可getWave。Init及其cleanup同时失败时，主异常cause保留原失败，`cleanup_error`保留清理异常/cause。成功Init之后才重置last tick、Python/C call计数和poison状态，开放getWave；合法stateless/null-handle成功也可使用。私有 `_initialize` 仅保留原准备/ABI调用，与public initialize的ready/cleanup职责分开。

析构复用close，捕获并通过模块logger记录cleanup异常，不向析构外抛错；半构造对象也安全。没有新增context-manager或通用事件/插件框架。

## 最小观察位置与失败原件

- `last_getwave_calls`：最近一次进入ready epoch的Python getWave所包含的每个C call；被precondition拒绝的新尝试保留前次原件。epoch/python_call/c_call、status/returned、input_wave、返回wave、完整capacity clock_buffer、原始params bytes、成功valid_clocks或错误原因。属性返回深拷贝，全部为host-owned Python数值/tuple/bytes，无C buffer别名。
- `last_init_call`：closing_previous / preparing / calling / configuring_getwave / ready阶段、返回状态、调用前IR/rows/ts/UI/params、返回IR/params/msg、是否得到handle、错误/cleanup。epoch为Init尝试标识，成功才建立可用的时钟状态。
- `last_close_call`：epoch、returned、资源释放未知/未验证、原异常信息。空操作不覆写前一失败。
- `AMIModelError.diagnostic`：当前失败边界快照；`__cause__`与`cleanup_error`保留异常链。GW先成功若干块再失败，所有块仍可从last_getwave_calls取出；不从它们构造成功三元组。

这些是内存观察，不是通用持久日志；调用方须在后续initialize/下一次有效getWave覆盖前复制所需原件。字节/NaN不能直接当有限JSON数据，本票不实现Web保存/版本合同。

## 定向红绿与guard

唯一运行入口为 [run_guarded.py](run_guarded.py)，只运行新增定向文件，禁用conftest、外部pytest插件自动加载、cache和Windows console capture。运行命令使用既有只读Python环境，`uv run --no-project --python <已记录的Python路径> python -B docs/issue24-clock-lifecycle-20261024/run_guarded.py <新的唯一run_id>`；现有run目录拒绝覆盖。

导入当前clone源码前封锁ctypes loader/CDLL及dlopen审计；生产AMIModel.__init__、grammar parser相关导入、get_responses、deconv/plot等helper也封锁。以AMIModel.__new__创建普通未初始化对象，真实initialize/getWave/close使用Python bound fake callbacks与普通ctypes buffers。参数串构造器是实际Python函数，不是grammar parser。receipt记录实际module/common路径、源码/测试/runner SHA、Python/NumPy/pytest环境、生产Python code profile及fake callback计数。没有CFUNCTYPE或真实C callbacks。

| 原始run | 结果与真实含义 |
|---|---|
| 01-prefix-red | harness启动失败：pytest colorama试图加载kernel32被guard拦截；尚未运行用例。完整终端trace、原runner/source/test及bootstrap说明保留。之后关闭capture插件，未放宽DLL guard |
| 02-prefix-red | 基线1失败：5个合法ticks被截成2个；波形已通过；真实生产源码SHA与基线一致 |
| 03-prefix-green | 1通过：每call有效prefix不再被UI数截断 |
| 04-epoch-failure-red | 17失败/2通过：缺epoch跨call、严格status、poison与host-owned失败诊断 |
| 05-epoch-failure-green | 19通过 |
| 06-lifecycle-red | 13失败/19通过：close缺失、准备失败后双Close、Init状态/异常清理等 |
| 07-lifecycle-green | **实际1失败/31通过，不算green**：capability测试错误写入未被读取的私有字段；保留原件，随后改为真实initializer.info_params |
| 08-lifecycle-green | 修正用例后32通过 |
| 09-boundary-green | 增补null-handle失败、空wave/空prefix、未Init拒绝、原始参数/IR观察、空params兼容；37通过 |
| 10-final-green | 类型注解/新增文件行宽修正后37通过；本地候选最终软件结果，仍不是模型资格 |

所有 `runs/*` 的stdout/stderr、receipt、source/test/runner快照保留原bytes；pytest本身的断言显示截断仍按原输出保留。最终37项为同一套覆盖，不能把各轮通过次数相加作覆盖。测试的fixture结束只清除Python fake handle以防GC假回调污染，不代表模型资源清理；Close行为由对应真实方法用例验证。

最终run生产Python initialize48/getWave50/close113（另有析构43等），**fake Init45/GetWave33/Close22**。它们均非0，也不是实际AMI C调用；全历史9次guarded pytest的聚合在 [cost-ledger.json](cost-ledger.json)。所有真实Main/COM/coopt、C Init/GW/Close、CDLL/引擎、原生数值helper、AMI grammar parser、模型下载编译始终0。

## 静态检查、自审与成本

Python3.10 AST语法检查通过；get_responses AST与基线相同；只改必要model方法。targeted mypy初次有6诊断：修正3个新增类型推导问题后，候选与baseline shadow均只剩完全相同的3项原有诊断（缺matplotlib stub、get_responses的s_init/h_getw注解）。保存3轮mypy完整输出/退出码，不称全type-check通过；未补装可选包或修改无关方法。

原repo的“session end全tests”和自动lint/type文案服从#24的0真实预算，不运行现有会加载DLL的test_init或全套；没有实际Git hooks。完整lint未运行，ruff不可用；不为格式重写相邻原代码。自身新行检查及diff whitespace检查单独记录。

首次staged whitespace检查将原始CRLF源码快照的回车报为trailing whitespace；不修剪/重写原件。本目录属性仅对runs/**关闭whitespace解释，保留raw bytes；生产源码、测试及普通文档继续正常检查。该检查失败不算数值失败，原snapshot/hash不变。

9次有效guarded pytest内部elapsed合计 `5.178930399983074 s`；另有启动失败tool wall `1.8278724 s`（其内部elapsed UNKNOWN）、3次静态mypy内部elapsed合计 `13.945955499992124 s`，不可混作模型时间或任务wall。总任务wall/peak RSS UNKNOWN。失败批次、bootstrap拦截和静态失败全部留存，不删除费用或重命名成成功。完整fake/Python统计、源码hash及环境见各receipt与cost-ledger。

独立Standards/Spec只读复核及最终封存另记录；只认本地候选。固定最终HEAD/tree/clean后停写返权给PM fresh review，未push、fork、PR、安装或关闭issue。
