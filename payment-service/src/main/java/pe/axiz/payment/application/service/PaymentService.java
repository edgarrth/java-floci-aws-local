package pe.axiz.payment.application.service;

import java.nio.charset.StandardCharsets;
import java.security.*;
import java.time.Clock;
import java.util.*;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.domain.*;

public final class PaymentService implements ProcessPaymentUseCase {
  private final PaymentStore store;
  private final MerchantPolicyPort policies;
  private final Clock clock;

  public PaymentService(PaymentStore store, MerchantPolicyPort policies, Clock clock) {
    this.store = store;
    this.policies = policies;
    this.clock = clock;
  }

  @Override
  public Payment process(Command command) {
    if (command.merchantId() == null || !command.merchantId().matches("[a-zA-Z0-9-]{1,40}"))
      throw new IllegalArgumentException("Se requiere un comercio válido");
    if (command.key() == null || !command.key().matches("[a-zA-Z0-9_-]{8,80}"))
      throw new IllegalArgumentException("Se requiere Idempotency-Key entre 8 y 80 caracteres");
    if (command.token() == null || !command.token().matches("tok_[a-zA-Z0-9_-]{1,80}"))
      throw new IllegalArgumentException("Se requiere un token de pago; no se admite PAN");
    Objects.requireNonNull(command.money(), "Se requiere el monto del pago");
    String fingerprint =
        hash(
            command.merchantId()
                + "|"
                + command.money().amount().toPlainString()
                + "|"
                + command.money().currency()
                + "|"
                + command.token());
    var existing = store.findByKey(command.merchantId(), command.key());
    if (existing.isPresent()) return replay(existing.get(), fingerprint);
    boolean accepted =
        command.money().amount().compareTo(policies.limit(command.merchantId())) <= 0;
    var payment =
        new Payment(
            UUID.randomUUID(),
            command.merchantId(),
            command.money(),
            command.token(),
            command.key(),
            fingerprint,
            accepted ? Payment.Status.ACCEPTED : Payment.Status.REJECTED,
            accepted ? "POLICY_ACCEPTED" : "MERCHANT_LIMIT_EXCEEDED",
            clock.instant());
    return replay(store.record(payment), fingerprint);
  }

  @Override
  public Payment get(UUID id) {
    return store.find(id).orElseThrow(() -> new NoSuchElementException("No se encontró el pago"));
  }

  private Payment replay(Payment payment, String fingerprint) {
    if (!payment.fingerprint().equals(fingerprint))
      throw new IllegalStateException("La clave de idempotencia ya se utilizó con otra solicitud");
    return payment;
  }

  private String hash(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("No se encontró SHA-256", exception);
    }
  }
}
