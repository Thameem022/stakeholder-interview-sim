# Persona portraits

One head-and-shoulders render per persona, layered over that persona's backdrop
on the selection card (see `PersonaCard` in `src/App.tsx`) so the card previews
the interview instead of showing an empty room.

## Convention

    /personas/<persona_id>.png

matching the ids already used by `/avatars/<persona_id>.glb` and
`/background/<persona_id>_bg.*`:

    alex_martinez  michael_mike_alvarez  sarah_donnelly  thomas_tom_caldwell

## What the file has to be

- **Transparent PNG.** The backdrop shows through; a baked-in background hides it.
- **The same GLB avatar the student talks to.** A card face that doesn't match
  the interview face breaks the persona.
- **Head and shoulders**, framed like `cameraView: 'upper'` in `Avatar.tsx`.
- **Subject centred horizontally**, head near the top edge, shoulders running off
  the bottom — the card renders at `h-[115%]`, anchored bottom, so the lowest
  ~15% is cropped by design.
- **Portrait-ish aspect** (square to 3:4). ~800x1000 is ample; it displays around
  166x202 CSS px, so 800 covers a 2x screen.
- **Under ~300KB.** These four load together on the first screen.

A missing or unreadable file is not an error: the card falls back to the
backdrop alone.
