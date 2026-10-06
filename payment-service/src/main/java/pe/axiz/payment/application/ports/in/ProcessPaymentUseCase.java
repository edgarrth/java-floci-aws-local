package pe.axiz.payment.application.ports.in;

import java.util.UUID;
import pe.axiz.payment.domain.*;

public interface ProcessPaymentUseCase {
  record Command(String merchantId, Money money, String token, String key) {}

  Payment process(Command command);

  Payment get(UUID id);
}
