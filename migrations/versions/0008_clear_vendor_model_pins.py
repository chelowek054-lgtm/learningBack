"""Снять вендорные слаги моделей с рубрик.

Колонка `rubric.model` означает «закрепить за рубрикой конкретную модель;
пусто — взять из конфига». Пока скоринг шёл через ClaudeAIGateway, в ней лежали
слаги Anthropic (`claude-opus-4-8`). Гейтвей удалён, и такие строки уже не
описывают ничего существующего: OpenAI-совместимый провайдер отвечает на них
`400 Model not found`, то есть скоринг падает на любой заведённой ранее базе.

Пересев не помогает: seed запускается руками, а миграции — всегда.

Revision ID: 0008_clear_vendor_model_pins
Revises: 0007_srs_concept_link
"""

from alembic import op

revision = "0008_clear_vendor_model_pins"
down_revision = "0007_srs_concept_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Только слаги удалённого вендора: осознанное закрепление другой модели
    # (её вправе поставить куратор рубрики) трогать нельзя.
    op.execute("UPDATE rubric SET model = '' WHERE model LIKE 'claude-%'")


def downgrade() -> None:
    # Восстанавливать нечего: модели, на которые указывали эти слаги, недоступны.
    pass
