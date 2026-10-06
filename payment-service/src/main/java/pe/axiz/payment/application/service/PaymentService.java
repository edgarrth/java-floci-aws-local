package pe.axiz.payment.application.service;

import java.nio.charset.StandardCharsets;
import java.security.*;
import java.time.*;
import java.util.*;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.domain.*;

public final class PaymentService implements ProcessPaymentUseCase {
  private final PaymentStore store;
  private final AuthorizationPort authorizer;
  private final MerchantPolicyPort policies;
  private final Clock clock;

  public PaymentService(
      PaymentStore store, AuthorizationPort authorizer, MerchantPolicyPort policies, Clock clock) {
    this.store = store;
    this.authorizer = authorizer;
    this.policies = policies;
    this.clock = clock;
  }

  @Override
  public Payment process(Command c) {
    if (c.merchantId() == null || !c.merchantId().matches("[a-zA-Z0-9-]{1,40}"))
      throw new IllegalArgumentException("Se requiere un comercio válido");
    if (c.key() == null || !c.key().matches("[a-zA-Z0-9_-]{8,80}"))
      throw new IllegalArgumentException("Se requiere Idempotency-Key entre 8 y 80 caracteres");
    if (c.token() == null || !c.token().matches("tok_[a-zA-Z0-9_-]{1,80}"))
      throw new IllegalArgumentException("Se requiere un token de pago; no se admite PAN");
    if (c.money().amount().compareTo(policies.limit(c.merchantId())) > 0)
      throw new IllegalArgumentException("El monto excede la política del comercio");
    String fingerprint =
        hash(
            c.merchantId()
                + "|"
                + c.money().amount().toPlainString()
                + "|"
                + c.money().currency()
                + "|"
                + c.token());
    var initial =
        new Payment(
            UUID.randomUUID(),
            c.merchantId(),
            c.money(),
            c.token(),
            c.key(),
            fingerprint,
            Payment.Status.PENDING,
            null,
            clock.instant());
    var reservation = store.reserve(initial);
    if (!reservation.payment().fingerprint().equals(fingerprint))
      throw new IllegalStateException("La clave de idempotencia ya se utilizó con otra solicitud");
    if (!reservation.created()) return reservation.payment();
    var p = reservation.payment();
    // Un fallo de transporte no equivale a un rechazo ni a una autorización financiera.
    AuthorizationPort.Decision d;
    try {
      d = authorizer.authorize(p);
    } catch (RuntimeException e) {
      d = new AuthorizationPort.Decision(Payment.Status.UNKNOWN, null);
    }
    return store.complete(p.complete(d.status(), d.code()));
  }

  @Override
  public Payment get(UUID id) {
    return store.find(id).orElseThrow(() -> new NoSuchElementException("No se encontró el pago"));
  }

  @Override
  public Payment reconcile(UUID id) {
    var p = get(id);
    if (p.terminal()) return p;
    var d = authorizer.query(p);
    return store.complete(p.complete(d.status(), d.code()));
  }

  private String hash(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }
}
