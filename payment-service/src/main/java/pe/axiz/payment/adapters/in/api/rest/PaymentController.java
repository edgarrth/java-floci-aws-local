package pe.axiz.payment.adapters.in.api.rest;

import io.github.resilience4j.ratelimiter.RequestNotPermitted;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.math.BigDecimal;
import java.util.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase;
import pe.axiz.payment.configuration.ResiliencePolicies;
import pe.axiz.payment.domain.*;

@RestController
public class PaymentController {
  private final ProcessPaymentUseCase service;
  private final ResiliencePolicies policies;

  public PaymentController(ProcessPaymentUseCase service, ResiliencePolicies policies) {
    this.service = service;
    this.policies = policies;
  }

  public record Request(
      @NotBlank String merchantId,
      @NotNull @DecimalMin("0.01") @Digits(integer = 7, fraction = 2) BigDecimal amount,
      @Pattern(regexp = "PEN|USD") @NotNull String currency,
      @Pattern(regexp = "tok_[a-zA-Z0-9_-]{1,80}") @NotNull String token) {}

  public record Response(
      UUID id,
      String merchantId,
      BigDecimal amount,
      String currency,
      Payment.Status status,
      String authorizationCode) {
    static Response of(Payment p) {
      return new Response(
          p.id(),
          p.merchantId(),
          p.money().amount(),
          p.money().currency(),
          p.status(),
          p.authorizationCode());
    }
  }

  @PostMapping("/api/payments")
  ResponseEntity<Response> pay(
      @RequestHeader("Idempotency-Key") String key, @Valid @RequestBody Request request) {
    var rate = policies.rates.rateLimiter("ingress");
    if (!rate.acquirePermission()) throw RequestNotPermitted.createRequestNotPermitted(rate);
    var p =
        service.process(
            new ProcessPaymentUseCase.Command(
                request.merchantId(),
                new Money(request.amount(), request.currency()),
                request.token(),
                key));
    return ResponseEntity.status(p.terminal() ? 200 : 202).body(Response.of(p));
  }

  @GetMapping("/api/payments/{id}")
  Response get(@PathVariable UUID id) {
    return Response.of(service.get(id));
  }

  @PostMapping("/api/payments/{id}/reconcile")
  Response reconcile(@PathVariable UUID id) {
    return Response.of(service.reconcile(id));
  }

  @GetMapping("/internal/resilience")
  Map<String, Object> resilience() {
    var cb = policies.circuits.circuitBreaker("acquirer");
    return Map.of(
        "state",
        cb.getState(),
        "failedCalls",
        cb.getMetrics().getNumberOfFailedCalls(),
        "bufferedCalls",
        cb.getMetrics().getNumberOfBufferedCalls(),
        "events",
        policies.events());
  }

  @PostMapping("/internal/resilience/reset")
  Map<String, String> reset() {
    policies.circuits.circuitBreaker("acquirer").reset();
    return Map.of("state", "CLOSED");
  }
}
