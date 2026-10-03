"""Schema: every v0 table (spec §5), constraints, and the messages consistency trigger.

RLS, grants, and SECURITY DEFINER functions are in 0002 so this file reads as plain DDL.

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- ---------------------------------------------------------------- owner side
        CREATE TABLE owners (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email           text NOT NULL,
            password_hash   text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_login_at   timestamptz
        );
        CREATE UNIQUE INDEX owners_email_lower_key ON owners (lower(email));

        CREATE TABLE owner_sessions (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id        uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            token_hash      text NOT NULL UNIQUE,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_seen_at    timestamptz NOT NULL DEFAULT now(),
            expires_at      timestamptz NOT NULL,
            revoked_at      timestamptz,
            user_agent      text,
            ip              inet
        );

        CREATE TABLE owner_api_keys (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id        uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            name            text NOT NULL,
            key_prefix      text NOT NULL,
            key_hash        text NOT NULL UNIQUE,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_used_at    timestamptz,
            revoked_at      timestamptz
        );

        CREATE TABLE login_attempts (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email           text NOT NULL,
            ip              inet,
            succeeded       boolean NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX login_attempts_email_created_idx ON login_attempts (lower(email), created_at);

        -- ---------------------------------------------------------------- customers
        CREATE TABLE customer_accounts (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            stripe_customer_id  text NOT NULL UNIQUE,
            display_name        text NOT NULL,
            email               text,
            status              text NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'disabled')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE customer_invites (
            id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_account_id         uuid NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
            token_hash                  text NOT NULL UNIQUE,
            expires_at                  timestamptz NOT NULL,
            used_at                     timestamptz,
            used_by_telegram_user_id    bigint,
            created_by                  uuid REFERENCES owners(id) ON DELETE SET NULL,
            created_at                  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE telegram_identities (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_account_id uuid NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
            telegram_user_id    bigint NOT NULL,
            telegram_chat_id    bigint NOT NULL,
            telegram_username   text,
            invite_id           uuid REFERENCES customer_invites(id) ON DELETE SET NULL,
            linked_at           timestamptz NOT NULL DEFAULT now(),
            revoked_at          timestamptz
        );
        -- One active link per Telegram user; history is kept as revoked rows.
        CREATE UNIQUE INDEX telegram_identities_active_user_key
            ON telegram_identities (telegram_user_id) WHERE revoked_at IS NULL;
        CREATE INDEX telegram_identities_account_idx ON telegram_identities (customer_account_id);

        -- ---------------------------------------------------------------- conversations
        CREATE TABLE conversations (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            channel             text NOT NULL CHECK (channel IN ('owner_web', 'customer_telegram')),
            customer_account_id uuid REFERENCES customer_accounts(id) ON DELETE CASCADE,
            owner_id            uuid REFERENCES owners(id) ON DELETE CASCADE,
            external_chat_id    text,
            title               text,
            status              text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            last_message_at     timestamptz NOT NULL DEFAULT now(),
            -- Exactly one actor, matching the channel. Owner conversations have a NULL
            -- customer_account_id, so the bot's RLS policy can never match them.
            CONSTRAINT conversations_actor_matches_channel CHECK (
                (channel = 'customer_telegram' AND customer_account_id IS NOT NULL AND owner_id IS NULL)
             OR (channel = 'owner_web' AND owner_id IS NOT NULL AND customer_account_id IS NULL)
            )
        );
        CREATE INDEX conversations_customer_idx ON conversations (customer_account_id, last_message_at DESC);
        CREATE INDEX conversations_owner_idx ON conversations (owner_id, last_message_at DESC);

        CREATE TABLE messages (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id     uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            -- Denormalized from the conversation so the RLS policy needs no join; the trigger
            -- below guarantees it matches.
            customer_account_id uuid,
            role                text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content             text NOT NULL DEFAULT '',
            status              text NOT NULL DEFAULT 'complete'
                                CHECK (status IN ('complete', 'interrupted', 'error')),
            tool_name           text,
            tool_payload        jsonb,
            llm_model           text,
            input_tokens        integer,
            output_tokens       integer,
            created_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX messages_conversation_created_idx ON messages (conversation_id, created_at);

        -- Runs as the inserting role (not SECURITY DEFINER), so under the bot's RLS a conversation
        -- belonging to another customer is invisible, the lookup yields NULL, and the insert fails.
        CREATE FUNCTION messages_check_customer() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            conv_customer uuid;
            conv_found boolean;
        BEGIN
            SELECT customer_account_id, true INTO conv_customer, conv_found
              FROM conversations WHERE id = NEW.conversation_id;
            IF conv_found IS NOT TRUE THEN
                RAISE EXCEPTION 'conversation % not visible', NEW.conversation_id
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.customer_account_id IS DISTINCT FROM conv_customer THEN
                RAISE EXCEPTION 'messages.customer_account_id must match its conversation'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER messages_check_customer
            BEFORE INSERT ON messages FOR EACH ROW EXECUTE FUNCTION messages_check_customer();

        -- ---------------------------------------------------------------- payments & handoffs
        CREATE TABLE payment_requests (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_account_id uuid NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
            conversation_id     uuid REFERENCES conversations(id) ON DELETE SET NULL,
            stripe_invoice_id   text NOT NULL,
            amount              bigint NOT NULL CHECK (amount > 0),
            currency            char(3) NOT NULL,
            hosted_url          text NOT NULL,
            status              text NOT NULL DEFAULT 'link_sent'
                                CHECK (status IN ('link_sent', 'paid', 'failed', 'expired', 'void')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX payment_requests_open_invoice_key
            ON payment_requests (stripe_invoice_id) WHERE status = 'link_sent';
        CREATE INDEX payment_requests_customer_idx ON payment_requests (customer_account_id, created_at DESC);

        CREATE TABLE handoffs (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_account_id uuid NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
            conversation_id     uuid REFERENCES conversations(id) ON DELETE SET NULL,
            reason              text NOT NULL CHECK (reason IN
                                ('amount_over_threshold', 'customer_requested', 'dispute', 'other')),
            stripe_invoice_id   text,
            amount              bigint,
            currency            char(3),
            summary             text NOT NULL DEFAULT '',
            status              text NOT NULL DEFAULT 'open'
                                CHECK (status IN ('open', 'acknowledged', 'resolved')),
            resolved_by         uuid REFERENCES owners(id) ON DELETE SET NULL,
            resolved_at         timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX handoffs_status_idx ON handoffs (status, created_at DESC);

        -- ---------------------------------------------------------------- owner actions etc.
        CREATE TABLE owner_actions (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id     uuid REFERENCES conversations(id) ON DELETE SET NULL,
            owner_id            uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
            api_key_id          uuid REFERENCES owner_api_keys(id) ON DELETE SET NULL,
            action_type         text NOT NULL CHECK (action_type IN
                                ('refund', 'create_invoice', 'send_invoice', 'create_payment_link')),
            params              jsonb NOT NULL,
            preview             text NOT NULL,
            status              text NOT NULL DEFAULT 'proposed' CHECK (status IN
                                ('proposed', 'confirmed', 'executed', 'failed', 'cancelled', 'expired')),
            idempotency_key     uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
            stripe_object_id    text,
            error               text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            expires_at          timestamptz NOT NULL,
            confirmed_at        timestamptz,
            executed_at         timestamptz
        );

        CREATE TABLE daily_summaries (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            summary_date    date NOT NULL,
            timezone        text NOT NULL,
            stats           jsonb NOT NULL,
            summary         text NOT NULL,
            llm_model       text NOT NULL,
            generated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (summary_date, timezone)
        );

        CREATE TABLE stripe_events (
            event_id        text PRIMARY KEY,
            type            text NOT NULL,
            livemode        boolean NOT NULL,
            payload         jsonb NOT NULL,
            received_at     timestamptz NOT NULL DEFAULT now(),
            processed_at    timestamptz,
            error           text
        );

        CREATE TABLE audit_log (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            actor_type          text NOT NULL CHECK (actor_type IN
                                ('owner', 'owner_mcp', 'customer', 'system', 'stripe')),
            actor_id            text,
            customer_account_id uuid,
            action              text NOT NULL,
            target              text,
            payload             jsonb,
            created_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_log_created_idx ON audit_log (created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS audit_log, stripe_events, daily_summaries, owner_actions, handoffs,
            payment_requests, messages, conversations, telegram_identities, customer_invites,
            customer_accounts, login_attempts, owner_api_keys, owner_sessions, owners CASCADE;
        DROP FUNCTION IF EXISTS messages_check_customer();
        """
    )
