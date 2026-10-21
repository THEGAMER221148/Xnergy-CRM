from .main import app
from .public_demo import router
app.include_router(router)
from .public_demo_ai import router as public_ai_router
app.include_router(public_ai_router)
