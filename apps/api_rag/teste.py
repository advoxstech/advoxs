import asyncio
import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = f"postgresql+asyncpg://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('DATABASE_HOST')}:{os.getenv('DATABASE_PORT')}/{os.getenv('POSTGRES_DB')}"

print("URL:", DATABASE_URL)

async def main():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name='documentos_sistema'"
        )
        for row in result:
            print(row)

asyncio.run(main())