package pe.axiz.acquirer.adapters.in.api.rest;

import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import pe.axiz.acquirer.application.service.AuthorizationService;
import pe.axiz.acquirer.domain.Authorization;

@RestController
public class AcquirerController {
  public enum Mode {
    SUCCESS,
    TRANSIENT,
    DECLINE,
    SLOW,
    TIMEOUT_AFTER_COMMIT
  }

  public record Fault(Mode mode, int failures, long delayMs) {
    public Fault {
      if (mode == null || failures < 0 || failures > 1000 || delayMs < 0 || delayMs > 10000)
        throw new IllegalArgumentException("La configuración de fallos no es válida");
    }
  }

  private volatile Fault fault = new Fault(Mode.SUCCESS, 0, 0);
  private final AtomicInteger remaining = new AtomicInteger();
  private final AtomicInteger calls = new AtomicInteger();
  private final AuthorizationService service;

  public AcquirerController(AuthorizationService service) {
    this.service = service;
  }

  @PutMapping("/internal/faults")
  Map<String, Object> faults(@RequestBody Fault f) {
    fault = f;
    remaining.set(f.failures());
    calls.set(0);
    return Map.of("mode", f.mode(), "remaining", remaining.get());
  }

  @GetMapping("/internal/faults")
  Map<String, Object> faults() {
    return Map.of("mode", fault.mode(), "remaining", remaining.get(), "calls", calls.get());
  }

  @PostMapping("/internal/authorizations")
  ResponseEntity<?> authorize(@RequestBody Authorization request) throws InterruptedException {
    calls.incrementAndGet();
    var f = fault;
    if (f.mode() == Mode.TRANSIENT && remaining.getAndUpdate(x -> Math.max(0, x - 1)) > 0)
      return ResponseEntity.status(503)
          .body(Map.of("error", "El adquirente no está disponible temporalmente"));
    if (f.mode() == Mode.SLOW) Thread.sleep(f.delayMs());
    var result = service.authorize(request, f.mode() == Mode.DECLINE);
    if (f.mode() == Mode.TIMEOUT_AFTER_COMMIT) Thread.sleep(f.delayMs());
    return ResponseEntity.ok(result);
  }

  @GetMapping("/internal/authorizations/{id}")
  Object get(@PathVariable UUID id) {
    return service.get(id);
  }
}
