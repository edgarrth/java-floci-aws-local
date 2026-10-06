package pe.axiz.payment;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.time.Clock;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase.Command;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.application.service.PaymentService;
import pe.axiz.payment.domain.*;

class PaymentServiceTest {
  static class Store implements PaymentStore {
    final Map<UUID, Payment> payments = new HashMap<>();
    final Map<String, UUID> keys = new HashMap<>();

    public synchronized Reservation reserve(Payment p) {
      String k = p.merchantId() + p.idempotencyKey();
      if (keys.containsKey(k)) return new Reservation(payments.get(keys.get(k)), false);
      keys.put(k, p.id());
      payments.put(p.id(), p);
      return new Reservation(p, true);
    }

    public synchronized Payment complete(Payment p) {
      payments.put(p.id(), p);
      return p;
    }

    public synchronized Optional<Payment> find(UUID id) {
      return Optional.ofNullable(payments.get(id));
    }

    public List<UUID> unresolved() {
      return List.of();
    }
  }

  private AuthorizationPort authorizer;
  private PaymentService service;
  private Store store;

  Command command(String key) {
    return new Command("merchant-demo", new Money(new BigDecimal("25.00"), "PEN"), "tok_demo", key);
  }

  @BeforeEach
  void init() {
    authorizer = mock(AuthorizationPort.class);
    store = new Store();
    service = new PaymentService(store, authorizer, m -> new BigDecimal("5000"), Clock.systemUTC());
    when(authorizer.authorize(any()))
        .thenReturn(new AuthorizationPort.Decision(Payment.Status.AUTHORIZED, "AX123"));
  }

  @Test
  void authorization() {
    var p = service.process(command("key-00001"));
    assertThat(p.status()).isEqualTo(Payment.Status.AUTHORIZED);
  }

  @Test
  void repeatUsesStoredResult() {
    var a = service.process(command("key-00002"));
    var b = service.process(command("key-00002"));
    assertThat(b.id()).isEqualTo(a.id());
    verify(authorizer, times(1)).authorize(any());
  }

  @Test
  void conflictingBody() {
    service.process(command("key-00003"));
    assertThatThrownBy(
            () ->
                service.process(
                    new Command(
                        "merchant-demo",
                        new Money(new BigDecimal("26"), "PEN"),
                        "tok_demo",
                        "key-00003")))
        .isInstanceOf(IllegalStateException.class);
  }

  @Test
  void transportFailureIsUnknown() {
    when(authorizer.authorize(any()))
        .thenThrow(new IllegalStateException("Se perdió la respuesta"));
    var p = service.process(command("key-00004"));
    assertThat(p.status()).isEqualTo(Payment.Status.UNKNOWN);
  }

  @Test
  void reconciliationUsesQuery() {
    when(authorizer.authorize(any())).thenThrow(new IllegalStateException());
    var p = service.process(command("key-00005"));
    when(authorizer.query(any()))
        .thenReturn(new AuthorizationPort.Decision(Payment.Status.AUTHORIZED, "AX1"));
    assertThat(service.reconcile(p.id()).status()).isEqualTo(Payment.Status.AUTHORIZED);
    verify(authorizer, times(1)).authorize(any());
    verify(authorizer, times(1)).query(any());
  }

  @Test
  void finalDoesNotReauthorize() {
    var p = service.process(command("key-00006"));
    service.reconcile(p.id());
    verify(authorizer, never()).query(any());
  }

  @Test
  void rejectsPan() {
    assertThatThrownBy(
            () ->
                service.process(
                    new Command(
                        "merchant-demo",
                        command("key-00007").money(),
                        "4111111111111111",
                        "key-00007")))
        .isInstanceOf(IllegalArgumentException.class);
    verifyNoInteractions(authorizer);
  }

  @Test
  void rejectsOverLimit() {
    assertThatThrownBy(
            () ->
                service.process(
                    new Command(
                        "merchant-demo",
                        new Money(new BigDecimal("5001"), "PEN"),
                        "tok_demo",
                        "key-00008")))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void declineIsFinal() {
    when(authorizer.authorize(any()))
        .thenReturn(new AuthorizationPort.Decision(Payment.Status.DECLINED, "DECLINED"));
    assertThat(service.process(command("key-00009")).terminal()).isTrue();
  }

  @Test
  void concurrentReplayCallsAcquirerOnce() throws Exception {
    try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
      var tasks = new ArrayList<Future<Payment>>();
      for (int i = 0; i < 20; i++)
        tasks.add(executor.submit(() -> service.process(command("key-00010"))));
      var ids = new HashSet<UUID>();
      for (var t : tasks) ids.add(t.get().id());
      assertThat(ids).hasSize(1);
      verify(authorizer, times(1)).authorize(any());
    }
  }

  @Test
  void moneyNormalizes() {
    assertThat(new Money(new BigDecimal("2"), "USD").amount()).isEqualByComparingTo("2.00");
  }

  @Test
  void moneyRejectsFraction() {
    assertThatThrownBy(() -> new Money(new BigDecimal("2.001"), "USD"))
        .isInstanceOf(ArithmeticException.class);
  }

  @Test
  void moneyRejectsNegative() {
    assertThatThrownBy(() -> new Money(new BigDecimal("-1"), "PEN"))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void moneyRejectsCurrency() {
    assertThatThrownBy(() -> new Money(BigDecimal.ONE, "EUR"))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void terminalCannotChange() {
    var p = service.process(command("key-00011"));
    assertThatThrownBy(() -> p.complete(Payment.Status.DECLINED, null))
        .isInstanceOf(IllegalStateException.class);
  }
}
