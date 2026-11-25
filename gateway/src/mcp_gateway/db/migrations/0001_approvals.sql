-- Human-in-the-loop approval store.
--
-- The database itself enforces the invariants the gateway relies on, so they hold even
-- if application code is wrong:
--   * status moves only along the approval state machine (trigger);
--   * what was approved is what runs: the request payload is immutable (trigger);
--   * at most one pending request per identical call in a session (partial unique index);
--   * the audit trail is append-only (trigger).

CREATE TABLE approval_requests (
    id                  uuid PRIMARY KEY,
    tenant_id           text        NOT NULL,
    session_id          text        NOT NULL,
    requester_subject   text        NOT NULL,
    requester_role      text        NOT NULL,
    tool_name           text        NOT NULL,
    arguments           jsonb       NOT NULL,
    arguments_digest    text        NOT NULL,
    risk                text        NOT NULL,
    approver_roles      text[]      NOT NULL CHECK (cardinality(approver_roles) > 0),
    status              text        NOT NULL CHECK (status IN (
                            'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'EXPIRED',
                            'EXECUTING', 'EXECUTED', 'FAILED')),
    policy_name         text        NOT NULL,
    policy_digest       text        NOT NULL,
    policy_rule         text        NOT NULL,
    trace_id            text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    decided_at          timestamptz,
    decided_by_subject  text,
    decided_by_role     text,
    decision_reason     text,
    executed_at         timestamptz,
    result              jsonb,
    error               text,
    CHECK (expires_at > created_at),
    CHECK (status IN ('PENDING_APPROVAL', 'EXPIRED')
           OR (decided_at IS NOT NULL AND decided_by_subject IS NOT NULL))
);

CREATE UNIQUE INDEX approval_requests_one_pending_per_call
    ON approval_requests (tenant_id, session_id, tool_name, arguments_digest)
    WHERE status = 'PENDING_APPROVAL';

CREATE INDEX approval_requests_queue
    ON approval_requests (tenant_id, status, created_at DESC);

CREATE INDEX approval_requests_due
    ON approval_requests (expires_at)
    WHERE status = 'PENDING_APPROVAL';

CREATE FUNCTION approval_requests_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id                IS DISTINCT FROM OLD.id
       OR NEW.tenant_id         IS DISTINCT FROM OLD.tenant_id
       OR NEW.session_id        IS DISTINCT FROM OLD.session_id
       OR NEW.requester_subject IS DISTINCT FROM OLD.requester_subject
       OR NEW.requester_role    IS DISTINCT FROM OLD.requester_role
       OR NEW.tool_name         IS DISTINCT FROM OLD.tool_name
       OR NEW.arguments         IS DISTINCT FROM OLD.arguments
       OR NEW.arguments_digest  IS DISTINCT FROM OLD.arguments_digest
       OR NEW.approver_roles    IS DISTINCT FROM OLD.approver_roles
       OR NEW.created_at        IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at        IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'approval request % is immutable once created', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
        ('PENDING_APPROVAL', 'APPROVED'),
        ('PENDING_APPROVAL', 'REJECTED'),
        ('PENDING_APPROVAL', 'EXPIRED'),
        ('APPROVED',         'EXECUTING'),
        ('APPROVED',         'FAILED'),
        ('EXECUTING',        'EXECUTED'),
        ('EXECUTING',        'FAILED')
    ) THEN
        RAISE EXCEPTION 'illegal approval transition % -> %', OLD.status, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER approval_requests_guard
    BEFORE UPDATE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION approval_requests_guard();

CREATE TABLE approval_audit (
    id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id    uuid        NOT NULL REFERENCES approval_requests (id),
    at             timestamptz NOT NULL DEFAULT clock_timestamp(),
    from_status    text,
    to_status      text        NOT NULL,
    actor_subject  text,
    actor_role     text,
    detail         jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX approval_audit_by_request ON approval_audit (approval_id, id);

CREATE FUNCTION approval_audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'approval_audit is append-only'
        USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE TRIGGER approval_audit_append_only
    BEFORE UPDATE OR DELETE ON approval_audit
    FOR EACH ROW EXECUTE FUNCTION approval_audit_append_only();
