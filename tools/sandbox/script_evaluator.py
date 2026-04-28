import logging
from typing import Any

logger = logging.getLogger("ScriptEvaluator")

class ScriptEvaluator:
    """安全脚本判定器：提供沙盒化的 Python 脚本执行和 LLM 提示词判定接口"""

    def __init__(self, llm: Any | None = None):
        """
        功能：初始化对象状态与依赖。
        入参：llm。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.llm = llm

    def evaluate_python_condition(self, code: str, context: dict[str, Any]) -> bool:
        """
        功能：在受限环境中执行 Python 代码。
        入参：code；context。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # 基础沙箱环境，仅允许内置安全函数和传入的上下文
        safe_globals = {
            "__builtins__": {
                "int": int, "float": float, "str": str, "bool": bool,
                "len": len, "abs": abs, "min": min, "max": max, "sum": sum,
                "any": any, "all": all, "dict": dict, "list": list
            }
        }

        try:
            # 使用 ast 预检查（可选，用于防止明显的攻击）
            # ast.parse(code)

            # 执行代码，结果必须赋值给变量 'result'
            exec(code, safe_globals, context)
            return bool(context.get("result", False))
        except Exception as e:
            logger.error(f"Python 脚本执行失败: {e}\n代码:\n{code}")
            return False

    def evaluate_llm_condition(self, prompt: str, history: str) -> bool:
        """
        功能：利用 LLM 对模糊剧情条件进行判定。
        入参：prompt；history。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not self.llm:
            logger.warning("LLM 未配置，无法执行 LLM 判定。默认返回 False。")
            return False

        full_prompt = f"""
你是一个严格的任务判定器。请根据提供的交互历史，判断以下条件是否已满足。
必须严格返回 'TRUE' 或 'FALSE'，不要有任何其他解释。

条件：{prompt}

交互历史：
{history}

判定结果："""

        try:
            response = self.llm.complete(full_prompt)
            result = str(response).strip().upper()
            return "TRUE" in result
        except Exception as e:
            logger.error(f"LLM 判定执行失败: {e}")
            return False
