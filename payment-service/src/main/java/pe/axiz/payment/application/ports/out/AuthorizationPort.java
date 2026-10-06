package pe.axiz.payment.application.ports.out;

import pe.axiz.payment.domain.Payment;

public interface AuthorizationPort {
  record Decision(Payment.Status status, String code) {}

  Decision authorize(Payment payment);

  Decision query(Payment payment);
}
