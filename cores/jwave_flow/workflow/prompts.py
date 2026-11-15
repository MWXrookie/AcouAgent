"""All LLM prompts used by the workflow.

The prompts are maintained as Python constants so the runtime has no
dependency on an external workflow export.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

# node ids in the original Dify graph are kept in comments for traceability
REQUIREMENT_ANALYST_SYSTEM = ''
REQUIREMENT_ANALYST_USER = '\n\xa0\xa0\xa0 你是一个声学仿真需求分析师。你的任务是将用户的自然语言输入整理成结构化的仿真需求描述。\n\xa0\xa0\xa0 阅读用户输入：{query}，提取并整理以下信息：\n\xa0\xa0\xa0 1. 仿真类型？\n\xa0\xa0\xa0 2. 涉及介质？\n\xa0\xa0\xa0 3. 声源类型与频率？\n\xa0\xa0\xa0 4. 有无障碍物？\n\xa0\xa0\xa0 5. 仿真区域？\n\xa0\xa0\xa0 6. 期望输出？\n\xa0\xa0\xa0\xa0\xa0\xa0 注意：如果用户没提，请根据常识进行合理猜测，输出一段通顺的需求描述。\n\xa0'  # node 1783990970058 (LLM / 需求分析)

PARAM_EXTRACT_USER = '''你是一个 jwave 声学仿真参数提取器。根据用户需求和知识库内容，提取 JSON 参数。

用户需求：{query}
知识库参考：{kb_context}

只输出一个 JSON 对象，格式必须是：
{{"params_json": {{...原有仿真参数...}}, "skill_query": null 或 {{"scenario_type": "...", "parameters": {{...}}, "result_spec": null 或 {{...}}}}}}

skill_query 仅用于服务端技能检索，必须满足：
1. scenario_type 只能是 point_source_2d、point_source_3d、plane_wave_2d、initial_pressure_2d、interface_2d、attenuation_helmholtz_2d、heterogeneous_2d。
2. parameters 使用明确 SI 字段，例如 sound_speed_m_s、density_kg_m3、frequency_hz、source_frequency_hz、dx_m、grid_shape、t_end_s、pml_size。
3. 频域 Helmholtz 且用户明确结果口径时，result_spec 填写对应字段、分量、单位等；没有足够信息就填 null。
4. 禁止输出 trusted、quality_level、runtime_approved、code 或批准旗标。
5. 不能确定场景或参数不完整时，skill_query 必须为 null，不得猜测。

只输出 JSON，不要 Markdown、解释或其他前缀。'''  # node 1783991048409 (参数提取)

CODE_GENERATE_USER = '你是 jwave Python 代码生成器。\n\xa0\n任务：\n\xa0\n根据参考代码生成最终代码。\n\xa0\n规则：\n\xa0\n1. 必须保持参考代码结构不变。\n2. 只允许修改：\n\xa0\xa0 - sound_speed\n\xa0\xa0 - density\n\xa0\xa0 - source_frequency\n\xa0\xa0 - t_end\n\xa0\xa0 - positions\n\xa0\n3. 不允许新增函数。\n4. 不允许删除函数。\n5. 不允许修改变量名称。\n6. 不允许修改参数顺序。\n7. 不允许修改 API 调用方式。\n\xa0\n\xa0\n用户需求：\n\xa0\n{query}\n\n代码参考：\n{code_ref}\n\xa0\n输出要求：\n\xa0\n仅输出完整 Python 代码。\n\xa0\n代码运行时输出要求：\n1. 除机器自检行外，必须用 print 输出用户要求的实际数值结果；自检行不能是唯一输出。\n2. 用户要求二维场、快照或可视化时，必须使用 matplotlib 的 Agg 后端并将图片保存到 outputs/ 目录（调用 savefig，不要只调用 show()），同时 print 图片路径。\n3. 模态分析必须输出排序后的 (nx, ny) 与频率表；不要伪造时域传播变量。\n\xa0\n禁止输出：\n\xa0\n<think>\n</think>\nENDTHINKFLAG\nMarkdown\n解释\n分析\n自然语言\n\xa0\n输出内容必须从代码第一行开始，到代码最后一行结束。'  # node 1783991079665 (代码生成)

CODE_FIX_USER = '你是一个代码改正器。\n\n你会收到三段输入：\n- 需要改正的代码：{code_final}\n- 可用辅助信息：{context}\n- 代码的报错：{stderr}\n\n你的唯一任务是：修复代码错误，并返回修复后的完整代码。\n\n# 最重要规则：\n**输出必须是代码格式，要求能完成运行**\n\n# 严格规则：\n1. 只输出最终修复后的完整代码。\n2. 不输出任何解释、分析、注释说明、步骤、标题、前后缀文本。\n3. 不使用 Markdown 代码块包裹（不要 ```）。\n4. 保持原有功能不变，仅做修复错误所必需的最小改动。\n5. 必须结合“可用辅助信息”进行修复，不得臆造不存在的库、函数、参数。\n6. 如果有多种修复方式，选择最稳健且兼容原逻辑的一种。\n7. 输出必须是可直接替换原代码的版本。\n8. 保留并修正用户结果输出：必须 print 实际结果；需要图像时必须用 matplotlib.savefig 保存到 outputs/，不能把 __JWAVE_SELFCHECK__ 自检行当作唯一输出。'  # node 1784023398114 (代码纠错)

COMPLETION_REVIEW_USER = '''你是仿真需求完成度评审器。你只检查用户明确提出的交付物，不做代码质量审查，不提出额外优化建议。

用户问题：
{query}

需求分析：
{requirement}

程序答案（用户可读输出）：
{answer}

可用证据：
{evidence}

请严格输出 JSON，不要输出 Markdown 或解释：
{{"completed": true 或 false, "missing_requirements": ["明确缺失的用户交付物"], "feedback": "只针对明确缺失项给生成节点的具体修改意见；完成时为空字符串"}}

判定规则：
1. 先从用户问题中列出明确要求的交付物，再逐项寻找证据；不要扩大或猜测用户需求。
2. 只有某个明确要求的交付物没有出现在用户可读输出或生成文件中，才允许 completed=false。
3. stdout、输出文件、图像路径都算用户可见证据；已生成的文件无需再次打印文件内容。
4. 语义数值门已通过是结果有效性的辅助证据，但不能替代缺失的明确交付物。
5. 以下情况必须 completed=true：格式/措辞偏好、未要求的额外信息、可选优化、代码风格、
   调试信息、自检字段、无法证明但也没有明确缺失证据的内容。
6. 如果没有明确缺失项，必须 completed=true，missing_requirements=[]，feedback=""。
7. completed=false 时，missing_requirements 至少包含一项明确缺失的用户交付物，
   feedback 只能针对这些条目，不能提出新的要求。'''

TEMPLATE_TRANSFORM = '{% for item in arg1 %}\r\n{{ item.content }}\r\n{% endfor %}\r\n'  # node 1783990989034 (模板转换)

SKILL_GUIDANCE_CONTRACT = '''以下是网关返回的同场景技能约束，仅用于避免参数和实现偏离已验证范围。
- 不得声称已经复用技能代码或完成技能核验。
- 不得把约束中的参数改写成与用户明确要求冲突的值。
- 不得读取或输出数据库路径、可信旗标或内部 token。
- 最终结果仍必须通过 AST、executor 和确定性数值门。

受控技能约束：
{guidance}'''



def requirement_analyst_prompt() -> ChatPromptTemplate:
    """① 需求分析 — turn the raw query into a structured requirement."""
    return ChatPromptTemplate.from_messages(
        [
            ("system", REQUIREMENT_ANALYST_SYSTEM),
            ("human", REQUIREMENT_ANALYST_USER),
        ]
    )


def param_extract_prompt() -> ChatPromptTemplate:
    """④ 参数提取 — pull JSON parameters out of query + knowledge base."""
    return ChatPromptTemplate.from_messages([("human", PARAM_EXTRACT_USER)])


def code_generate_prompt() -> ChatPromptTemplate:
    """⑤ 代码生成 — write the jwave script from the reference code."""
    return ChatPromptTemplate.from_messages([("human", CODE_GENERATE_USER)])


def code_fix_prompt() -> ChatPromptTemplate:
    """⑦ 代码纠错 — repair a script given its stderr."""
    return ChatPromptTemplate.from_messages([("human", CODE_FIX_USER)])


def completion_review_prompt() -> ChatPromptTemplate:
    """⑥c 需求完成度评审 — compare the question with the executed answer."""
    return ChatPromptTemplate.from_messages([("human", COMPLETION_REVIEW_USER)])
