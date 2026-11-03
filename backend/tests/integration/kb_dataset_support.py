"""Builders the dataset integration tests share.

Postgres is real here, so every row needs a user, an organization and a
knowledge base before it needs a dataset. Those three are the same in every
file; writing them once keeps the tests about the thing they are testing.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_base import KBScope, KnowledgeBase
from app.db.models.organization import Organization
from app.db.models.user import User
from app.schemas.kb_dataset import DatasetFieldInput
from app.services.kb_dataset.model import create_dataset
from app.services.kb_dataset.versions import publish

ORDERS: list[DatasetFieldInput] = [
    DatasetFieldInput(name="订单编号", data_type="text", is_primary_key=True),
    DatasetFieldInput(name="客户编号", data_type="text"),
    DatasetFieldInput(name="金额", data_type="integer"),
]


async def base(db: AsyncSession, name: str = "orders") -> KnowledgeBase:
    """An organization with a dataset base in it."""
    owner = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x", is_active=True)
    db.add(owner)
    await db.flush()
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=owner.id
    )
    db.add(organization)
    await db.flush()
    kb = KnowledgeBase(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        scope=KBScope.ORG.value,
        collection_name=f"{name}-{uuid.uuid4().hex[:8]}",
        kb_type="dataset",
        embedding_model="text-embedding-3-small",
        embedding_dim=1536,
        embedding_provider="openrouter",
        organization_id=organization.id,
        owner_user_id=owner.id,
        visibility="org",
    )
    db.add(kb)
    await db.flush()
    return kb


async def dataset(
    db: AsyncSession,
    kb: KnowledgeBase,
    *,
    name: str = "订单明细",
    fields: list[DatasetFieldInput] | None = None,
) -> Any:
    return await create_dataset(
        db,
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        name=name,
        fields=fields if fields is not None else list(ORDERS),
    )


async def published(
    db: AsyncSession,
    dataset: Any,
    rows: list[dict[str, Any]],
    *,
    key: tuple[str, ...] = ("订单编号",),
) -> int:
    """Publish `rows` as the next version, and say which number it got."""
    result = await publish(db, dataset, rows, key_fields=list(key), kind="import")
    return result.version_no
