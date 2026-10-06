package pe.axiz.payment.application.ports.out;

import java.util.*;
import pe.axiz.payment.domain.Payment;

public interface PaymentStore {
  record Reservation(Payment payment, boolean created) {}

  Reservation reserve(Payment payment);

  Payment complete(Payment payment);

  Optional<Payment> find(UUID id);

  List<UUID> unresolved();
}
