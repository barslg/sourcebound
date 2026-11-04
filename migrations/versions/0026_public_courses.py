"""Public courses: an author flags a published course open to every signed-in user.

`courses.is_public` defaults to false. Every account that exists today and
holds no student grant gets the student role with NO course tags (the same
grant a first sign-in gives from now on): it reads only public courses.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

STUDENT_CODES = ("learn.read", "learn.answer", "learn.ask")


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        "INSERT INTO roles (name) SELECT 'student' WHERE NOT EXISTS"
        " (SELECT 1 FROM roles WHERE name = 'student')"
        " AND EXISTS (SELECT 1 FROM users)"
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code)"
            " SELECT r.id, p.code FROM roles r JOIN permissions p ON p.code = ANY(:codes)"
            " WHERE r.name = 'student' ON CONFLICT DO NOTHING"
        ).bindparams(sa.bindparam("codes", list(STUDENT_CODES), type_=sa.ARRAY(sa.Text())))
    )
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE name ="
            " 'student') AND NOT (permission_code = ANY(:codes))"
        ).bindparams(sa.bindparam("codes", list(STUDENT_CODES), type_=sa.ARRAY(sa.Text())))
    )
    op.execute(
        "INSERT INTO user_roles (user_id, role_id, scope_tags)"
        " SELECT u.id, r.id, '{}' FROM users u JOIN roles r ON r.name = 'student'"
        " WHERE NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id"
        " AND ur.role_id = r.id)"
    )


def downgrade() -> None:
    op.drop_column("courses", "is_public")
