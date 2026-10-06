package pe.axiz.payment;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.time.*;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase.Command;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.application.service.PaymentService;
import pe.axiz.payment.domain.*;

class PaymentServiceTest {
  private final PaymentStore store = mock(PaymentStore.class);
  private final MerchantPolicyPort policies = mock(MerchantPolicyPort.class);
  private final Clock clock = Clock.fixed(Instant.parse("2026-10-06T00:00:00Z"), ZoneOffset.UTC);
  private final PaymentService service = new PaymentService(store, policies, clock);

  @BeforeEach
  void defaults() {
    when(policies.limit("merchant-demo")).thenReturn(new BigDecimal("5000.00"));
    when(store.findByKey(anyString(), anyString())).thenReturn(Optional.empty());
    when(store.record(any())).thenAnswer(invocation -> invocation.getArgument(0));
  }

  Command command(String key, String amount) {
    return new Command("merchant-demo", new Money(new BigDecimal(amount), "PEN"), "tok_demo", key);
  }

  @Test
  void acceptsPolicyCompliantPaymentWithStableTimestamp() {
    var payment = service.process(command("test-key-001", "25.50"));
    assertThat(payment.status()).isEqualTo(Payment.Status.ACCEPTED);
    assertThat(payment.reason()).isEqualTo("POLICY_ACCEPTED");
    assertThat(payment.createdAt()).isEqualTo(clock.instant());
    assertThat(payment.fingerprint()).hasSize(64);
    verify(store).record(payment);
  }

  @Test
  void persistsRejectionAboveMerchantLimit() {
    var payment = service.process(command("test-key-002", "5000.01"));
    assertThat(payment.status()).isEqualTo(Payment.Status.REJECTED);
    assertThat(payment.reason()).isEqualTo("MERCHANT_LIMIT_EXCEEDED");
    verify(store).record(payment);
  }

  @Test
  void acceptsExactLimit() {
    assertThat(service.process(command("test-key-003", "5000.00")).status())
        .isEqualTo(Payment.Status.ACCEPTED);
  }

  @Test
  void replayDoesNotRequireConfigurationOrCreateEvents() {
    var original = service.process(command("test-key-004", "25.50"));
    clearInvocations(store, policies);
    when(store.findByKey("merchant-demo", "test-key-004")).thenReturn(Optional.of(original));
    assertThat(service.process(command("test-key-004", "25.5"))).isEqualTo(original);
    verifyNoInteractions(policies);
    verify(store, never()).record(any());
  }

  @Test
  void rejectsChangedPayloadForExistingKey() {
    var original = service.process(command("test-key-005", "25.50"));
    when(store.findByKey("merchant-demo", "test-key-005")).thenReturn(Optional.of(original));
    assertThatThrownBy(() -> service.process(command("test-key-005", "25.51")))
        .isInstanceOf(IllegalStateException.class);
  }

  @Test
  void resolvesConcurrentReservationToExistingPayment() {
    var first = service.process(command("test-key-006", "25.50"));
    when(store.record(any())).thenReturn(first);
    assertThat(service.process(command("test-key-006", "25.50"))).isEqualTo(first);
  }

  @Test
  void detectsConcurrentKeyConflict() {
    var first = service.process(command("test-key-007", "25.50"));
    when(store.record(any())).thenReturn(first);
    assertThatThrownBy(() -> service.process(command("test-key-007", "26.00")))
        .isInstanceOf(IllegalStateException.class);
  }

  @Test
  void readsRecordedPayment() {
    var payment = service.process(command("test-key-008", "1.00"));
    when(store.find(payment.id())).thenReturn(Optional.of(payment));
    assertThat(service.get(payment.id())).isEqualTo(payment);
  }

  @Test
  void rejectsMissingPayment() {
    assertThatThrownBy(() -> service.get(UUID.randomUUID()))
        .isInstanceOf(NoSuchElementException.class);
  }

  @Test
  void refusesCardNumberAsToken() {
    assertThatThrownBy(
            () ->
                service.process(
                    new Command(
                        "merchant-demo",
                        new Money(BigDecimal.ONE, "PEN"),
                        "4111111111111111",
                        "test-key-009")))
        .isInstanceOf(IllegalArgumentException.class);
    verifyNoInteractions(store, policies);
  }

  @Test
  void validatesMerchantIdentifierBeforeInfrastructure() {
    assertThatThrownBy(
            () ->
                service.process(
                    new Command(
                        "../merchant",
                        new Money(BigDecimal.ONE, "PEN"),
                        "tok_demo",
                        "test-key-010")))
        .isInstanceOf(IllegalArgumentException.class);
    verifyNoInteractions(store, policies);
  }

  @Test
  void validatesIdempotencyKey() {
    assertThatThrownBy(() -> service.process(command("short", "1.00")))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void validatesPositiveAmount() {
    assertThatThrownBy(() -> new Money(BigDecimal.ZERO, "PEN"))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void validatesSupportedCurrency() {
    assertThatThrownBy(() -> new Money(BigDecimal.ONE, "EUR"))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void validatesExactDecimalPrecision() {
    assertThatThrownBy(() -> new Money(new BigDecimal("1.001"), "PEN"))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void doesNotRecordWhenParameterStoreFails() {
    when(policies.limit("merchant-demo"))
        .thenThrow(new IllegalArgumentException("No se encontró el comercio"));
    assertThatThrownBy(() -> service.process(command("test-key-011", "1.00")))
        .isInstanceOf(IllegalArgumentException.class);
    verify(store, never()).record(any());
  }
}
