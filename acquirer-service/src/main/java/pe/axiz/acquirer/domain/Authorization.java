package pe.axiz.acquirer.domain;

import java.math.BigDecimal;
import java.util.UUID;

public record Authorization(
    UUID paymentId, String merchantId, BigDecimal amount, String currency, String token) {
  public Authorization {
    if (paymentId == null
        || merchantId == null
        || amount == null
        || amount.signum() <= 0
        || token == null
        || !token.matches("tok_[a-zA-Z0-9_-]{1,80}")
        || !merchantId.matches("[a-zA-Z0-9-]{1,40}")
        || currency == null
        || !java.util.Set.of("PEN", "USD").contains(currency)
        || amount.compareTo(new BigDecimal("1000000")) > 0)
      throw new IllegalArgumentException("La autorización no es válida");
    try {
      amount = amount.setScale(2, java.math.RoundingMode.UNNECESSARY);
    } catch (ArithmeticException ex) {
      throw new IllegalArgumentException("Se admiten dos decimales monetarios", ex);
    }
  }
}
