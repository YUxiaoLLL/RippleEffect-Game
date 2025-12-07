from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class Block(BaseModel):
    id: str
    footprint: List[List[float]]  # A list of [x, y] vertex pairs
    height: float
    use: str

class SceneState(BaseModel):
    blocks: List[Block] = []
    paths: List[Any] = []
    trees: List[Any] = []
    constraints: Dict[str, Any] = {}
    kpis: Dict[str, Any] = {}

class Action(BaseModel):
    action_type: str
    payload: Dict[str, Any]

class SceneUpdate(BaseModel):
    state: SceneState
    deltas: Dict[str, Any]
    kpis: Dict[str, Any]
    violations: List[str] = []

class SceneCard(BaseModel):
    snapshot: bytes
    deltas: Dict[str, Any]
    kpis: Dict[str, Any]
