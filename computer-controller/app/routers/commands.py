from fastapi import APIRouter
import pyautogui
from enum import Enum

router = APIRouter()

class Command(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    SPACE = "space"
    RIGHT = "right"


@router.get("/commands/{command}", tags=["command"])
async def read_user(command: Command):
    pyautogui.press(command.value)




    return {"command": command}
