package pe.axiz.settlement.domain;

import java.math.BigDecimal;
import java.util.UUID;

public record SettlementEvent(
    String eventId,
    UUID paymentId,
    String merchantId,
    BigDecimal amount,
    String currency,
    String status,
    String reason,
    int schemaVersion) {
  public SettlementEvent {
    if (eventId == null
        || paymentId == null
        || merchantId == null
        || amount == null
        || amount.signum() <= 0
        || !java.util.Set.of("PEN", "USD").contains(currency)
        || !java.util.Set.of("ACCEPTED", "REJECTED").contains(status)
        || schemaVersion != 1)
      throw new IllegalArgumentException("El evento de liquidación no es válido");
  }
}
