from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.orm import Session


async def get_session(request: Request) -> AsyncIterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
