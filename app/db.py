import asyncpg
from contextlib import asynccontextmanager
from .config import get_settings

_pool: asyncpg.Pool | None = None

async def init_db():
    global _pool
    s=get_settings()
    _pool=await asyncpg.create_pool(s.database_url, min_size=2, max_size=20, command_timeout=30)

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool=None

def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialized")
    return _pool

@asynccontextmanager
async def tenant_tx(ctx):
    """Every request transaction gets RLS context. Empty investor id is safe."""
    async with pool().acquire() as con:
        async with con.transaction():
            await con.execute("select set_config('app.tenant_id',$1,true)", str(ctx["tenant_id"]))
            await con.execute("select set_config('app.user_id',$1,true)", str(ctx["user_id"]))
            await con.execute("select set_config('app.user_role',$1,true)", str(ctx["role"]))
            await con.execute("select set_config('app.investor_contact_id',$1,true)", str(ctx.get("investor_contact_id") or ""))
            yield con
