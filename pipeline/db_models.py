from sqlalchemy import Column, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("doc_id", name="uq_knowledge_documents_doc_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(120), nullable=False, index=True)
    category = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(255), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    embedding = Column(JSONB, nullable=False)