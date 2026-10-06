package pe.axiz.payment.application.ports.out;

import java.util.Optional;
import java.util.UUID;
import pe.axiz.payment.domain.Payment;

public interface PaymentStore {
  Payment record(Payment payment);

  Optional<Payment> find(UUID id);

  Optional<Payment> findByKey(String merchantId, String key);
}
