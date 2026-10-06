CREATE TABLE payments (
    id UUID PRIMARY KEY,
    merchant_id VARCHAR(40) NOT NULL,
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('PEN', 'USD')),
    token VARCHAR(90) NOT NULL,
    idempotency_key VARCHAR(80) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(12) NOT NULL CHECK (status IN ('ACCEPTED', 'REJECTED')),
    reason VARCHAR(40) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (merchant_id, idempotency_key)
);
CREATE TABLE outbox (
    id UUID PRIMARY KEY,
    payment_id UUID NOT NULL REFERENCES payments(id),
    destination VARCHAR(20) NOT NULL CHECK (destination IN ('SNS', 'KAFKA')),
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    UNIQUE (payment_id, destination)
);
CREATE INDEX outbox_pending ON outbox(next_attempt_at) WHERE published_at IS NULL;
