"""Nova Sonic voice per persona.

Nova Sonic voice ids replace the previous provider's (sage / ash / coral /
ballad). Which voices an account can use depends on the Nova Sonic model
version and region — confirm these against the Bedrock console before go-live
and adjust here (the API never accepts a voice outside this map).
"""

VOICE_MAP: dict[str, str] = {
    "alex_martinez": "tiffany",
    "michael_mike_alvarez": "matthew",
    "sarah_donnelly": "amy",
    "thomas_tom_caldwell": "matthew",
}

DEFAULT_VOICE = "matthew"
