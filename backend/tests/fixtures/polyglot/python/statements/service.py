"""Builds monthly statements from the core payment ledger."""
from sqlalchemy.orm import Session

from statements.models import Statement

OVERDRAFT_LIMIT = 1000


class StatementService:
    def __init__(self, conn=None, session: Session | None = None):
        self.conn = conn
        self.session = session

    def build(self, account_id, month):
        cursor = self.conn.cursor()
        cursor.execute("SELECT PAYMENT_ID, AMOUNT FROM PAYMENT_LEDGER WHERE ACCOUNT_ID = %s", (account_id,))
        total = sum(row[1] for row in cursor.fetchall())
        if total > OVERDRAFT_LIMIT:
            total = total - OVERDRAFT_LIMIT
        self.session.add(Statement(account_id=account_id, month=month, total=total))
        return total

    def export(self, exporter, data):
        return exporter.render(data)


class PdfExporter:
    def render(self, data):
        return b"%PDF"


class CsvExporter:
    def render(self, data):
        return ",".join(data)
