"""Админ-панель (sqladmin) — служебный веб-интерфейс к данным.

Вход только для `user.is_superuser`; суперпользователь заводится из CLI
(`scripts/createsuperuser.py`), как `manage.py createsuperuser` в Django.

Границы: ядро регистрирует свои таблицы и отдаёт `admin` модулям — предметные
представления живут в модулях (см. `modules/knowledge/admin.py`).
"""

from __future__ import annotations

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter
from starlette.requests import Request

from core.config import settings
from core.db import SessionLocal, engine
from core.models import (
    Activity,
    Job,
    Material,
    PasswordResetCode,
    Response,
    Rubric,
    SrsCard,
    User,
)
from core.security import verify_password

_SESSION_KEY = "admin_user_id"


class AdminAuth(AuthenticationBackend):
    """Сессия в подписанной cookie; пускаем только суперпользователей."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        with SessionLocal() as session:
            user = session.query(User).filter(User.email == email).first()
            if (
                user is None
                or not user.is_superuser
                or user.password_hash is None
                or not verify_password(password, user.password_hash)
            ):
                return False
            request.session.update({_SESSION_KEY: str(user.id)})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get(_SESSION_KEY)
        if not user_id:
            return False
        # Права проверяем на каждом запросе: снятый флаг должен закрывать доступ сразу.
        with SessionLocal() as session:
            user = session.get(User, user_id)
            return user is not None and user.is_superuser


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"
    category = "Ядро"
    column_list = [User.email, User.is_superuser, User.created_at]
    column_searchable_list = [User.email]
    column_sortable_list = [User.created_at, User.email]
    column_filters = [BooleanFilter(User.is_superuser, title="Администратор")]
    # Хеш пароля не редактируем руками: пароль меняется через auth-эндпоинты.
    form_excluded_columns = [User.password_hash]


class PasswordResetCodeAdmin(ModelView, model=PasswordResetCode):
    name = "Код восстановления"
    name_plural = "Коды восстановления"
    icon = "fa-solid fa-key"
    category = "Ядро"
    column_list = [
        PasswordResetCode.user_id,
        PasswordResetCode.code,
        PasswordResetCode.expires_at,
        PasswordResetCode.used_at,
        PasswordResetCode.attempts,
    ]
    can_create = False


class ActivityAdmin(ModelView, model=Activity):
    name_plural = "Активности"
    icon = "fa-solid fa-list-check"
    category = "Ядро"
    column_list = [Activity.module, Activity.type, Activity.connectivity, Activity.due_at]
    column_filters = [
        AllUniqueStringValuesFilter(Activity.module, title="Модуль"),
        AllUniqueStringValuesFilter(Activity.type, title="Тип"),
        AllUniqueStringValuesFilter(Activity.connectivity, title="Связность"),
    ]


class ResponseAdmin(ModelView, model=Response):
    name_plural = "Ответы (event log)"
    icon = "fa-solid fa-clock-rotate-left"
    category = "Ядро"
    column_list = [
        Response.user_id,
        Response.activity_id,
        Response.synced,
        Response.local_created_at,
    ]
    column_filters = [BooleanFilter(Response.synced, title="Синхронизирован")]
    column_sortable_list = [Response.local_created_at]


class SrsCardAdmin(ModelView, model=SrsCard):
    name_plural = "SRS-карточки"
    icon = "fa-solid fa-layer-group"
    category = "Ядро"
    column_list = [SrsCard.module, SrsCard.source, SrsCard.due_at]
    column_filters = [
        AllUniqueStringValuesFilter(SrsCard.module, title="Модуль"),
        AllUniqueStringValuesFilter(SrsCard.source, title="Источник"),
    ]
    column_sortable_list = [SrsCard.due_at]


class JobAdmin(ModelView, model=Job):
    name_plural = "Задачи (jobs)"
    icon = "fa-solid fa-gears"
    category = "Ядро"
    column_list = [Job.type, Job.status, Job.attempts, Job.updated_at]
    column_filters = [
        AllUniqueStringValuesFilter(Job.status, title="Статус"),
        AllUniqueStringValuesFilter(Job.type, title="Тип"),
    ]
    column_sortable_list = [Job.updated_at]


class MaterialAdmin(ModelView, model=Material):
    name_plural = "Материалы"
    icon = "fa-solid fa-book"
    category = "Ядро"
    column_list = [Material.module, Material.source, Material.title]
    column_searchable_list = [Material.title]
    column_filters = [
        AllUniqueStringValuesFilter(Material.module, title="Модуль"),
        AllUniqueStringValuesFilter(Material.source, title="Источник"),
    ]


class RubricAdmin(ModelView, model=Rubric):
    name_plural = "Рубрики"
    icon = "fa-solid fa-ruler"
    category = "Ядро"
    column_list = [Rubric.id, Rubric.version, Rubric.module, Rubric.model]
    column_filters = [AllUniqueStringValuesFilter(Rubric.module, title="Модуль")]


_CORE_VIEWS = [
    UserAdmin,
    PasswordResetCodeAdmin,
    ActivityAdmin,
    ResponseAdmin,
    SrsCardAdmin,
    JobAdmin,
    MaterialAdmin,
    RubricAdmin,
]


def setup_admin(app) -> Admin:
    """Смонтировать /admin: каркас + таблицы ядра + представления модулей."""
    from modules.knowledge.admin import VIEWS as KNOWLEDGE_VIEWS

    admin = Admin(
        app,
        engine,
        title="Praxis · админка",
        base_url="/admin",
        authentication_backend=AdminAuth(
            secret_key=settings.admin_session_secret or settings.jwt_secret
        ),
    )
    for view in [*_CORE_VIEWS, *KNOWLEDGE_VIEWS]:
        admin.add_view(view)
    return admin
