package pe.axiz.settlement.domain;

import java.util.Objects;

public record PaymentAudit(String eventId, String payload, int partition, long offset) {
  public PaymentAudit {
    Objects.requireNonNull(eventId);
    Objects.requireNonNull(payload);
    if (partition < 0 || offset < 0)
      throw new IllegalArgumentException("Se requiere una posición válida del evento Kafka");
  }
}
