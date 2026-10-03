"""RLS policies, per-role grants (spec §5.4), and SECURITY DEFINER identity functions (spec §3.2, §4).

Model:
- `api` (owner surface): sees every row; policies `TO api USING (true)`.
- `bot` (Telegram worker): sees only rows whose customer_account_id equals the transaction-local
  setting `app.customer_account_id`. Unset → NULL → zero rows (fail closed).
- `reporter`: SELECT on a few owner-side tables; no customer data.
- Pre-scope identity lookups go only through the SECURITY DEFINER functions below; `bot` has no
  direct access to `customer_invites` and cannot write `telegram_identities`.

Grants are explicit here (not ALTER DEFAULT PRIVILEGES) so dev and test databases match.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables keyed by customer_account_id that the bot may see within its scope.
CUSTOMER_SCOPED = [
    "telegram_identities",
    "conversations",
    "messages",
    "payment_requests",
    "handoffs",
]
ALL_TABLES = [
    "owners",
    "owner_sessions",
    "owner_api_keys",
    "login_attempts",
    "customer_accounts",
    "customer_invites",
    "telegram_identities",
    "conversations",
    "messages",
    "payment_requests",
    "handoffs",
    "owner_actions",
    "daily_summaries",
    "stripe_events",
    "audit_log",
]
RLS_TABLES = [*CUSTOMER_SCOPED, "customer_accounts", "customer_invites", "audit_log"]


def upgrade() -> None:
    op.execute(
        """
        -- The bot's current customer, or NULL when no scope is set. Invalid values raise (fail closed).
        CREATE FUNCTION app_current_customer() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT nullif(current_setting('app.customer_account_id', true), '')::uuid
        $$;
        """
    )

    # ---- RLS on, forced (owner/admin is superuser and bypasses anyway; FORCE guards future owners)
    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {t}_api ON {t} TO api USING (true) WITH CHECK (true)")

    for t in CUSTOMER_SCOPED:
        op.execute(
            f"""
            CREATE POLICY {t}_bot ON {t} TO bot
                USING (customer_account_id = app_current_customer())
                WITH CHECK (customer_account_id = app_current_customer())
            """
        )
    op.execute(
        """
        CREATE POLICY customer_accounts_bot ON customer_accounts TO bot
            USING (id = app_current_customer());

        -- Bot may only append audit rows about its own customer; it can never read the log.
        CREATE POLICY audit_log_bot_insert ON audit_log FOR INSERT TO bot
            WITH CHECK (customer_account_id = app_current_customer());
        """
    )

    # ---- Grants
    tables = ", ".join(ALL_TABLES)
    op.execute(f"REVOKE ALL ON {tables} FROM PUBLIC")
    crud = [t for t in ALL_TABLES if t not in ("messages", "audit_log")]
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(crud)} TO api")
    op.execute("GRANT SELECT, INSERT ON messages, audit_log TO api")

    op.execute(
        """
        GRANT SELECT ON customer_accounts, telegram_identities TO bot;
        GRANT SELECT, INSERT ON conversations, messages, handoffs TO bot;
        GRANT UPDATE (last_message_at, status) ON conversations TO bot;
        GRANT SELECT, INSERT, UPDATE ON payment_requests TO bot;
        GRANT INSERT ON audit_log TO bot;

        GRANT SELECT ON daily_summaries, owner_actions, stripe_events TO reporter;
        """
    )

    # ---- SECURITY DEFINER identity functions (owned by admin, who bypasses RLS)
    op.execute(
        """
        CREATE FUNCTION redeem_customer_invite(
            p_token_hash text,
            p_telegram_user_id bigint,
            p_telegram_chat_id bigint,
            p_telegram_username text DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            v_invite_id uuid;
            v_account_id uuid;
        BEGIN
            SELECT i.id, i.customer_account_id INTO v_invite_id, v_account_id
              FROM public.customer_invites i
              JOIN public.customer_accounts a ON a.id = i.customer_account_id
             WHERE i.token_hash = p_token_hash
               AND i.used_at IS NULL
               AND i.expires_at > now()
               AND a.status = 'active'
             FOR UPDATE OF i;
            IF NOT FOUND THEN
                RETURN NULL;  -- unknown, used, expired, or disabled account: same answer
            END IF;

            UPDATE public.customer_invites
               SET used_at = now(), used_by_telegram_user_id = p_telegram_user_id
             WHERE id = v_invite_id;

            -- Re-linking revokes any previous link for this Telegram user.
            UPDATE public.telegram_identities
               SET revoked_at = now()
             WHERE telegram_user_id = p_telegram_user_id AND revoked_at IS NULL;

            INSERT INTO public.telegram_identities
                (customer_account_id, telegram_user_id, telegram_chat_id, telegram_username, invite_id)
            VALUES
                (v_account_id, p_telegram_user_id, p_telegram_chat_id, p_telegram_username, v_invite_id);

            RETURN v_account_id;
        END
        $$;

        CREATE FUNCTION resolve_telegram_identity(p_telegram_user_id bigint) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT ti.customer_account_id
              FROM public.telegram_identities ti
              JOIN public.customer_accounts a ON a.id = ti.customer_account_id
             WHERE ti.telegram_user_id = p_telegram_user_id
               AND ti.revoked_at IS NULL
               AND a.status = 'active'
        $$;

        CREATE FUNCTION revoke_telegram_identity(p_telegram_user_id bigint) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            UPDATE public.telegram_identities
               SET revoked_at = now()
             WHERE telegram_user_id = p_telegram_user_id AND revoked_at IS NULL;
            RETURN FOUND;
        END
        $$;

        REVOKE ALL ON FUNCTION redeem_customer_invite(text, bigint, bigint, text),
                               resolve_telegram_identity(bigint),
                               revoke_telegram_identity(bigint) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION redeem_customer_invite(text, bigint, bigint, text),
                                  resolve_telegram_identity(bigint),
                                  revoke_telegram_identity(bigint) TO bot;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS redeem_customer_invite(text, bigint, bigint, text);
        DROP FUNCTION IF EXISTS resolve_telegram_identity(bigint);
        DROP FUNCTION IF EXISTS revoke_telegram_identity(bigint);
        """
    )
    for t in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_api ON {t}")
        op.execute(f"DROP POLICY IF EXISTS {t}_bot ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_log_bot_insert ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS app_current_customer()")
    tables = ", ".join(ALL_TABLES)
    op.execute(f"REVOKE ALL ON {tables} FROM api, bot, reporter")
