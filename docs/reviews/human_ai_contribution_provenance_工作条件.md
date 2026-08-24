# 工作条件
- 主题: Human-AI Contribution Provenance for Agentic Software and Creative Work
- 档位: standard
- 目标字数: 6000–10000（可覆盖）
- 目标参考文献: 50–90（可覆盖）
- 最高原则: 不可偷懒/短视，需说明不确定性处理。

## ⚠️ 内容分离原则（防止 AI 流程泄露）

**重要**：本文件用于记录 AI 工作流程和方法学信息。
综述正文（{主题}_review.tex）必须完全聚焦领域知识，不应出现任何以下内容：
- ❌ '本综述基于 X 条初检文献、去重后 Y 条、最终保留 Z 篇'
- ❌ '方法学上，本综述按照检索→去重→评分→选文→写作的管线执行'
- ❌ 任何提及'检索'、'去重'、'相关性评分'、'选文'、'字数预算'等元操作的描述

这些方法学信息应记录在本文件的相应章节（Search Log、Relevance Scoring & Selection 等），
而**不是**放在综述正文中。目标是让读者感受不到这是 AI 生成的综述。

---

## Search Plan
- 主题聚焦：Human-AI Contribution Provenance for Agentic Software and Creative Work。
- 证据线：人机协作与 human agency；软件/数据 provenance；可审计日志与完整性；AIGC 归因/水印；AI 治理、隐私与责任。
- 查询：12 组英文查询，覆盖 provenance、audit trail、human oversight、co-creation、AI coding、authorship attribution、watermark、workflow provenance 等变体。
- 来源：OpenAlex（多查询主力，必要时由脚本配置的降级来源补充）；时间范围不设硬截断，以保留经典 provenance 形式模型与 2023--2026 年生成式 AI 研究。
- 语言：英文为主，纳入跨学科计算机、HCI、软件工程、治理、教育、医疗和数字出版研究。
## Search Log
- 12 个查询各返回 50 篇，合并后按上限截取 500 篇。
- 去重前 500 篇，去重后 494 篇；合并 6 条记录（其中 5 条跨 DOI 合并）。
- 494 条均保留标题、摘要、年份、作者、venue 和 DOI/URL 字段；入选 70 条的摘要覆盖率为 100%。
## Dedup
- 使用标题相似度、token Jaccard、年份窗口和 DOI 归一化去重。
- 映射：`.systematic-literature-review/artifacts/dedupe_map_human_ai_contribution_provenance.json`。
## Relevance Scoring & Selection
- 对每条记录的标题/摘要执行结构化语义锚点评分，按任务匹配度、方法匹配度、模态匹配度和应用价值组织 1--10 分判断；score >= 5 才分配子主题，并同步抽取 design、key_findings、limitations。该首轮评分用于高分优先和主题覆盖，不替代后续全文人工复核。
- 评分文件：`output/artifacts/scored_papers_human_ai_contribution_provenance.jsonl`；高分（>=7）7 条，中分（5--6.9）65 条，低分（<5）422 条。低分占比较高，反映检索故意包含治理、检测和邻近 provenance 证据；选文时仍按高分优先并保留能够支撑缺口论证的中分文献。
- 高分优先区间 60%--80%；按 standard 档位从 494 条中选取 70 条，覆盖 6 个主题簇，并剔除摘要缺失条目。
- 选文结果：`output/artifacts/selected_papers_human_ai_contribution_provenance.jsonl`；选文理由：`output/artifacts/selection_rationale_human_ai_contribution_provenance.yaml`；BibTeX：`human_ai_contribution_provenance_参考文献.bib`。
- 评分分布没有“全为 1.0”的保底异常；但自动评分使用了可解释的标题/摘要锚点，核心结论仍需在全文阅读和后续实证研究中复核。
## Data Extraction Table（数据抽取表）
- 数据抽取表：`output/reference/data_extraction_table.md`。
- 证据卡：`output/artifacts/evidence_cards_human_ai_contribution_provenance.jsonl`，摘要截断上限 800 字符。
- 选中文献的主要主题：贡献溯源/可审计性、人类主体性/协作、生成内容归因/水印、AI 治理/责任、安全/隐私与软件/工作流 provenance。
## Review Structure
- 引言：从 AI 产物检测转向人类主体性证据，提出 BAC 研究问题。
- 概念边界：作者、操作执行者、贡献主体；贡献事件与项目上下文。
- 人类主体性与人机协作：监督、评价、控制、认知贡献与跨模态记录。
- 技术溯源与可审计记录：形式 provenance、哈希链/日志、互操作性。
- 生成内容归因与检测：检测器/水印的能力边界及与过程证据的互补关系。
- 治理、责任与隐私安全：披露、第三方监督、最小披露和威胁模型。
- 讨论、展望、结论：五类研究缺口与可检验的 BAC 评测议程。
## Validation
- 目标：standard，正文 6,000--10,000（预算中点约 8,000），参考文献 50--90；正文固定包含摘要、引言、主体、讨论、展望、结论。
- 输出：`output/artifacts/word_budget_final.csv`、`non_cited_budget.csv`。
- 验证脚本将在正文完成后运行：`validate_counts.py`、`validate_review_tex.py`、`validate_subtopic_count.py`、`validate_no_process_leakage.py`、`generate_validation_report.py`。
- skill 环境缺陷记录：原始 `config.yaml` 位于 skill 安装目录，路径隔离检查拒绝从工作区外读取；已复制为工作区内 `review_config.yaml` 作为不改源码的 workaround。
