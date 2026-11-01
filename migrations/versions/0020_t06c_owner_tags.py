"""T06c: owner-qualified group tags for rows written before T06b, and accept pins.

T06b stores a group tag as `<owner id>:<tag>`; an invite or a student grant
written earlier still holds the plain name and would match nothing (or,
read the old way, every owner's group of that name). Upgrade qualifies
every plain group tag of an invite with its creator, and every plain tag of
a student grant with the creators of the invites that user accepted and
that carry it (a plain tag no accepted invite explains stays as it is). A
course id and an already qualified key are never touched, so running it
twice changes nothing. It also pins every currently accepted block to its
current version (a downgrade to 0018 drops the pin column; upgrading again
must not turn accepted blocks back into replayable holds).

Downgrade changes no data: a qualified key cannot be turned back into the
plain name without losing which owner it belonged to, and the previous
revision's code reads both forms.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

UUID_RE = "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"


def _plain(tag: str) -> str:
    return f"({tag} !~ {UUID_RE} AND position(':' in {tag}) = 0)"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE invites i SET
          course_tags = fixed.tags,
          scope = array_to_string(fixed.tags, ',')
        FROM (
          SELECT i2.id,
                 ARRAY(
                   SELECT DISTINCT x FROM (
                     SELECT CASE WHEN {_plain("t")} THEN i2.created_by::text || ':' || t
                                 ELSE t END AS x
                     FROM unnest(i2.course_tags) t
                   ) q ORDER BY x
                 ) AS tags
          FROM invites i2
          WHERE i2.created_by IS NOT NULL
            AND EXISTS (SELECT 1 FROM unnest(i2.course_tags) t WHERE {_plain("t")})
        ) fixed
        WHERE i.id = fixed.id
        """
    )
    op.execute(
        f"""
        UPDATE user_roles ur SET scope_tags = fixed.tags
        FROM (
          SELECT ur2.user_id, ur2.role_id,
                 ARRAY(
                   SELECT DISTINCT x FROM (
                     SELECT t AS x FROM unnest(ur2.scope_tags) t WHERE NOT {_plain("t")}
                     UNION ALL
                     SELECT k AS x FROM unnest(ur2.scope_tags) t
                       JOIN LATERAL (
                         SELECT k FROM invite_uses iu JOIN invites i ON i.id = iu.invite_id
                         CROSS JOIN unnest(i.course_tags) k
                         WHERE iu.user_id = ur2.user_id
                           AND right(k, length(t) + 1) = ':' || t
                           AND k ~ '^[0-9a-fA-F-]{{36}}:'
                       ) m ON true
                       WHERE {_plain("t")}
                     UNION ALL
                     SELECT t AS x FROM unnest(ur2.scope_tags) t
                       WHERE {_plain("t")} AND NOT EXISTS (
                         SELECT 1 FROM invite_uses iu JOIN invites i ON i.id = iu.invite_id
                         CROSS JOIN unnest(i.course_tags) k
                         WHERE iu.user_id = ur2.user_id
                           AND right(k, length(t) + 1) = ':' || t
                           AND k ~ '^[0-9a-fA-F-]{{36}}:')
                   ) q ORDER BY x
                 ) AS tags
          FROM user_roles ur2 JOIN roles r ON r.id = ur2.role_id
          WHERE r.name = 'student'
            AND EXISTS (SELECT 1 FROM unnest(ur2.scope_tags) t WHERE {_plain("t")})
        ) fixed
        WHERE ur.user_id = fixed.user_id AND ur.role_id = fixed.role_id
        """
    )
    op.execute(
        "UPDATE blocks SET accepted_version_id = current_version_id"
        " WHERE status = 'accepted' AND accepted_version_id IS NULL"
        " AND current_version_id IS NOT NULL"
    )


def downgrade() -> None:
    """No data change: see the module docstring."""
