from __future__ import annotations

from tools.roll.dice_roller import check_success, roll_d20, roll_d100, roll_dice


class _FixedRng:
    """
    功能：为掷骰测试提供可预测随机数输出。
    入参：values（list[int]）：按顺序返回的数值序列。
    出参：_FixedRng，可被 dice_roller 作为 rng 注入。
    异常：序列耗尽时抛出 RuntimeError，避免静默测试失真。
    """

    def __init__(self, values: list[int]) -> None:
        """
        功能：初始化固定输出序列。
        入参：values（list[int]）：返回值序列。
        出参：None。
        异常：不抛异常。
        """
        self._values = values
        self._index = 0

    def randint(self, _left: int, _right: int) -> int:
        """
        功能：按序返回预置值，模拟 randint。
        入参：_left（int）：下界（仅占位）；_right（int）：上界（仅占位）。
        出参：int，预置随机值。
        异常：当序列耗尽时抛 RuntimeError。
        """
        if self._index >= len(self._values):
            raise RuntimeError("fixed rng sequence exhausted")
        current = self._values[self._index]
        self._index += 1
        return current


class _RecordingRng:
    """
    功能：记录 randint 调用边界，并按顺序返回预置值。
    入参：values（list[int]）：按顺序返回的数值序列。
    出参：_RecordingRng，可用于验证每种骰型的上下界。
    异常：序列耗尽时抛出 RuntimeError。
    """

    def __init__(self, values: list[int]) -> None:
        """
        功能：初始化返回值与调用记录。
        入参：values（list[int]）：返回值序列。
        出参：None。
        异常：不抛异常。
        """
        self._values = values
        self._index = 0
        self.calls: list[tuple[int, int]] = []

    def randint(self, left: int, right: int) -> int:
        """
        功能：记录 randint 上下界并返回预置值。
        入参：left（int）：下界；right（int）：上界。
        出参：int，预置随机值。
        异常：当序列耗尽时抛 RuntimeError。
        """
        if self._index >= len(self._values):
            raise RuntimeError("recording rng sequence exhausted")
        self.calls.append((left, right))
        current = self._values[self._index]
        self._index += 1
        return current


def test_roll_d20_and_d100_apply_modifier() -> None:
    """
    功能：验证 roll_d20/roll_d100 会在掷骰结果基础上叠加 modifier。
    入参：无。
    出参：None。
    异常：断言失败表示基础掷骰计算回归。
    """
    rng = _FixedRng([7, 42])
    assert roll_d20(modifier=3, rng=rng) == 10
    assert roll_d100(modifier=-2, rng=rng) == 40


def test_roll_dice_case_insensitive_and_per_die_modifier() -> None:
    """
    功能：验证 roll_dice 对骰型大小写不敏感，且 modifier 会应用到每一颗骰子。
    入参：无。
    出参：None。
    异常：断言失败表示批量掷骰计算语义退化。
    """
    rng = _FixedRng([1, 6, 3])
    assert roll_dice("D6", count=3, modifier=2, rng=rng) == [3, 8, 5]


def test_roll_dice_returns_empty_for_unknown_type_or_zero_count() -> None:
    """
    功能：验证未知骰型与零数量场景返回空列表，不产生脏结果。
    入参：无。
    出参：None。
    异常：断言失败表示边界分支回归。
    """
    rng = _FixedRng([5, 5, 5])
    assert roll_dice("d999", count=3, modifier=10, rng=rng) == []
    assert roll_dice("d20", count=0, modifier=10, rng=rng) == []
    assert roll_dice("d20", count=-2, modifier=10, rng=rng) == []


def test_roll_dice_covers_all_supported_polyhedral_types() -> None:
    """
    功能：验证 roll_dice 支持 d8/d10/d12/d20/d100，并按骰型调用正确上界。
    入参：无。
    出参：None。
    异常：断言失败表示支持骰型分支或 modifier 应用回归。
    """
    cases = [
        ("d8", 8, [2, 8], [1, 7]),
        ("d10", 10, [3, 10], [2, 9]),
        ("d12", 12, [4, 12], [3, 11]),
        ("d20", 20, [5, 20], [4, 19]),
        ("d100", 100, [6, 100], [5, 99]),
    ]
    for dice_type, upper_bound, raw_values, expected in cases:
        rng = _RecordingRng(raw_values)
        assert roll_dice(dice_type, count=2, modifier=-1, rng=rng) == expected
        assert rng.calls == [(1, upper_bound), (1, upper_bound)]


def test_check_success_uses_greater_or_equal_boundary() -> None:
    """
    功能：验证 check_success 的判定边界为 `roll >= dc`。
    入参：无。
    出参：None。
    异常：断言失败表示成功判定契约回归。
    """
    assert check_success(roll=10, dc=10) is True
    assert check_success(roll=9, dc=10) is False
