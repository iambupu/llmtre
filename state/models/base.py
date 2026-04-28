from pydantic import BaseModel, Field


class Stats(BaseModel):
    """基础属性契约"""
    strength: int = Field(default=10, description="力量：影响物理伤害和负重")
    agility: int = Field(default=10, description="敏捷：影响闪避、潜行和先攻")
    intelligence: int = Field(default=10, description="智力：影响魔法效果和解谜")
    constitution: int = Field(default=10, description="体质：影响最大生命值和抗性")

class ResourcePool(BaseModel):
    """生命与法力资源契约"""
    hp: int = Field(..., description="当前生命值")
    max_hp: int = Field(..., description="最大生命值")
    mp: int = Field(..., description="当前法力值")
    max_mp: int = Field(..., description="最大法力值")

class Requirement(BaseModel):
    """行为与物品使用条件契约"""
    min_strength: int = Field(default=0, description="最低力量要求")
    min_agility: int = Field(default=0, description="最低敏捷要求")
    min_intelligence: int = Field(default=0, description="最低智力要求")
