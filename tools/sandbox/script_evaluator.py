import ast
import logging
from typing import Any

logger = logging.getLogger("ScriptEvaluator")

class ScriptEvaluator:
    """脚本判定器：提供受限 Python 执行与 LLM 判定接口（非安全沙箱）。"""

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
        功能：以受限 AST 白名单求值条件表达式，不执行任意 Python 语句。
        入参：code；context。
        出参：bool。
        异常：执行异常时记录错误并降级返回 False。
        """
        # 安全边界说明：
        # 1) 本实现仅裁剪了 __builtins__，不是可信安全沙箱；
        # 2) Python 官方文档明确不建议把 exec 受限内置视作安全隔离；
        # 3) 调用方必须保证脚本来源可信，生产环境不要把它暴露给不可信输入。
        try:
            expr = ast.parse(code, mode="eval")
            return bool(self._eval_safe_ast(expr.body, context))
        except Exception as e:
            logger.error(f"Python 脚本执行失败: {e}\n代码:\n{code}")
            return False

    def _eval_safe_ast(self, node: ast.AST, context: dict[str, Any]) -> Any:
        """
        功能：递归求值受限 AST 节点。
        入参：node（ast.AST）：表达式节点；context（dict[str, Any]）：变量上下文。
        出参：Any，表达式计算结果。
        异常：遇到不允许语法或未定义变量时抛 ValueError/KeyError，由上层统一降级。
        """
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise KeyError(f"未定义变量: {node.id}")
            return context[node.id]
        if isinstance(node, ast.BoolOp):
            values = [bool(self._eval_safe_ast(v, context)) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
            raise ValueError("不支持的布尔操作")
        if isinstance(node, ast.UnaryOp):
            operand = self._eval_safe_ast(node.operand, context)
            if isinstance(node.op, ast.Not):
                return not bool(operand)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            raise ValueError("不支持的一元操作")
        if isinstance(node, ast.BinOp):
            left = self._eval_safe_ast(node.left, context)
            right = self._eval_safe_ast(node.right, context)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            raise ValueError("不支持的算术操作")
        if isinstance(node, ast.Compare):
            left = self._eval_safe_ast(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_safe_ast(comparator, context)
                if isinstance(op, ast.Eq):
                    passed = left == right
                elif isinstance(op, ast.NotEq):
                    passed = left != right
                elif isinstance(op, ast.Gt):
                    passed = left > right
                elif isinstance(op, ast.GtE):
                    passed = left >= right
                elif isinstance(op, ast.Lt):
                    passed = left < right
                elif isinstance(op, ast.LtE):
                    passed = left <= right
                elif isinstance(op, ast.In):
                    passed = left in right
                elif isinstance(op, ast.NotIn):
                    passed = left not in right
                else:
                    raise ValueError("不支持的比较操作")
                if not passed:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            cond = bool(self._eval_safe_ast(node.test, context))
            return self._eval_safe_ast(node.body if cond else node.orelse, context)
        if isinstance(node, ast.List):
            return [self._eval_safe_ast(elt, context) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval_safe_ast(elt, context) for elt in node.elts)
        if isinstance(node, ast.Dict):
            return {
                self._eval_safe_ast(k, context): self._eval_safe_ast(v, context)
                for k, v in zip(node.keys, node.values)
            }
        raise ValueError(f"不允许的语法节点: {type(node).__name__}")

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
