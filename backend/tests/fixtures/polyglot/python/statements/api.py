"""Statement download API for online banking."""
from fastapi import APIRouter

from statements.service import StatementService

router = APIRouter()


@router.get("/statements/{account_id}")
def get_statement(account_id: str, month: str):
    return StatementService().build(account_id, month)
