from fastapi import APIRouter

from app.personas.prompt_assembly import get_available_personas
from app.personas.voices import VOICE_MAP

router = APIRouter()


@router.get("/personas")
async def list_personas():
    return get_available_personas()


@router.get("/voices")
async def list_voices():
    return [{"persona_id": k, "voice_id": v} for k, v in VOICE_MAP.items()]
