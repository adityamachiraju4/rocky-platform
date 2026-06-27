from fastapi import FastAPI

app = FastAPI(
    title="Rocky API",
    version="0.1.0",
    description="Backend API for the Rocky AI Operating System",
)


@app.get("/")
def root():
    return {
        "message": "Welcome to Rocky",
        "status": "online",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }