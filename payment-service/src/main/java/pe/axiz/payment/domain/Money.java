package pe.axiz.payment.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Set;

public record Money(BigDecimal amount, String currency) {
  public Money {
    if (amount == null || amount.signum() <= 0 || amount.compareTo(new BigDecimal("1000000")) > 0)
      throw new IllegalArgumentException("Se requiere un monto positivo hasta 1000000");
    try {
      amount = amount.setScale(2, RoundingMode.UNNECESSARY);
    } catch (ArithmeticException exception) {
      throw new IllegalArgumentException("Se admiten como máximo dos decimales", exception);
    }
    if (currency == null || !Set.of("PEN", "USD").contains(currency))
      throw new IllegalArgumentException("Se admite PEN o USD");
  }
}
