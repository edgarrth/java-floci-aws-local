package pe.axiz.payment.domain;

import java.time.Instant;
import java.util.UUID;

public record Payment(
    UUID id,
    String merchantId,
    Money money,
    String token,
    String idempotencyKey,
    String fingerprint,
    Status status,
    String authorizationCode,
    Instant createdAt) {
  public enum Status {
    PENDING,
    AUTHORIZED,
    DECLINED,
    UNKNOWN
  }

  public boolean terminal() {
    return status == Status.AUTHORIZED || status == Status.DECLINED;
  }

  public Payment complete(Status result, String code) {
    if (terminal() && status != result)
      throw new IllegalStateException("El pago ya tiene una decisión final");
    if (result == Status.PENDING)
      throw new IllegalArgumentException("La decisión no puede ser PENDING");
    return new Payment(
        id, merchantId, money, token, idempotencyKey, fingerprint, result, code, createdAt);
  }
}
