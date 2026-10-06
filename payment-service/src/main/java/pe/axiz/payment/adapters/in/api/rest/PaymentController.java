package pe.axiz.payment.adapters.in.api.rest;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.math.BigDecimal;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase;
import pe.axiz.payment.domain.*;

@RestController
public class PaymentController {
  private final ProcessPaymentUseCase service;

  public PaymentController(ProcessPaymentUseCase service) {
    this.service = service;
  }

  public record Request(
      @NotBlank String merchantId,
      @NotNull @DecimalMin("0.01") @Digits(integer = 7, fraction = 2) BigDecimal amount,
      @NotNull @Pattern(regexp = "PEN|USD") String currency,
      @NotNull @Pattern(regexp = "tok_[a-zA-Z0-9_-]{1,80}") String token) {}

  public record Response(
      UUID id,
      String merchantId,
      BigDecimal amount,
      String currency,
      Payment.Status status,
      String reason) {
    static Response of(Payment payment) {
      return new Response(
          payment.id(),
          payment.merchantId(),
          payment.money().amount(),
          payment.money().currency(),
          payment.status(),
          payment.reason());
    }
  }

  @PostMapping("/api/payments")
  Response pay(@RequestHeader("Idempotency-Key") String key, @Valid @RequestBody Request request) {
    return Response.of(
        service.process(
            new ProcessPaymentUseCase.Command(
                request.merchantId(),
                new Money(request.amount(), request.currency()),
                request.token(),
                key)));
  }

  @GetMapping("/api/payments/{id}")
  Response get(@PathVariable UUID id) {
    return Response.of(service.get(id));
  }
}
