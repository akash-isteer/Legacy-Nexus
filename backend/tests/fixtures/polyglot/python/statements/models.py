from sqlalchemy import Column, Numeric, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Statement(Base):
    __tablename__ = "STATEMENTS"

    account_id = Column(String(12), primary_key=True)
    month = Column(String(7), primary_key=True)
    total = Column(Numeric(11, 2))
