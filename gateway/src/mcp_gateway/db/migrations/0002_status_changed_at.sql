-- When the current status was entered: lets the recovery sweeper find approved calls that
-- a crashed gateway replica left in APPROVED (never started) or EXECUTING (interrupted).

ALTER TABLE approval_requests
    ADD COLUMN status_changed_at timestamptz NOT NULL DEFAULT now();

UPDATE approval_requests
SET status_changed_at = coalesce(executed_at, decided_at, created_at);

CREATE INDEX approval_requests_in_flight
    ON approval_requests (status, status_changed_at)
    WHERE status IN ('APPROVED', 'EXECUTING');
