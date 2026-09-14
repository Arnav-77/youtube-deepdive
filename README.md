# YouTube Deep-Dive

Conversational RAG over YouTube video transcripts. Ask questions about a video
and get answers grounded in the transcript, with timestamp citations.

Minor project, B.Tech CSE, USICT (GGSIPU).

## Status

Phase 02 — thin end-to-end path. Currently: deployable API skeleton.

## Stack

FastAPI, PostgreSQL (Neon), Docker, deployed on Render.

## Running locally

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py


Then `curl http://localhost:8000/health`.

## Endpoints

- `GET /` — service identity
- `GET /health` — liveness check, returns the deployed commit SHA