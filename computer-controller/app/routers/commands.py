from fastapi import APIRouter

router = APIRouter()

@router.get("/commands/{command}", tags=["command"])
async def read_user(command: str):
    print(f"{command}")

    return {"command": command}
