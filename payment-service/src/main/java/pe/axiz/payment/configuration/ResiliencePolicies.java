package pe.axiz.payment.configuration;

import io.github.resilience4j.bulkhead.*;
import io.github.resilience4j.circuitbreaker.*;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.micrometer.tagged.*;
import io.github.resilience4j.ratelimiter.*;
import io.github.resilience4j.retry.*;
import io.github.resilience4j.timelimiter.*;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Supplier;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import pe.axiz.payment.adapters.out.http.TransientAcquirerException;

@Component
public class ResiliencePolicies implements AutoCloseable {
  public final CircuitBreakerRegistry circuits;
  public final RetryRegistry retries;
  public final BulkheadRegistry bulkheads;
  public final TimeLimiterRegistry timers;
  public final RateLimiterRegistry rates;
  public final ThreadPoolBulkhead publisher;
  private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
  private final Deque<Map<String, String>> events = new ArrayDeque<>();

  public ResiliencePolicies(Environment e, MeterRegistry meter) {
    circuits =
        CircuitBreakerRegistry.of(
            CircuitBreakerConfig.custom()
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(10)
                .minimumNumberOfCalls(4)
                .failureRateThreshold(50)
                .slowCallRateThreshold(60)
                .slowCallDurationThreshold(Duration.ofMillis(600))
                .waitDurationInOpenState(
                    Duration.ofSeconds(
                        e.getRequiredProperty("axiz.resilience.open-seconds", Integer.class)))
                .permittedNumberOfCallsInHalfOpenState(2)
                .automaticTransitionFromOpenToHalfOpenEnabled(true)
                .recordException(
                    t -> t instanceof TransientAcquirerException || t instanceof TimeoutException)
                .ignoreExceptions(BulkheadFullException.class, RequestNotPermitted.class)
                .build());
    retries =
        RetryRegistry.of(
            RetryConfig.custom()
                .maxAttempts(e.getRequiredProperty("axiz.resilience.max-attempts", Integer.class))
                .intervalFunction(IntervalFunction.ofExponentialRandomBackoff(100, 2, 0.5))
                .retryOnException(
                    t -> t instanceof TransientAcquirerException || t instanceof TimeoutException)
                .build());
    bulkheads =
        BulkheadRegistry.of(
            BulkheadConfig.custom()
                .maxConcurrentCalls(
                    e.getRequiredProperty("axiz.resilience.max-concurrent", Integer.class))
                .maxWaitDuration(Duration.ZERO)
                .build());
    timers =
        TimeLimiterRegistry.of(
            TimeLimiterConfig.custom()
                .timeoutDuration(
                    Duration.ofMillis(
                        e.getRequiredProperty("axiz.resilience.timeout-ms", Integer.class)))
                .cancelRunningFuture(true)
                .build());
    rates = RateLimiterRegistry.ofDefaults();
    rates.rateLimiter(
        "ingress",
        RateLimiterConfig.custom()
            .limitForPeriod(
                e.getRequiredProperty("axiz.resilience.ingress-per-second", Integer.class))
            .limitRefreshPeriod(Duration.ofSeconds(1))
            .timeoutDuration(Duration.ZERO)
            .build());
    rates.rateLimiter(
        "acquirer",
        RateLimiterConfig.custom()
            .limitForPeriod(
                e.getRequiredProperty("axiz.resilience.outbound-per-second", Integer.class))
            .limitRefreshPeriod(Duration.ofSeconds(1))
            .timeoutDuration(Duration.ZERO)
            .build());
    var publisherRegistry =
        ThreadPoolBulkheadRegistry.of(
            ThreadPoolBulkheadConfig.custom()
                .coreThreadPoolSize(2)
                .maxThreadPoolSize(2)
                .queueCapacity(8)
                .build());
    publisher = publisherRegistry.bulkhead("publisher");
    TaggedThreadPoolBulkheadMetrics.ofThreadPoolBulkheadRegistry(publisherRegistry).bindTo(meter);
    var cb = circuits.circuitBreaker("acquirer");
    cb.getEventPublisher().onEvent(ev -> event("circuit", ev.getEventType().name()));
    retries
        .retry("acquirer")
        .getEventPublisher()
        .onEvent(ev -> event("retry", ev.getEventType().name()));
    bulkheads
        .bulkhead("acquirer")
        .getEventPublisher()
        .onEvent(ev -> event("bulkhead", ev.getEventType().name()));
    timers
        .timeLimiter("acquirer")
        .getEventPublisher()
        .onEvent(ev -> event("timeLimiter", ev.getEventType().name()));
    rates
        .getAllRateLimiters()
        .forEach(
            r ->
                r.getEventPublisher()
                    .onEvent(ev -> event("rateLimiter", ev.getEventType().name())));
    TaggedCircuitBreakerMetrics.ofCircuitBreakerRegistry(circuits).bindTo(meter);
    TaggedRetryMetrics.ofRetryRegistry(retries).bindTo(meter);
    TaggedBulkheadMetrics.ofBulkheadRegistry(bulkheads).bindTo(meter);
    TaggedTimeLimiterMetrics.ofTimeLimiterRegistry(timers).bindTo(meter);
    TaggedRateLimiterMetrics.ofRateLimiterRegistry(rates).bindTo(meter);
  }

  private synchronized void event(String policy, String type) {
    if (events.size() == 100) events.removeFirst();
    events.addLast(
        Map.of("policy", policy, "type", type, "time", java.time.Instant.now().toString()));
  }

  public synchronized List<Map<String, String>> events() {
    return List.copyOf(events);
  }

  public <T> T execute(Supplier<T> work) {
    Callable<T> bounded =
        () -> {
          var rate = rates.rateLimiter("acquirer");
          if (!rate.acquirePermission()) throw RequestNotPermitted.createRequestNotPermitted(rate);
          var bulk = bulkheads.bulkhead("acquirer");
          return timers
              .timeLimiter("acquirer")
              .executeFutureSupplier(() -> executor.submit(() -> bulk.executeSupplier(work)));
        };
    var circuit = CircuitBreaker.decorateCallable(circuits.circuitBreaker("acquirer"), bounded);
    try {
      return Retry.decorateCallable(retries.retry("acquirer"), circuit).call();
    } catch (RuntimeException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new TransientAcquirerException(ex);
    }
  }

  @Override
  public void close() throws Exception {
    executor.shutdownNow();
    publisher.close();
  }
}
