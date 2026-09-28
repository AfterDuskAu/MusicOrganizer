# Step 01: Project setup

## Context
This is a fresh project; there's no earlier version. Version 0.1 is the **engine only**, in Python. The Mac app (SwiftUI) arrives in v0.2 and talks to the engine over JSON-RPC (see `docs/ENGINE_API.md`). The owner builds everything with Claude Code, and the Mac is an Intel iMac.

## Goal
Create the repository, folder layout, git setup and GitHub remote, so every later step has somewhere to live and CI can run.

## Do
1. Ask me:
   - the project name (suggest `MusicOrganizer`)
   - where the brief folder is, so its files can be copied in
   
   Create the project at `~/Developer/<name>/`.
2. Create this layout. `app-mac/` and `app-windows/` come later; don't create them.
   ```
   <name>/
     CLAUDE.md          (from the brief)
     README.md          short: what this is, how to set up .venv and run the engine
     docs/
       LIBRARY_CONTRACT.md   (from the brief)
       ENGINE_API.md         (from the brief)
       CHANGELOG.md          start with "0.1.0 — in progress"
     prompts/           (the brief's prompt files, so they're versioned with the code)
     engine/            created in step 02
   ```
3. `git init`, with a `.gitignore` covering:
   - Python: `__pycache__/`, `.venv/`, `*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`
   - macOS: `.DS_Store`
   - **Music and library data, anywhere in the repo:** `*.mp3`, `*.m4a`, `*.flac`, `*.wav`, `*.opus`, `*.ogg`, `*.webm`, `*.aac`, `*.sqlite`, `.musicorg/`
   - CSV files, except `engine/tests/data/*.csv` and `engine/tests/data/*.tsv`
   
   Audio fixtures are generated at test time, so no exception is needed for them.
4. **GitHub repository:** help me create one and push. Use the `gh` CLI if it's installed, otherwise give me the exact clicks.
   - Private repos on the free plan get a limited number of Actions minutes a month, and macOS runners use them several times faster than Linux. Check GitHub's current numbers and tell me.
   - Then recommend either **public** (the repo holds no music and no secrets) or **private** with CI limited to pushes to `main` and pull requests. Let me choose.
5. First commit: "Project setup".

## Rules
- Don't create any engine code yet. That's step 02.
- Don't touch anything outside `~/Developer/<name>/`, except reading the brief folder.

## Acceptance
- The folder exists with the layout above and the brief's files copied in.
- `git log` shows the first commit, and GitHub shows it too.
- `.gitignore` works: create dummy `x.mp3` and `x.csv` files in the root, check `git status` ignores them, then remove them.
