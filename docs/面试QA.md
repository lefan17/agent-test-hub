# 面试高频问答（测试岗 + Agent 开发岗）

## 测试方向

**Q1：测试用例怎么设计的？**
正/反/边界/契约四个维度：正向主流程（登录成功、创建待办 201）、反向（错误密码 401、缺参 400）、边界（空 title 400、非法 status 400）、契约符合性（实现与 OpenAPI 不符即缺陷）。

**Q2：接口自动化一般怎么做断言？**
状态码 + 响应体关键字段双断言；本项目工具层返回结构化结果（status/body/耗时），用例层把「期望状态」与「实际状态」比对，失败留证据（响应体、请求参数、trace）。

**Q3：Flaky 测试怎么处理？**
两件事：① 执行层自动重试（5xx/网络错误，指数退避最多 3 次）并标记 retried；② 归因层把重试仍失败的归类为 flaky，不直接算 Bug——重试与归因分离，避免把不稳定误报成缺陷。

**Q4：为什么被测系统要注入缺陷？**
演示价值：Agent 自动测试的价值在于「发现缺陷」，全绿的演示没有说服力。注入典型缺陷（契约不符、校验缺失、不稳定服务）让项目能真实展示发现-归因-报告闭环；RAG 检索链路另有 6 个缺陷开关（分词缺失、排序升序、并列不稳定、重复命中、陈旧索引、宽松契约），覆盖「检索质量变差」这类不看状态码就发现不了的问题。

**Q5：pytest 里 xfail 是干嘛的？**
记录已知缺陷的回归基准：@pytest.mark.xfail(strict=True) 断言缺陷存在，如果哪天实现修好了，测试会变成 XPASS 并失败提醒——相当于缺陷回归看板。

**Q6：覆盖率怎么统计？**
不是代码覆盖率，是「接口/页面维度」的用例覆盖映射：每个端点（method+path）→ 覆盖它的用例 ID 列表，报告里可视化，便于发现没测到的端点。

## Agent 方向

**Q7：为什么用 LangGraph 而不是自己写 while 循环？**

先把**实际用到了什么**说清楚，再说为什么值：

- **用到了**：显式 `AgentState`（TypedDict）、节点化编排（planner / executor / retrier / critic / reporter 五个节点，每个都是纯函数，输入 state 输出增量）、**一处条件边**、**一条回边**、`stream(stream_mode="updates")` 的逐节点观测。
- **没用**：checkpointer（断点续跑）、`interrupt`（人工介入）、`Send`/subgraph（并行分发与子图）。这几项在 Q24 的「下一步」里，不是现在的能力。

**条件边与回边具体是什么**（这是这个问题的真正答案，见 `agenttest/graph/build.py`）：

executor 跑完后由 `_route_after_executor` 判断——若还存在「失败且状态码属于 5xx/无响应、且不是用例写错」的用例，并且补跑轮次没用完，就走向 `retrier`，`retrier` 再连回 `executor` 补跑**那一批失败的用例**（不重跑全量）；否则直接进 `critic`。轮次上限是 `Config.retry_rounds`（默认 1）。

**为什么需要跨轮补跑，而不是只靠 executor 内的重试**：executor 内部 `max_attempts` 的重试是**同一次执行里的连续退避**（0.3s / 0.6s / 1.2s），对「服务端偶发 503」常常在整个抖动窗口里连续失败；跨轮补跑把时间窗口拉开，`/api/flaky`（10% 触发）这种概率性失败才有机会在第二轮通过，不至于被误判成缺陷。同时刻意把轮次压到 1——轮次越多越像在用重试掩盖真缺陷，重试用尽的失败照常进归因。

**为什么这套比手写 while 循环好**：不是「因为 LangGraph 高级」，而是三件具体的事——① 状态是显式 TypedDict，节点是纯函数，可以单独测（`tests/test_graph_edges.py` 就是直接对图结构断言，不需要起服务）；② 补跑这个分支在图里是声明式的，读 `build.py` 一眼能看到有环，手写循环里这个 `while` 混在业务代码中，改起来容易漏；③ `stream_mode="updates"` 让每个节点的 trace 天然可观测，报告里能逐节点回放。

**已知局限（必须主动说）**：没有 checkpointer，所以**进程中断后无法断点续跑**，只能整轮重来。这是我下一步要补的第一项。

**Q8：planner/executor/critic/reporter 为什么这样切？**
遵循「决策与执行分离」：planner 出计划、executor 只执行、critic 归因、reporter 产出，职责单一；任何一步都可以换实现（比如 planner 从规则引擎换 LLM），不影响其他节点。

**Q9：Brain 抽象的价值？**
BaseBrain 协议同时被 MockBrain 和 LLMBrain 实现：无 Key/CI 环境用规则引擎保证确定性与速度，需要智能时切 LLM。面试点：AI 能力可插拔、降级方案、可测试性。

**Q10：LLM 输出不可控怎么办？**
用 with_structured_output 把输出约束成 Pydantic 模型（工具调用/JSON Schema），程序只消费结构化字段；同时做兜底：LLM 输出为空时回退 MockBrain 计划，保证流程不中断。

**Q11：Agent 会乱调用工具吗？**
工具面刻意做小：api_request、ui_check、fetch_openapi、rag_search 四个工具，每个都有清晰入参约束；执行层是确定性代码，LLM 只负责「选哪些用例、怎么归类」，可控性优先。

**Q12：为什么让 LLM 只做规划/归因，不直接让它写执行代码？**
测试系统第一要求是可复现：LLM 生成代码再执行，结果是概率性的，不适合做回归断言。让 LLM 做「决策」（理解契约、判断缺陷），执行走确定性工具——兼得智能与可靠。

**Q13：trace 是怎么实现的？**
LangGraph state 里定义 trace: Annotated[list, operator.add]，每个节点追加结构化日志；报告页直接渲染，演示时能完整回放 Agent 的每一步。

**Q14：模型用哪家的？**
OpenAI 兼容接口抽象（base_url + model + api_key），DeepSeek/OpenAI/通义/Kimi 都可以；演示环境用 MockBrain 保证零成本可跑。

**Q15：这个系统和普通测试框架（pytest+requests）的区别？**
普通框架要人写用例、人分析失败；本项目 Agent 自动生成计划、自动归因缺陷、自动出报告，人可以聚焦审查与修复。定位是「测试执行 + 智能分析」的平台化能力。

**Q16：如果被测系统没有 OpenAPI 文档呢？**
规划器可以退化：用规则引擎按常见 REST 模式生成冒烟用例，或让 LLM 根据页面/抓包信息推导；本项目契约缺失时 mock 计划仍可兜底。

**Q17：怎么保证重试不是掩盖 Bug？**
只有 5xx/网络错误才重试（业务 4xx 不重试），且报告里标记 retried 与 attempts；重试后仍失败照常进归因。

**Q18：UI 自动化为什么优先 Chrome 无头而非 Selenium？**
零驱动、零下载、离线可用、对 CI 友好；Selenium 作为可选后端保留，体现对不同场景的取舍判断。

**Q19：项目怎么验证自己是对的？**
**332 项 pytest**（本机实测：有 Key 全量 325 passed + 15 xfailed；`--ignore=tests/test_ui_tool.py` 321 passed + 15 xfailed；无 Key 时 4 条 LLM 冒烟 skip）：被测系统功能测试（含 15 项缺陷基准 xfail：API 与 RAG 检索缺陷面）、MockBrain 规划/归因单测、LLM 降级与缺陷关联用例白名单单测、工具集成测试（api_request / rag_search / rag_evaluate）、BM25 后端与检索指标纯函数单测、评测集端到端阈值判定（空标注 / 被测量缺失 / 召回为 0 都判失败）、coverage 端点映射声明与 `rag_tool` 真实调用的一致性、executor 断言求值层单测（API 双断言 / rag_status / 未实现断言必须失败（RAG、API、UI 三条求值路径）/ 指数退避 / 并发锁）、报告渲染测试、端到端测试（断言必须发现 contract+bug 两类缺陷）。

**图结构本身也在被测**（2026-10-24 新增 `tests/test_graph_edges.py`，8 条）：编译后的图里必须真的存在 `executor → retrier/critic` 条件分支与 `retrier → executor` 回边；补跑只跑失败用例不重跑全量；补跑清单被消费不死循环；`invalid_case` 不补跑；「第一轮 503 / 第二轮 200」时该用例被救回 PASS。**这组测试的意义是让「文档写了什么」和「代码做了什么」被机器盯住**——之前正是这两者不一致。

另有 4 条 UI 用例需要**能真正启动**的 Chrome，单独跑 `tests/test_ui_tool.py` 即可覆盖。

**能验证的和不能验证的，要分清**：上面这些验证的是**代码是否正确**，不是**判断质量有多好**。归因准确率（Agent 把失败归到 bug/contract/flaky 这几类，归对了几条）**本项目没有度量**，因为没做变异测试（mutmut / cosmic-ray）。这是真实缺口，别把「332 项 pytest」说成「我的 Agent 判断准确」。真要做，得先有变异体再谈归因准确率。

**Q20：RAG 检索链路怎么测？**
三层断言，而不是只看 HTTP 200：
① **契约层**：状态码（空 q / 非法 top_k 必须 400）、响应结构、`index` 元信息；
② **不变量层**：rank 必须 1..n 连续、score 单调非增、并列分数按 doc_id 升序（可复现性）、同一 doc_id 不得重复、已删除文档不得再被召回（ghost hit）；
③ **质量层**：用标注集算 recall@k 与 MRR，把「检索变差」变成数字。`LABELED_QUERIES`（7 条，钉住文档数字）：注入缺陷实现 recall@5=0.5714 / MRR=0.5714，修正实现两项都是 1.0000，差距全部来自中文查询召回为 0。另有 `RAG_EVAL_SET`（20 条）用于**阈值判定**：`rag_evaluate(min_recall=0.8)` 的结论写在 `quality` / `quality_ok` 里，缺陷实现 0.7000 判失败、正确实现 1.0000 判通过；空标注（`labeled_count == 0`）与被测量缺失都判失败——「指标无定义」不等于「指标达标」。

**Q21：为什么 BM25 自己写，不用 rank_bm25 / jieba？**
硬约束是零新增依赖 + 离线确定性可回归：BM25 的公式本身不长（idf + 词频饱和 + 文档长度归一），纯标准库几十行就能实现，还省掉第三方库版本与分词器行为带来的不确定性。中文分词用「字符 unigram + 相邻 bigram」的简化方案，不需要词典，也不用 jieba，并且恰好能把「分词缺失导致召回归零」这个真实缺陷暴露出来。

**Q22：RAG 的 embedding 后端怎么处理依赖与降级？**
embedding 后端是可选项：相关依赖（如 openai / numpy）**延迟导入**（只在真正调用时 import），缺依赖或未配置 Key 时抛 EmbeddingUnavailable，服务层捕获后**自动降级回 BM25** 并在 `GET /api/rag/index` 的 `fallback` 字段写明原因。因此 CI 与离线演示全程只走 BM25，硬依赖里没有任何检索库。

**Q23：检索质量的「缺陷归因」有什么区别？**
新增了 `relevance` 缺陷类别：检索失败不能笼统归成 test_issue（那是「测试自身的问题」）。排序/召回/索引一致性失败归 `relevance`，`top_k`/空 q 这类契约边界失败归 `contract`，去重失败归 `bug`——归因结果直接影响修复方向，归错类别等于白测。

**Q24：下一步想怎么扩展？**

按「补真实缺口」而不是「堆功能」排：

1. **接 LangGraph checkpointer 做断点续跑**——现在进程中断只能整轮重来，这是最实在的缺口。
2. **补归因准确率的度量**——引入变异测试（mutmut / cosmic-ray）造变异体，再统计 Agent 把失败归到正确类别的比例。现在「332 项 pytest」验证的是代码对不对，不是判断准不准，这两件事不能混。
3. **让 MockBrain 真的读 spec**——现在 `make_plan(goal, spec, context)` 的 `spec` 参数没有被使用，28 条用例是硬编码模板。不改成真的按 `spec.paths` 生成，就换不到别人的系统上。
4. **UI 换成 Playwright 做交互级流程**——现在 Chrome `--dump-dom` + BeautifulSoup 是字符串匹配，没有点击与输入。
5. 把报告推送到钉钉/飞书/企业微信；支持带鉴权系统与数据库断言。
6. RAG 侧接入真实 embedding 后端做召回率对比评测（BM25 vs 向量），并加检索延迟/首屏分位指标。

**明确没有的（别当成现有能力）**：`interrupt` 人工介入、`Send`/subgraph 并行分发、断点重放。这三项目前都不存在。
