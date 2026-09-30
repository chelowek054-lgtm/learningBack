"""job.retry_after: отсрочка повтора после временного сбоя

Без неё задача, упавшая на сетевом сбое провайдера, сразу становилась `failed`
и навсегда: пользователь видел ошибку там, где хватило бы повтора через минуту.

Revision ID: 0010_job_retry_after
Revises: 0009_llm_usage
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010_job_retry_after"
down_revision: str | None = "0009_llm_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("retry_after", pg.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "retry_after")
