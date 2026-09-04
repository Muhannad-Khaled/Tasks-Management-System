from fastapi import FastAPI

from app.api import health, projects

app = FastAPI(
    title="SOW-to-Project Execution Platform",
    description="Transforms a Scope of Work into a traceable, validated, PM-approved project plan.",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(projects.router)
