package pe.axiz.payment.domain;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record Payment(
    UUID id,
    String merchantId,
    Money money,
    String token,
    String idempotencyKey,
    String fingerprint,
    Status status,
    String reason,
    Instant createdAt) {
  public enum Status {
    ACCEPTED,
    REJECTED
  }

  public Payment {
    Objects.requireNonNull(id);
    Objects.requireNonNull(money);
    Objects.requireNonNull(status);
    Objects.requireNonNull(createdAt);
  }
}
