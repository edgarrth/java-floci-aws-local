package pe.axiz.payment.adapters.in.api.rest;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

@RestController
public class OutboxController {
  private final JdbcTemplate jdbc;

  public OutboxController(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  @GetMapping("/internal/outbox")
  Object get() {
    return jdbc.queryForList(
        "SELECT destination,COUNT(*) AS total,COUNT(*) FILTER (WHERE published_at IS NULL) AS pending FROM outbox GROUP BY destination ORDER BY destination");
  }
}
