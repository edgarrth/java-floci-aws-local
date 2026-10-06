package pe.axiz.payment;

import static org.assertj.core.api.Assertions.*;

import io.github.resilience4j.circuitbreaker.*;
import io.github.resilience4j.ratelimiter.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import org.springframework.mock.env.MockEnvironment;
import pe.axiz.payment.adapters.out.http.TransientAcquirerException;
import pe.axiz.payment.configuration.ResiliencePolicies;

class ResiliencePoliciesTest {
  private ResiliencePolicies policy;

  @BeforeEach
  void init() {
    policy =
        new ResiliencePolicies(
            new MockEnvironment()
                .withProperty("axiz.resilience.open-seconds", "1")
                .withProperty("axiz.resilience.max-attempts", "3")
                .withProperty("axiz.resilience.max-concurrent", "1")
                .withProperty("axiz.resilience.timeout-ms", "100")
                .withProperty("axiz.resilience.ingress-per-second", "1")
                .withProperty("axiz.resilience.outbound-per-second", "100"),
            new SimpleMeterRegistry());
  }

  @AfterEach
  void close() throws Exception {
    policy.close();
  }

  @Test
  void succeedsAfterTransientFailures() {
    var n = new AtomicInteger();
    assertThat(
            policy.execute(
                () -> {
                  if (n.incrementAndGet() < 3)
                    throw new TransientAcquirerException("Fallo transitorio");
                  return "ok";
                }))
        .isEqualTo("ok");
    assertThat(n.get()).isEqualTo(3);
  }

  @Test
  void doesNotRetryBusinessFailure() {
    var n = new AtomicInteger();
    assertThatThrownBy(
            () ->
                policy.execute(
                    () -> {
                      n.incrementAndGet();
                      throw new IllegalArgumentException("Regla comercial");
                    }))
        .isInstanceOf(IllegalArgumentException.class);
    assertThat(n.get()).isEqualTo(1);
  }

  @Test
  void circuitOpensAndRejectsCalls() {
    var cb = policy.circuits.circuitBreaker("acquirer");
    for (int i = 0; i < 4; i++)
      cb.onError(1, TimeUnit.MILLISECONDS, new TransientAcquirerException("Fallo"));
    assertThat(cb.getState()).isEqualTo(CircuitBreaker.State.OPEN);
    assertThatThrownBy(() -> policy.execute(() -> "ok"))
        .isInstanceOf(CallNotPermittedException.class);
  }

  @Test
  void halfOpenRecovers() {
    var cb = policy.circuits.circuitBreaker("acquirer");
    cb.transitionToOpenState();
    cb.transitionToHalfOpenState();
    policy.execute(() -> "ok");
    policy.execute(() -> "ok");
    assertThat(cb.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
  }

  @Test
  void timeoutInterruptsWorker() {
    var interrupted = new AtomicInteger();
    assertThatThrownBy(
            () ->
                policy.execute(
                    () -> {
                      try {
                        Thread.sleep(1000);
                      } catch (InterruptedException e) {
                        interrupted.incrementAndGet();
                        Thread.currentThread().interrupt();
                        throw new TransientAcquirerException(e);
                      }
                      return "ok";
                    }))
        .isInstanceOf(TransientAcquirerException.class);
    assertThat(interrupted.get()).isPositive();
  }

  @Test
  void rateLimiterRejects() {
    var rate = policy.rates.rateLimiter("ingress");
    assertThat(rate.acquirePermission()).isTrue();
    assertThat(rate.acquirePermission()).isFalse();
  }

  @Test
  void bulkheadRejectsConcurrentCalls() {
    var bulk = policy.bulkheads.bulkhead("acquirer");
    assertThat(bulk.tryAcquirePermission()).isTrue();
    assertThat(bulk.tryAcquirePermission()).isFalse();
    bulk.onComplete();
    assertThat(bulk.tryAcquirePermission()).isTrue();
    bulk.onComplete();
  }

  @Test
  void eventBufferIsBounded() {
    var cb = policy.circuits.circuitBreaker("acquirer");
    for (int i = 0; i < 150; i++) cb.onSuccess(1, TimeUnit.MILLISECONDS);
    assertThat(policy.events()).hasSize(100);
  }

  @Test
  void exhaustedRetryIsBounded() {
    var n = new AtomicInteger();
    assertThatThrownBy(
            () ->
                policy.execute(
                    () -> {
                      n.incrementAndGet();
                      throw new TransientAcquirerException("Fallo");
                    }))
        .isInstanceOf(TransientAcquirerException.class);
    assertThat(n.get()).isEqualTo(3);
  }
}
