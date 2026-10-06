package pe.axiz.platform.web;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.ratelimiter.RequestNotPermitted;
import java.util.NoSuchElementException;
import org.springframework.http.*;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;

@RestControllerAdvice
public class ApiExceptionHandler {
  @ExceptionHandler(IllegalArgumentException.class)
  ResponseEntity<ProblemDetail> bad(IllegalArgumentException e) {
    return problem(400, e.getMessage());
  }

  @ExceptionHandler(IllegalStateException.class)
  ResponseEntity<ProblemDetail> conflict(IllegalStateException e) {
    return problem(409, e.getMessage());
  }

  @ExceptionHandler(NoSuchElementException.class)
  ResponseEntity<ProblemDetail> missing(NoSuchElementException e) {
    return problem(404, e.getMessage());
  }

  @ExceptionHandler(MethodArgumentNotValidException.class)
  ResponseEntity<ProblemDetail> invalid(MethodArgumentNotValidException e) {
    return problem(400, "La solicitud contiene campos inválidos");
  }

  @ExceptionHandler(RequestNotPermitted.class)
  ResponseEntity<ProblemDetail> rate(RequestNotPermitted e) {
    return ResponseEntity.status(429)
        .header("Retry-After", "1")
        .body(
            ProblemDetail.forStatusAndDetail(
                HttpStatus.TOO_MANY_REQUESTS, "Se excedió el límite de solicitudes"));
  }

  @ExceptionHandler(BulkheadFullException.class)
  ResponseEntity<ProblemDetail> busy(BulkheadFullException e) {
    return problem(503, "Se agotó la capacidad concurrente");
  }

  private ResponseEntity<ProblemDetail> problem(int code, String detail) {
    return ResponseEntity.status(code)
        .body(ProblemDetail.forStatusAndDetail(HttpStatusCode.valueOf(code), detail));
  }
}
