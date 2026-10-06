package pe.axiz.platform.web;

import java.util.NoSuchElementException;
import org.springframework.dao.DataAccessException;
import org.springframework.http.*;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.*;
import software.amazon.awssdk.core.exception.SdkException;

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

  @ExceptionHandler({SdkException.class, DataAccessException.class})
  ResponseEntity<ProblemDetail> unavailable(RuntimeException exception) {
    return problem(503, "No se pudo completar la operación con la infraestructura");
  }

  private ResponseEntity<ProblemDetail> problem(int code, String detail) {
    return ResponseEntity.status(code)
        .body(ProblemDetail.forStatusAndDetail(HttpStatusCode.valueOf(code), detail));
  }
}
